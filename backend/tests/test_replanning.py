import json
import tempfile
import unittest

from app.adapters.orchestration.supervisor import ReviewVerdict
from app.application.ports.providers import ModelRequest, ModelResponse
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.models import Claim, RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile


class _PlannerGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self, replan_reply: str) -> None:
        self.replan_reply = replan_reply
        self.replan_prompts: list[str] = []

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        system = " ".join(str(m.get("content")) for m in request.messages if m.get("role") == "system")
        text = f"Executed {request.role.value} step"
        if "evidence review rejected" in system:
            self.replan_prompts.append(json.dumps(request.messages, default=str))
            text = self.replan_reply
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=text, input_tokens=50, output_tokens=10)


def _reject_once(claims: list[Claim], replan_count: int) -> ReviewVerdict:
    return ReviewVerdict(approved=replan_count > 0, gaps=() if replan_count else ("Fee totals don't match payouts",))


class TestPlannerDrivenReplanning(unittest.TestCase):
    def _run(self, reply: str):
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gateway = _PlannerGateway(reply)
            c.providers.register(gateway)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            events: list = []
            answer = InvestigationOrchestrator(c).execute_local(
                run_id="run_replan", workspace_id="ws1", question="Reconcile fees", mode=RunMode.DEEP,
                emit_event=events.append, replan_evaluator=_reject_once,
            )
        plans = [e.payload for e in events if e.type.value == "plan.updated"]
        started = [e.payload["task_id"] for e in events if e.type.value == "task.status" and e.payload.get("status") == "running"]
        return gateway, answer, plans, started

    def test_planner_chooses_the_remediation_task(self) -> None:
        reply = json.dumps({"action": "remediate", "kind": "reconciliation_specialist", "objective": "Match fees.amount_inr to payouts by order_id", "reason": None})
        gateway, _, plans, started = self._run(reply)
        self.assertEqual(len(plans), 2)
        self.assertFalse(plans[1]["fallback"])
        self.assertIn("Fee totals don't match payouts", gateway.replan_prompts[0])
        remediation = [t for t in started if t.endswith("_remediate")]
        self.assertEqual(len(remediation), 1)

    def test_planner_can_stop_remediation(self) -> None:
        reply = json.dumps({"action": "stop", "kind": None, "objective": None, "reason": "The dataset has no payout records"})
        _, answer, plans, started = self._run(reply)
        self.assertEqual(len(plans), 1)
        self.assertFalse(any(t.endswith("_remediate") for t in started))
        self.assertIn("The planner stopped remediation: The dataset has no payout records", answer.limitations)

    def test_unusable_replan_reply_falls_back_to_the_template(self) -> None:
        _, answer, plans, started = self._run("not json")
        self.assertEqual(len(plans), 2)
        self.assertTrue(plans[1]["fallback"])
        self.assertTrue(plans[0]["fallback"])
        self.assertTrue(any(t.endswith("_remediate") for t in started))
        self.assertIn("The planner's reply couldn't be used, so the standard plan ran instead.", answer.limitations)


if __name__ == "__main__":
    unittest.main()
