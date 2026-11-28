import json
import tempfile
import unittest
from collections import Counter

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse
from app.application.services.execution_router import ExecutionRoute, choose_route
from app.application.services.orchestration_service import InvestigationOrchestrator, _evidence_verdict
from app.application.services.small_talk import small_talk_kind, small_talk_reply
from app.application.services.verification import stated_figures
from app.bootstrap.container import build_container
from app.domain.models import Claim, RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from tests.test_execution_router import RoleCountingGateway
from tests.test_tool_loop import _publish_orders

B = "/api/v1/workspaces/ws1"
ONE_TABLE = {"table_details": [{"name": "orders"}]}


class TestSmallTalkDetection(unittest.TestCase):
    def test_greetings_thanks_and_goodbyes_are_small_talk(self) -> None:
        cases = {
            "hi": "greeting",
            "Hello!": "greeting",
            "hey there 👋": "greeting",
            "Good morning": "greeting",
            "how are you?": "greeting",
            "What's up": "greeting",
            "thanks": "thanks",
            "Thank you so much!": "thanks",
            "ok thanks": "thanks",
            "thx 🙏": "thanks",
            "cheers mate": "thanks",
            "great, thanks for the help": "thanks",
            "hi, thanks": "thanks",
            "bye": "goodbye",
            "See you": "goodbye",
        }
        for text, kind in cases.items():
            self.assertEqual(small_talk_kind(text), kind, text)

    def test_anything_with_a_question_or_data_term_stays_on_the_normal_path(self) -> None:
        for text in (
            "",
            "   ",
            "hi, how many rows?",
            "hello, what's in this dataset?",
            "thanks, now show revenue by month",
            "what's up with revenue",
            "Total sales",
            "how many orders are there?",
            "hi 2024",
            "hi 销售额",
            "hola, ventas por mes",
            # Bare acknowledgements can mean "go ahead" after a suggestion, so they are not small talk.
            "ok",
            "yes",
            "sure",
            "no thanks",
            "great",
            "help",
            "hi " * 20,
        ):
            self.assertIsNone(small_talk_kind(text), text)

    def test_route_is_small_talk_in_both_modes_and_after_earlier_turns(self) -> None:
        for mode in (RunMode.QUICK, RunMode.DEEP):
            for prior in (False, True):
                decision = choose_route(question="thanks!", mode=mode, snapshot=ONE_TABLE, has_prior_turns=prior, has_clarification=False)
                self.assertEqual(decision.route, ExecutionRoute.SMALL_TALK)
        # A clarification means a real question is part-way through; never short-cut it.
        resumed = choose_route(question="hi", mode=RunMode.QUICK, snapshot=ONE_TABLE, has_prior_turns=False, has_clarification=True)
        self.assertNotEqual(resumed.route, ExecutionRoute.SMALL_TALK)
        mixed = choose_route(question="hi, how many rows?", mode=RunMode.QUICK, snapshot=ONE_TABLE, has_prior_turns=False, has_clarification=False)
        self.assertEqual(mixed.route, ExecutionRoute.DIRECT_QUERY)

    def test_reply_points_the_user_to_their_data(self) -> None:
        snapshot = {"table_details": [{"name": "orders"}, {"name": "pay`outs"}, {"name": "fees"}, {"name": "refunds"}]}
        greeting = small_talk_reply("hello", snapshot)
        self.assertIn("questions about your data", greeting)
        self.assertIn("`orders`, `payouts`, `fees` and more", greeting)
        self.assertTrue(small_talk_reply("thank you", None).startswith("You're welcome!"))
        self.assertTrue(small_talk_reply("bye", snapshot).startswith("Goodbye!"))


class _ToolRecordingGateway(RoleCountingGateway):
    def __init__(self) -> None:
        super().__init__()
        self.tools_by_role: dict[str, set[str]] = {}

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.tools_by_role.setdefault(request.role.value, set()).update(t.name for t in request.tools or ())
        return super().invoke(request, provider_profile, model_profile, api_key)


def _orchestrate(question: str, mode: RunMode, gateway, prior_turns=None):
    with tempfile.TemporaryDirectory() as td:
        c = build_container(artifact_root=td)
        c.providers.register(gateway)
        c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
        events: list = []
        answer = InvestigationOrchestrator(c).execute_local(
            run_id="run_small", workspace_id="ws1", question=question, mode=mode, snapshot_id="snap1",
            snapshot={**_publish_orders(c), "snapshot_id": f"snap_small_{mode.value}_{len(question)}"},
            emit_event=events.append, prior_turns=prior_turns,
        )
        totals = c.budget_ledger.get_run_totals("run_small")
        return answer, events, totals


