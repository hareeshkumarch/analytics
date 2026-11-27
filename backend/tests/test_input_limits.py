import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse
from app.bootstrap.container import build_container
from app.domain.limits import DEFAULT_GLOBAL_LIMITS
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile

B = "/api/v1/workspaces/ws1"


class RecordingGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.prompts.append(" ".join(str(m.get("content")) for m in request.messages))
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="No data.", input_tokens=1, output_tokens=1)


class TestInputLimits(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        c = build_container(artifact_root=self._td.name)
        self.gateway = RecordingGateway()
        c.providers.register(self.gateway)
        c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
        self.client = TestClient(create_app(c))

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_too_long_questions_and_answers_are_rejected_not_cut(self) -> None:
        limit = DEFAULT_GLOBAL_LIMITS.max_question_chars
        res = self.client.post(f"{B}/runs", json={"question": "x" * (limit + 1)})
        self.assertEqual(res.status_code, 422)
        self.assertIn(str(limit), str(res.json()))
        res = self.client.post(f"{B}/pins", json={"title": "t", "question_text": "x" * (limit + 1), "run_id": "run_x"})
        self.assertEqual(res.status_code, 422)
        res = self.client.post(f"{B}/runs/run_x/clarify", json={"revision": 0, "answer": "x" * (DEFAULT_GLOBAL_LIMITS.max_clarification_chars + 1)})
        self.assertEqual(res.status_code, 422)

    def test_a_question_at_the_limit_reaches_the_model_whole(self) -> None:
        limit = DEFAULT_GLOBAL_LIMITS.max_question_chars
        question = "Total fees? " + "q" * (limit - 13) + "Z"
        self.assertEqual(len(question), limit)
        res = self.client.post(f"{B}/runs", json={"question": question, "execute_inline": True})
        self.assertEqual(res.status_code, 202, res.text)
        self.assertTrue(any(question in p for p in self.gateway.prompts))

    def test_health_publishes_the_limits(self) -> None:
        limits = self.client.get("/api/v1/health").json()["input_limits"]
        self.assertEqual(
            limits,
            {
                "max_question_chars": DEFAULT_GLOBAL_LIMITS.max_question_chars,
                "max_clarification_chars": DEFAULT_GLOBAL_LIMITS.max_clarification_chars,
                "max_pin_title_chars": DEFAULT_GLOBAL_LIMITS.max_pin_title_chars,
            },
        )


if __name__ == "__main__":
    unittest.main()
