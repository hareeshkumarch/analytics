import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse
from app.bootstrap.container import build_container
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile

B = "/api/v1/workspaces/ws1"


class _GatedGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self) -> None:
        self.armed = threading.Event()
        self.first = True

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        if self.first:
            self.first = False
            self.armed.wait(10)
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="Done.", input_tokens=5, output_tokens=2)


class TestOneRunsSlowEventWriteDoesNotBlockOthers(unittest.TestCase):
    def test_other_runs_can_be_queued_and_cancelled_while_one_runs_event_write_is_stuck(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            gateway = _GatedGateway()
            container.providers.register(gateway)
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            app = create_app(container)
            blocked: set[str] = set()
            entered = threading.Event()
            release = threading.Event()
            original_append = app.state.repo_events.append

            def slow_append(event, **kwargs):
                if event.run_id in blocked and event.type.value != "run.status":
                    entered.set()
                    release.wait(15)
                return original_append(event, **kwargs)

            app.state.repo_events.append = slow_append
            with TestClient(app) as client:
                run_a = client.post(f"{B}/runs", json={"question": "slow one", "background_async": True}).json()["run_id"]
                blocked.add(run_a)
                gateway.armed.set()
                self.assertTrue(entered.wait(10), "run A never reached its event write")
                timer = threading.Timer(12, release.set)
                timer.start()
                started = time.monotonic()
                run_b = client.post(f"{B}/runs", json={"question": "queued one", "background_async": True}).json()["run_id"]
                cancelled = client.post(f"{B}/runs/{run_b}/cancel").json()
                elapsed = time.monotonic() - started
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertFalse(release.is_set(), "run B waited for run A's event write")
                self.assertLess(elapsed, 5)
                timer.cancel()
                release.set()
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and client.get(f"{B}/runs/{run_a}").json()["status"] != "completed":
                    time.sleep(0.05)
                self.assertEqual(client.get(f"{B}/runs/{run_a}").json()["status"], "completed")
                sequences = [e.sequence for e in app.state.repo_events.list_after(run_a, 0)]
                self.assertEqual(sequences, sorted(sequences))


if __name__ == "__main__":
    unittest.main()