class TestSmallTalkRuns(unittest.TestCase):
    def test_small_talk_runs_make_no_model_or_tool_calls(self) -> None:
        for mode in (RunMode.QUICK, RunMode.DEEP):
            for question, prior in (("hi", None), ("thanks", [{"question": "Total sales?", "answer": "**12.5** [ev_0123456789ab]"}])):
                gateway = RoleCountingGateway()
                answer, events, totals = _orchestrate(question, mode, gateway, prior_turns=prior)
                label = f"{mode.value} {question}"
                self.assertEqual(sum(gateway.calls.values()), 0, label)
                self.assertEqual(int(str(totals.get("used_attempts", 0))), 0, label)
                self.assertEqual(answer.route, "small_talk", label)
                self.assertEqual(answer.outcome, "answered", label)
                self.assertEqual((answer.claims, answer.charts, answer.limitations), ((), (), ()), label)
                types = [e.type.value for e in events]
                self.assertNotIn("tool.result", types, label)
                self.assertNotIn("task.status", types, label)
                plan = [e.payload for e in events if e.type.value == "plan.updated"][-1]
                self.assertEqual((plan["route"], plan["tasks"]), ("small_talk", []), label)
                finished = [e.payload for e in events if e.type.value == "run.finished"][-1]
                self.assertEqual((finished["status"], finished["route"], finished["tool_calls"]), ("completed", "small_talk", 0), label)
                streamed = "".join(str(e.payload["text"]) for e in events if e.type.value == "answer.delta")
                self.assertEqual(streamed, answer.summary_markdown, label)

    def test_greeting_with_a_question_still_runs_the_analysis(self) -> None:
        gateway = RoleCountingGateway()
        answer, _, _ = _orchestrate("hi, which region has the most sales?", RunMode.QUICK, gateway)
        self.assertEqual(answer.route, "direct_query")
        self.assertGreaterEqual(gateway.calls["data_specialist"], 1)
        self.assertIn("North leads with 12.5", answer.summary_markdown)


class _FeesGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.prompts.append(json.dumps(request.messages, default=str))
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id,
                             text="Fees were 320 INR in October.", input_tokens=20, output_tokens=10)


class TestSmallTalkInConversations(unittest.TestCase):
    def test_small_talk_is_a_recorded_run_but_not_context_for_the_next_question(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            gateway = _FeesGateway()
            container.providers.register(gateway)
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            client = TestClient(create_app(container))

            hello = client.post(f"{B}/runs", json={"question": "hello", "execute_inline": True}).json()["run_id"]
            stored = client.get(f"{B}/runs/{hello}").json()
            self.assertEqual(stored["status"], "completed")
            self.assertEqual((stored["answer"]["route"], stored["answer"]["outcome"]), ("small_talk", "answered"))
            self.assertIn("questions about your data", stored["answer"]["summary_markdown"])
            self.assertEqual(gateway.prompts, [])
            self.assertIn(hello, {r["run_id"] for r in client.get(f"{B}/runs").json()["runs"]})

            real = client.post(
                f"{B}/runs", json={"question": "What were fees in October?", "execute_inline": True, "follow_up_of": hello}
            ).json()["run_id"]
            self.assertEqual(client.get(f"{B}/runs/{real}").json()["status"], "completed")
            joined = "\n".join(gateway.prompts)
            self.assertIn('\\"previous_turns\\": []', joined)
            self.assertNotIn("<previous_turns>", joined)
            self.assertNotIn("Q: hello", joined)

            gateway.prompts.clear()
            thanks = client.post(f"{B}/runs", json={"question": "thanks!", "execute_inline": True, "follow_up_of": real}).json()["run_id"]
            self.assertEqual(client.get(f"{B}/runs/{thanks}").json()["answer"]["route"], "small_talk")
            self.assertEqual(gateway.prompts, [])

            client.post(f"{B}/runs", json={"question": "And September?", "execute_inline": True, "follow_up_of": thanks})
            joined = "\n".join(gateway.prompts)
            self.assertIn("<previous_turns>\nQ: What were fees in October?", joined.replace("\\n", "\n"))
            self.assertNotIn("You're welcome", joined)
            self.assertNotIn("Q: thanks!", joined)


class TestSpecialistToolManifest(unittest.TestCase):
    def test_specialists_are_not_offered_the_task_tool(self) -> None:
        gateway = _ToolRecordingGateway()
        _orchestrate("Total sales", RunMode.DEEP, gateway)
        self.assertIn("run_query_ir", gateway.tools_by_role.get("data_specialist", set()))
        offered = set().union(*gateway.tools_by_role.values())
        self.assertNotIn("task", offered, gateway.tools_by_role)
        self.assertLessEqual(offered, {"inspect_schema", "run_query_ir", "calculate"})


class _NoFiguresGateway:
    """Planner plans one analysis step and a review; the analysis step answers without querying or giving figures."""

    provider_id = ProviderId.OPENAI

    def __init__(self, step_text: str) -> None:
        self.calls: Counter[str] = Counter()
        self.step_text = step_text

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        role = request.role.value
        self.calls[role] += 1
        if role == "planner":
            text = json.dumps({
                "needs_clarification": False, "clarification_question": None, "goal": "Answer",
                "completion_criteria": ["Answer grounded"],
                "tasks": [
                    {"task_id": "t1", "kind": "data_specialist", "objective": "Describe the status column", "dependencies": [],
                     "required_inputs": [], "acceptance_criteria": ["Figures cite evidence"]},
                    {"task_id": "t2", "kind": "evidence_reviewer", "objective": "Check t1", "dependencies": ["t1"],
                     "required_inputs": ["t1"], "acceptance_criteria": ["Verdict lists gaps"]},
                ],
            })
        elif role == "data_specialist":
            text = self.step_text
        elif role == "evidence_reviewer":
            text = json.dumps({"approved": True, "gaps": [], "contradictions": [], "summary": "Nothing to check."})
        elif role == "semantic_assistant":
            text = json.dumps({"follow_ups": []})
        else:
            text = self.step_text
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=text, input_tokens=5, output_tokens=5)


