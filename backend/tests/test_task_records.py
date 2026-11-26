import json
import re
import tempfile
import unittest
from collections import Counter

from app.adapters.orchestration.deep_agent_runtime import _langchain_tool
from app.adapters.orchestration.supervisor import ReviewVerdict
from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.adapters.persistence.pg_repositories import PgRunRepository, PgTaskRepository
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget
from app.application.services.orchestration_service import InvestigationOrchestrator, _default_plan
from app.application.services.task_records import budget_slice, task_fingerprints
from app.application.services.task_store import InMemoryTaskStore
from app.bootstrap.container import build_container
from app.domain.errors import ValidationError, VersionConflictError
from app.domain.models import RunMode, TaskBudget, TaskRecord
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from tests.test_tool_loop import QUERY, _publish_orders

APPROVE = json.dumps({"approved": True, "gaps": [], "contradictions": [], "summary": "Supported."})


class Crash(BaseException):
    pass


class DeepGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self, crash_on: str | None = None, fail_on: str | None = None) -> None:
        self.crash_on = crash_on
        self.fail_on = fail_on
        self.calls: Counter[str] = Counter()

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        role = request.role.value
        self.calls[role] += 1
        if role == self.crash_on:
            raise Crash()
        if role == self.fail_on:
            raise RuntimeError("provider exploded")
        text_in = " ".join(str(m.get("content")) for m in request.messages)
        tool_msgs = [m for m in request.messages if m.get("role") == "tool"]
        names = {t.name for t in request.tools or ()}
        if role in ("data_specialist", "reconciliation_specialist") and "run_query_ir" in names and not tool_msgs:
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=(ToolCallResult("q1", "run_query_ir", QUERY),))
        if role == "evidence_reviewer":
            text = APPROVE
        else:
            found = re.findall(r"ev_[0-9a-f]{12}", text_in)
            text = f"North leads with 12.5 [{found[-1]}]." if found else "No data."
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=text, input_tokens=5, output_tokens=5)


class TestTaskStores(unittest.TestCase):
    def _record(self, **kw) -> TaskRecord:
        base = dict(run_id="run_s", task_id="t1", plan_revision=1, kind="data_specialist", objective="o", acceptance_criteria=("c",),
                    dependencies=(), status="pending", attempt=0, input_fingerprint="f" * 64, version=1)
        return TaskRecord(**{**base, **kw})

    def _check(self, store) -> None:
        store.add_tasks([self._record(), self._record(task_id="t2", dependencies=("t1",))])
        store.add_tasks([self._record(objective="ignored")])
        running = store.transition("run_s", "t1", expected_version=1, status="running", attempt=1, budget=TaskBudget(1000, 4))
        self.assertEqual((running.version, running.attempt, running.budget), (2, 1, TaskBudget(1000, 4)))
        with self.assertRaises(VersionConflictError):
            store.transition("run_s", "t1", expected_version=1, status="completed")
        done = store.transition("run_s", "t1", expected_version=2, status="completed", evidence_ids=("ev_1",), result={"text": "x"})
        self.assertEqual((done.status, done.evidence_ids, done.result), ("completed", ("ev_1",), {"text": "x"}))
        with self.assertRaises(ValidationError):
            store.transition("run_s", "t2", expected_version=1, status="completed")
        listed = {r.task_id: r for r in store.list_for_run("run_s")}
        self.assertEqual((listed["t1"].objective, listed["t1"].version, listed["t2"].dependencies), ("o", 3, ("t1",)))

    def test_in_memory_store(self) -> None:
        self._check(InMemoryTaskStore())

    def test_database_store(self) -> None:
        sf = create_session_factory(create_db_engine())
        PgRunRepository(sf).create_run(run_id="run_s", workspace_id="ws1", conversation_id="c", mode="deep", status="queued", question="q")
        self._check(PgTaskRepository(sf))


