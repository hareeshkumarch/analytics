import json
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse
from app.bootstrap.container import build_container
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile

B = "/api/v1/workspaces/ws1"


class _RecordingGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.prompts.append(json.dumps(request.messages, default=str))
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id,
                             text="Fees were 320 INR in October.", input_tokens=20, output_tokens=10)


class TestFollowUps(unittest.TestCase):
    def test_follow_up_joins_the_conversation_and_sees_the_earlier_turn(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            gateway = _RecordingGateway()
            container.providers.register(gateway)
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            app = create_app(container)
            client = TestClient(app)
            first = client.post(f"{B}/runs", json={"question": "What were fees in October?", "execute_inline": True}).json()["run_id"]
            self.assertEqual(client.get(f"{B}/runs/{first}").json()["status"], "completed")
            gateway.prompts.clear()
            second = client.post(
                f"{B}/runs", json={"question": "And how does that compare to September?", "execute_inline": True, "follow_up_of": first}
            ).json()["run_id"]
            runs = {r["run_id"]: r for r in client.get(f"{B}/runs").json()["runs"]}
            self.assertEqual(runs[first]["conversation_id"], runs[second]["conversation_id"])
            joined = "\n".join(gateway.prompts)
            self.assertIn("previous_turns", joined)
            self.assertIn("What were fees in October?", joined)
            self.assertIn("Fees were 320 INR in October.", joined)
            planner = [p for p in gateway.prompts if "Create a minimal analytical task plan" in p]
            self.assertTrue(planner)
            self.assertIn("previous_turns", planner[0])
            self.assertIn("What were fees in October?", planner[0])
            fresh = client.post(f"{B}/runs", json={"question": "Unrelated", "execute_inline": True}).json()["run_id"]
            self.assertNotEqual(client.get(f"{B}/runs").json()["runs"][0]["conversation_id"], runs[first]["conversation_id"])
            self.assertEqual(client.get(f"{B}/runs").json()["runs"][0]["run_id"], fresh)
            self.assertEqual(client.post(f"{B}/runs", json={"question": "x", "follow_up_of": "run_missing"}).status_code, 404)
            self.assertEqual(
                client.post("/api/v1/workspaces/ws2/runs", json={"question": "x", "follow_up_of": first}).status_code, 404
            )


if __name__ == "__main__":
    unittest.main()
