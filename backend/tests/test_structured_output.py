import json
import tempfile
import unittest

from app.adapters.orchestration.structured_schemas import PLAN_SCHEMA, REPLAN_SCHEMA, REVIEW_SCHEMA
from app.application.ports.providers import ModelRequest, ModelResponse
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.models import RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile

PLAN = {
    "needs_clarification": False,
    "clarification_question": None,
    "goal": "Total fees",
    "completion_criteria": ["Grounded"],
    "tasks": [
        {"task_id": "fees", "kind": "data_specialist", "objective": "Sum fees", "dependencies": [], "required_inputs": []},
        {"task_id": "audit", "kind": "evidence_reviewer", "objective": "Check fees", "dependencies": ["fees"], "required_inputs": ["fees"]},
    ],
}


class _ProseGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self, review: dict) -> None:
        self.review = review
        self.schemas: list[dict] = []

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        if request.structured_schema is not None:
            self.schemas.append(request.structured_schema)
            if request.structured_schema == PLAN_SCHEMA:
                data = PLAN
            elif request.structured_schema == REVIEW_SCHEMA:
                data = self.review
            else:
                data = {"action": "stop", "kind": None, "objective": None, "reason": "Nothing more to check"}
            text = json.dumps(data)
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=text, structured_output=data, input_tokens=40, output_tokens=20)
        return ModelResponse(
            provider_id=self.provider_id, model_id=model_profile.model_id,
            text="Here is my reasoning in prose, without any JSON.", input_tokens=40, output_tokens=20,
        )


class TestStructuredOutput(unittest.TestCase):
    def _run(self, review: dict):
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gateway = _ProseGateway(review)
            c.providers.register(gateway)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            events: list = []
            answer = InvestigationOrchestrator(c).execute_local(
                run_id="run_structured", workspace_id="ws1", question="Total fees?", mode=RunMode.DEEP, emit_event=events.append,
            )
        plans = [e.payload for e in events if e.type.value == "plan.updated"]
        return gateway, answer, plans

    def test_prose_plan_and_review_are_repaired_with_structured_output(self) -> None:
        gateway, answer, plans = self._run({"approved": True, "gaps": [], "contradictions": [], "summary": "All figures cited."})
        self.assertEqual(plans[0]["tasks"], ["fees", "audit"])
        self.assertFalse(plans[0]["fallback"])
        self.assertEqual(len(plans), 1)
        self.assertIn(PLAN_SCHEMA, gateway.schemas)
        self.assertIn(REVIEW_SCHEMA, gateway.schemas)
        self.assertNotIn("The planner's reply couldn't be used, so the standard plan ran instead.", answer.limitations)

    def test_replanning_uses_the_replan_schema(self) -> None:
        gateway, answer, plans = self._run({"approved": False, "gaps": ["fees lack evidence"], "contradictions": [], "summary": "Gaps."})
        self.assertIn(REPLAN_SCHEMA, gateway.schemas)
        self.assertEqual(len(plans), 1)
        self.assertIn("The planner stopped remediation: Nothing more to check", answer.limitations)


if __name__ == "__main__":
    unittest.main()
