import dataclasses
import tempfile
import unittest
from unittest.mock import patch

from app.adapters.orchestration import supervisor as supervisor_module
from app.adapters.orchestration.supervisor import DeterministicSupervisor, ReviewVerdict
from app.application.ports.providers import ModelRequest, ModelResponse
from app.application.services.orchestration_service import InvestigationOrchestrator, _default_plan
from app.bootstrap.container import build_container
from app.domain.limits import DEEP_BUDGET_LIMITS, QUICK_BUDGET_LIMITS, REPLAN_TASKS, RunBudgetLimits
from app.domain.models import Claim, PlanDAG, RunMode, TaskNode, TaskStatus
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile


class _TextGateway:
    provider_id = ProviderId.OPENAI

    def invoke(
        self,
        request: ModelRequest,
        provider_profile: ProviderProfile,
        model_profile: ModelProfile,
        api_key: str,
    ) -> ModelResponse:
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=f"Executed {request.role.value} step",
            input_tokens=100,
            output_tokens=20,
        )


def _always_reject(claims: list[Claim], replan_count: int) -> ReviewVerdict:
    return ReviewVerdict(approved=False, gaps=("Verify fee breakdown",))


def _task(task_id: str, deps: tuple[str, ...] = ()) -> TaskNode:
    return TaskNode(task_id, "data_specialist", "fetch", deps, "s1", "sem1", (), "schema")


class TestBudgetLimitInvariants(unittest.TestCase):
    def test_planner_capacity_reserves_every_replan(self) -> None:
        for limits in (QUICK_BUDGET_LIMITS, DEEP_BUDGET_LIMITS):
            reserved = REPLAN_TASKS * limits.max_replans
            self.assertGreaterEqual(limits.planner_task_limit, 1)
            self.assertLessEqual(
                limits.planner_task_limit + reserved,
                min(limits.accepted_dag_tasks, limits.lifetime_specialist_instances),
            )

    def test_default_plans_fit_planner_capacity(self) -> None:
        for mode, limits in ((RunMode.QUICK, QUICK_BUDGET_LIMITS), (RunMode.DEEP, DEEP_BUDGET_LIMITS)):
            plan = _default_plan("q", mode, "s1", "sem1")
            self.assertLessEqual(len(plan.tasks), limits.planner_task_limit)

    def test_limits_without_planner_capacity_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dataclasses.replace(DEEP_BUDGET_LIMITS, lifetime_specialist_instances=4)
        with self.assertRaises(ValueError):
            RunBudgetLimits("x", 1, 1, 1, 4, 1, 4, 1, 2, 1)


class TestSupervisorCapacity(unittest.TestCase):
    def test_can_accept_replan_tracks_capacity_and_replan_count(self) -> None:
        sup = DeterministicSupervisor(RunMode.DEEP, "run_cap")
        sup.accept_plan(PlanDAG(1, "g", ("done",), (_task("t1"),)))
        self.assertTrue(sup.can_accept_replan(REPLAN_TASKS))
        sup.state.specialist_instances_used = DEEP_BUDGET_LIMITS.lifetime_specialist_instances - 1
        self.assertEqual(sup.remaining_specialist_capacity(), 1)
        self.assertFalse(sup.can_accept_replan(REPLAN_TASKS))
        sup.state.specialist_instances_used = 0
        sup.state.replan_count = DEEP_BUDGET_LIMITS.max_replans
        self.assertFalse(sup.can_accept_replan(REPLAN_TASKS))

    def test_abandon_pending_only_touches_pending_tasks(self) -> None:
        sup = DeterministicSupervisor(RunMode.DEEP, "run_abandon")
        sup.accept_plan(PlanDAG(1, "g", ("done",), (_task("t1"), _task("t2", ("t1",)))))
        ready = sup.next_ready_tasks()
        self.assertEqual([t.task_id for t in ready], ["t1"])
        self.assertEqual(sup.abandon_pending(), ("t2",))
        self.assertEqual(sup.state.task_statuses["t1"], TaskStatus.RUNNING)
        self.assertEqual(sup.state.task_statuses["t2"], TaskStatus.CANCELLED)


class TestOrchestratorBudgetGuardrails(unittest.TestCase):
    def _run(self, run_id: str, limits: RunBudgetLimits | None = None):
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            c.providers.register(_TextGateway())
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            orch = InvestigationOrchestrator(c)
            events: list = []
            with patch.object(supervisor_module, "DEEP_BUDGET_LIMITS", limits or DEEP_BUDGET_LIMITS):
                answer = orch.execute_local(
                    run_id=run_id,
                    workspace_id="ws1",
                    question="Reconcile INR orders and fees",
                    mode=RunMode.DEEP,
                    emit_event=events.append,
                    replan_evaluator=_always_reject,
                )
            return answer, events

    @staticmethod
    def _finished(events: list) -> dict:
        return next(e.payload for e in events if e.type.value == "run.finished")

    def test_deep_run_survives_every_replan(self) -> None:
        answer, events = self._run("run_all_replans")
        plan_events = [e for e in events if e.type.value == "plan.updated"]
        self.assertEqual(len(plan_events), 1 + DEEP_BUDGET_LIMITS.max_replans)
        self.assertEqual(self._finished(events)["status"], "completed")
        self.assertIn("The review found unresolved gaps after the available remediation steps.", answer.limitations)

    def test_replan_is_skipped_when_specialist_capacity_is_spent(self) -> None:
        tight = dataclasses.replace(DEEP_BUDGET_LIMITS, lifetime_specialist_instances=6)
        answer, events = self._run("run_tight_capacity", tight)
        plan_events = [e for e in events if e.type.value == "plan.updated"]
        self.assertEqual(len(plan_events), 2)
        self.assertEqual(self._finished(events)["status"], "completed")
        self.assertTrue(answer.summary_markdown)

    def test_exhausted_step_limit_skips_remaining_tasks_instead_of_crashing(self) -> None:
        tight = dataclasses.replace(DEEP_BUDGET_LIMITS, lifetime_specialist_instances=2, max_replans=0)
        answer, events = self._run("run_step_limit", tight)
        skipped = [
            e.payload["task_id"]
            for e in events
            if e.type.value == "task.status" and e.payload.get("status") == "cancelled"
        ]
        self.assertEqual(skipped, ["t2_review"])
        self.assertEqual(self._finished(events)["status"], "completed")
        self.assertTrue(any("limit of 2 analysis steps" in item for item in answer.limitations))

    def test_model_call_budget_keeps_a_reserve_for_the_answer(self) -> None:
        tight = dataclasses.replace(DEEP_BUDGET_LIMITS, total_model_attempts=3)
        answer, events = self._run("run_attempt_limit", tight)
        skipped = [
            e.payload["task_id"]
            for e in events
            if e.type.value == "task.status" and e.payload.get("status") == "cancelled"
        ]
        composer_routes = [
            e for e in events if e.type.value == "route.selected" and e.payload.get("task_id") == "answer_composer"
        ]
        self.assertEqual(sorted(skipped), ["t1_reconcile", "t2_review"])
        self.assertEqual(len(composer_routes), 1)
        self.assertEqual(self._finished(events)["status"], "completed")
        self.assertTrue(any("used its 3 model calls" in item for item in answer.limitations))


if __name__ == "__main__":
    unittest.main()