class TestFingerprintsAndSlices(unittest.TestCase):
    def test_changing_an_input_changes_only_its_dependents(self) -> None:
        plan = _default_plan("q", RunMode.DEEP, "snap", "sem")
        before = task_fingerprints(plan, "q")
        tasks = tuple(t if t.task_id != "t1_reconcile" else type(t)(**{**{f: getattr(t, f) for f in t.__slots__}, "objective": "other"}) for t in plan.tasks)
        after = task_fingerprints(type(plan)(plan.revision, plan.goal, plan.completion_criteria, tasks), "q")
        self.assertEqual(before["t1_analyze"], after["t1_analyze"])
        self.assertNotEqual(before["t1_reconcile"], after["t1_reconcile"])
        self.assertNotEqual(before["t2_review"], after["t2_review"])
        self.assertNotEqual(before["t1_analyze"], task_fingerprints(plan, "another question")["t1_analyze"])

    def test_budget_slice_shares_what_is_left(self) -> None:
        self.assertEqual(budget_slice(tokens_left=124_000, reserve_tokens=24_000, tools_left=64, unfinished=4, min_tokens=8000), TaskBudget(25_000, 16))
        self.assertEqual(budget_slice(tokens_left=10_000, reserve_tokens=24_000, tools_left=1, unfinished=3, min_tokens=8000), TaskBudget(8000, 1))

    def test_tool_wrapper_enforces_the_step_share(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            snapshot = _publish_orders(c)
            toolbox = AnalyticsToolbox(engine=InvestigationOrchestrator(c)._engine(), snapshot=snapshot, semantics=None, budget=ToolBudget(10, 10))
            trace: list = []
            run_query = next(d for d in toolbox.definitions if d.name == "run_query_ir")
            tool = _langchain_tool(run_query, toolbox, on_tool=None, should_cancel=None, trace=trace, evidence=[], max_calls=1)
            tool.func(**QUERY)
            second = json.loads(tool.func(**QUERY))
            self.assertEqual(second["status"], "denied")
            self.assertIn("share of 1 tool calls", json.dumps(second))
            self.assertEqual(toolbox.budget.used, 1)


class TestDurableTasks(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.c = build_container(artifact_root=self._td.name)
        self.c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
        self.snapshot = {**_publish_orders(self.c), "snapshot_id": "snap_tasks"}
        self.plans: list[dict] = []

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run(self, gateway: DeepGateway, run_id: str = "run_tasks", **kw):
        self.c.providers.register(gateway)
        events: list = []
        answer = InvestigationOrchestrator(self.c).execute_local(
            run_id=run_id, workspace_id="ws1", question="Which region sells most?", mode=RunMode.DEEP, snapshot_id="snap_tasks",
            snapshot=self.snapshot, emit_event=events.append, on_plan=self.plans.append,
            initial_plan=self.plans[-1] if self.plans else None, **kw,
        )
        return answer, events

    def _tasks(self, run_id: str = "run_tasks") -> dict[str, TaskRecord]:
        return {r.task_id: r for r in self.c.task_store.list_for_run(run_id)}

    def test_a_resumed_deep_run_reexecutes_only_unfinished_tasks(self) -> None:
        evidence: dict[str, dict] = {}
        lookup = evidence.get
        with self.assertRaises(Crash):
            self._run(DeepGateway(crash_on="evidence_reviewer"), on_evidence=lambda e: evidence.setdefault(e["evidence_id"], e))
        tasks = self._tasks()
        self.assertEqual({k: v.status for k, v in tasks.items()}, {"t1_analyze": "completed", "t1_reconcile": "completed", "t2_review": "running"})
        self.assertTrue(tasks["t1_analyze"].evidence_ids)
        self.assertEqual(tasks["t1_analyze"].budget.tool_calls, 64 // 3)
        executions_before = len(self.c.tool_executions.list_for_runs(["run_tasks"]))

        resumed = DeepGateway()
        answer, events = self._run(resumed, evidence_lookup=lookup)
        self.assertEqual(resumed.calls["data_specialist"], 0, resumed.calls)
        self.assertEqual(resumed.calls["reconciliation_specialist"], 0, resumed.calls)
        self.assertEqual(resumed.calls["evidence_reviewer"], 1, resumed.calls)
        self.assertEqual(len(self.c.tool_executions.list_for_runs(["run_tasks"])), executions_before)
        restored = [e.payload["task_id"] for e in events if e.type.value == "task.status" and e.payload.get("restored")]
        self.assertEqual(sorted(restored), ["t1_analyze", "t1_reconcile"])
        tasks = self._tasks()
        self.assertEqual({k: v.status for k, v in tasks.items()}, {"t1_analyze": "completed", "t1_reconcile": "completed", "t2_review": "completed"})
        self.assertEqual(tasks["t2_review"].attempt, 2)
        self.assertEqual(answer.outcome, "success", answer.criteria)
        self.assertIn(tasks["t1_analyze"].evidence_ids[0], {e for claim in answer.claims for e in claim.evidence_ids})

    def test_replanning_after_a_failed_task_keeps_verified_evidence(self) -> None:
        verdicts = iter([ReviewVerdict(approved=False, gaps=("Reconciliation is missing",)), ReviewVerdict(approved=True)])
        gateway = DeepGateway(fail_on="reconciliation_specialist")
        answer, events = self._run(gateway, replan_evaluator=lambda claims, n: next(verdicts))
        tasks = self._tasks()
        self.assertEqual(tasks["t1_reconcile"].status, "failed")
        self.assertEqual(tasks["t2_review"].status, "failed")
        self.assertEqual(tasks["t1_analyze"].status, "completed")
        self.assertEqual(tasks["t1_analyze"].version, 3)
        remediation = [t for t in tasks.values() if t.plan_revision == 2]
        self.assertEqual(sorted(t.kind for t in remediation), ["data_specialist", "evidence_reviewer"])
        self.assertTrue(all(t.status == "completed" for t in remediation), remediation)
        review = next(t for t in remediation if t.kind == "evidence_reviewer")
        self.assertIn("t1_analyze", review.dependencies)
        self.assertEqual(gateway.calls["data_specialist"], 4, gateway.calls)
        self.assertEqual(self.plans[-1]["revision"], 2)
        claimed = {e for claim in answer.claims for e in claim.evidence_ids}
        self.assertTrue(set(tasks["t1_analyze"].evidence_ids) <= claimed)
        self.assertTrue(all(t.acceptance_criteria for t in tasks.values()))


if __name__ == "__main__":
    unittest.main()
