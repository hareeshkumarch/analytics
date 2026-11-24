import json
import tempfile
import unittest

from app.adapters.providers.http_utils import classify_http_error
from app.application.ports.providers import ModelRequest, ModelResponse
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.bootstrap.container import build_container
from app.domain.errors import ProviderError, public_message
from app.domain.models import RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile

SECRET = "internal trace id=abc123 key=sk-live-leak"


class _ExplodingGateway:
    provider_id = ProviderId.OPENAI

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        if request.role.value == "planner":
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="{}", input_tokens=10, output_tokens=5)
        raise RuntimeError(SECRET)


class TestErrorExposure(unittest.TestCase):
    def test_provider_body_stays_out_of_the_error_message(self) -> None:
        for status in (400, 429, 500):
            with self.assertLogs("app.adapters.providers.http_utils", level="WARNING") as logs:
                err = classify_http_error(status, SECRET, "openai")
            self.assertNotIn(SECRET, str(err))
            self.assertIn(SECRET, "\n".join(logs.output))

    def test_public_message_keeps_domain_text_and_hides_the_rest(self) -> None:
        self.assertEqual(public_message(ProviderError("No eligible model")), "No eligible model")
        hidden = public_message(RuntimeError(SECRET))
        self.assertNotIn(SECRET, hidden)
        self.assertIn("RuntimeError", hidden)

    def test_unexpected_step_failures_do_not_reach_run_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            c.providers.register(_ExplodingGateway())
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            events: list = []
            InvestigationOrchestrator(c).execute_local(
                run_id="run_leak", workspace_id="ws1", question="Total?", mode=RunMode.QUICK, emit_event=events.append
            )
        dumped = json.dumps([e.payload for e in events], default=str)
        self.assertNotIn(SECRET, dumped)
        self.assertIn("server log", dumped)


if __name__ == "__main__":
    unittest.main()
