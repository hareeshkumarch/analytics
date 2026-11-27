import json
import re
import tempfile
import unittest

from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.services.orchestration_service import TRIMMED_FINDINGS_LIMITATION, InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.models import RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from tests.test_tool_loop import QUERY, _publish_orders

APPROVE = json.dumps({"approved": True, "gaps": [], "contradictions": [], "summary": "Supported."})


class LongFindingsGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self, finding_chars: int) -> None:
        self.finding_chars = finding_chars
        self.composer_inputs: list[dict] = []

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        role = request.role.value
        content = " ".join(str(m.get("content")) for m in request.messages)
        tool_msgs = [m for m in request.messages if m.get("role") == "tool"]
        if role in ("data_specialist", "reconciliation_specialist") and request.tools and not tool_msgs:
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=(ToolCallResult("q1", "run_query_ir", QUERY),))
        if role == "evidence_reviewer":
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=APPROVE)
        if role == "answer_composer":
            user = next(m for m in request.messages if m.get("role") == "user")
            self.composer_inputs.append(json.loads(str(user["content"])))
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="North leads.")
        found = re.findall(r"ev_[0-9a-f]{12}", content)
        marker = f"{role} finding [{found[-1]}] " if found else f"{role} finding "
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=marker + "x" * self.finding_chars, input_tokens=5, output_tokens=5)


class TestComposerFindings(unittest.TestCase):
    def _run(self, finding_chars: int):
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            gateway = LongFindingsGateway(finding_chars)
            c.providers.register(gateway)
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            answer = InvestigationOrchestrator(c).execute_local(
                run_id="run_long", workspace_id="ws1", question="Which region sells most?", mode=RunMode.DEEP,
                snapshot_id="snap1", snapshot={**_publish_orders(c), "snapshot_id": f"snap_long_{finding_chars}"},
            )
            return answer, gateway.composer_inputs

    def test_long_findings_all_reach_the_composer_and_the_trim_is_reported(self) -> None:
        answer, inputs = self._run(30_000)
        findings = inputs[-1]["findings"]
        self.assertEqual(len(findings), 3)
        self.assertTrue(findings[0].startswith("data_specialist finding"))
        self.assertTrue(findings[1].startswith("reconciliation_specialist finding"))
        self.assertEqual(findings[2], "Supported.")
        # The two long analytical findings share the cut: neither is reduced to a stub.
        self.assertLess(abs(len(findings[0]) - len(findings[1])), 100)
        self.assertGreater(len(findings[0]), 4_000)
        self.assertIn(TRIMMED_FINDINGS_LIMITATION, answer.limitations)
        self.assertIn(TRIMMED_FINDINGS_LIMITATION, inputs[-1]["limitations"])

    def test_short_findings_are_not_reported_as_trimmed(self) -> None:
        answer, inputs = self._run(200)
        self.assertEqual(len(inputs[-1]["findings"]), 3)
        self.assertNotIn(TRIMMED_FINDINGS_LIMITATION, answer.limitations)


if __name__ == "__main__":
    unittest.main()
