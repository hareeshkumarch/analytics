import re
import tempfile
import unittest
from collections import Counter

from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.services.execution_router import ExecutionRoute, choose_route
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.models import RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from tests.test_tool_loop import QUERY, _publish_orders

ONE_TABLE = {"table_details": [{"name": "orders"}]}


class TestChooseRoute(unittest.TestCase):
    def route(self, question: str, **kw) -> ExecutionRoute:
        args = {"mode": RunMode.QUICK, "snapshot": ONE_TABLE, "has_prior_turns": False, "has_clarification": False, **kw}
        return choose_route(question=question, **args).route

    def test_simple_single_metric_questions_go_direct(self) -> None:
        for q in ("What were total fees in October?", "Total sales by region", "How many orders are there?"):
            self.assertEqual(self.route(q), ExecutionRoute.DIRECT_QUERY, q)

    def test_everything_else_is_planned(self) -> None:
        for q in (
            "Compare fees in October and September",
            "Why did refunds grow?",
            "Sales this month versus last month",
            "Reconcile payouts against orders",
            "What were fees? And which region led?",
            "word " * 40,
        ):
            self.assertEqual(self.route(q), ExecutionRoute.STANDARD, q)
        self.assertEqual(self.route("Total fees", snapshot={"table_details": [{"name": "a"}, {"name": "b"}]}), ExecutionRoute.STANDARD)
        self.assertEqual(self.route("Total fees", snapshot=None), ExecutionRoute.STANDARD)
        self.assertEqual(self.route("Total fees", has_prior_turns=True), ExecutionRoute.STANDARD)
        self.assertEqual(self.route("Total fees", has_clarification=True), ExecutionRoute.STANDARD)
        self.assertEqual(self.route("Total fees", mode=RunMode.DEEP), ExecutionRoute.DEEP)


class RoleCountingGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.calls[request.role.value] += 1
        tool_msgs = [m for m in request.messages if m.get("role") == "tool"]
        names = {t.name for t in request.tools or ()}
        if "run_query_ir" in names and not tool_msgs:
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=(ToolCallResult("q1", "run_query_ir", QUERY),))
        found = re.findall(r"ev_[0-9a-f]{12}", " ".join(str(m.get("content")) for m in request.messages))
        text = f"North leads with 12.5 [{found[-1]}]." if found else "No data."
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=text, input_tokens=5, output_tokens=5)


class TestRoutedRuns(unittest.TestCase):
    def _run(self, question: str, mode: RunMode = RunMode.QUICK):
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gateway = RoleCountingGateway()
            c.providers.register(gateway)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            events: list = []
            saved: list[dict] = []
            answer = InvestigationOrchestrator(c).execute_local(
                run_id="run_route", workspace_id="ws1", question=question, mode=mode, snapshot_id="snap1",
                snapshot={**_publish_orders(c), "snapshot_id": f"snap_route_{mode.value}_{len(question)}"},
                emit_event=events.append, on_plan=saved.append,
            )
            return answer, gateway.calls, events, saved

    def test_single_metric_question_uses_two_model_calls(self) -> None:
        answer, calls, events, saved = self._run("Which region has the most sales?")
        self.assertEqual(sum(calls.values()), 2, calls)
        self.assertEqual(set(calls), {"data_specialist"})
        self.assertIn("North leads with 12.5 [ev_", answer.summary_markdown)
        self.assertEqual(answer.outcome, "success")
        self.assertEqual(answer.route, "direct_query")
        plan = [e.payload for e in events if e.type.value == "plan.updated"][-1]
        finished = [e.payload for e in events if e.type.value == "run.finished"][-1]
        self.assertEqual((plan["route"], finished["route"], saved[-1]["route"]), ("direct_query",) * 3)

    def test_comparison_question_still_plans_and_composes(self) -> None:
        answer, calls, events, _ = self._run("Compare north and south sales")
        self.assertGreaterEqual(calls["planner"], 1)
        self.assertGreaterEqual(calls["answer_composer"], 1)
        self.assertEqual(answer.route, "standard")

    def test_deep_mode_is_never_shortened(self) -> None:
        answer, calls, _, _ = self._run("Total sales", mode=RunMode.DEEP)
        self.assertGreaterEqual(calls["planner"], 1)
        self.assertEqual(answer.route, "deep")


if __name__ == "__main__":
    unittest.main()