class TestStepsWithoutFigures(unittest.TestCase):
    @staticmethod
    def _claim(cid: str, statement: str) -> Claim:
        return Claim(claim_id=cid, label="unverified_observation", statement=statement, value=None, unit=None, currency=None, evidence_ids=())

    def test_stated_figures_ignores_dates_years_and_evidence_ids(self) -> None:
        self.assertEqual(stated_figures("Hello! Ask me a question about your dataset."), [])
        self.assertEqual(stated_figures("There is no refunds column in 2024 or on 2024-10-01 [ev_0123456789ab]."), [])
        self.assertEqual(stated_figures("1. Revenue was $1,200 [ev_0123456789ab] and grew 4%."), ["$1,200", "4%"])

    def test_a_claim_without_figures_needs_no_evidence(self) -> None:
        verdict = _evidence_verdict([self._claim("clm_t1", "The dataset has no refunds column, so this can't be answered.")], set(), set())
        self.assertTrue(verdict.approved)
        self.assertEqual(verdict.gaps, ())

    def test_figures_anywhere_in_the_step_still_need_evidence(self) -> None:
        clipped = self._claim("clm_t1", "Here is what I found in the orders table")
        self.assertTrue(_evidence_verdict([clipped], set(), set()).approved)
        verdict = _evidence_verdict([clipped], set(), set(), {"t1": "Here is what I found in the orders table: revenue was 4,500."})
        self.assertFalse(verdict.approved)
        self.assertIn("step t1 gave figures without query evidence", verdict.gaps[0])

    def test_deep_step_reporting_no_figures_is_not_replanned(self) -> None:
        gateway = _NoFiguresGateway("The dataset has no column describing refunds, so this cannot be answered from it.")
        answer, events, _ = _orchestrate("What do refunds look like?", RunMode.DEEP, gateway)
        self.assertEqual(gateway.calls["planner"], 1, gateway.calls)
        self.assertEqual(gateway.calls["evidence_reviewer"], 1, gateway.calls)
        planned = [task for e in events if e.type.value == "plan.updated" for task in e.payload["tasks"]]
        self.assertFalse([t for t in planned if "remediate" in t], planned)
        self.assertNotIn("The review found unresolved gaps after the available remediation steps.", answer.limitations)

    def test_deep_step_giving_figures_without_evidence_is_still_replanned(self) -> None:
        gateway = _NoFiguresGateway("Refunds totalled 4,500 last month.")
        _, events, _ = _orchestrate("What do refunds look like?", RunMode.DEEP, gateway)
        self.assertGreaterEqual(gateway.calls["planner"], 2, gateway.calls)
        planned = [task for e in events if e.type.value == "plan.updated" for task in e.payload["tasks"]]
        self.assertTrue([t for t in planned if "remediate" in t], planned)


if __name__ == "__main__":
    unittest.main()
