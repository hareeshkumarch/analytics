import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse
from app.bootstrap.container import build_container
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile


class DeterministicTestGateway:
    def __init__(self, provider_id: ProviderId) -> None:
        self.provider_id = provider_id

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
            text="Net sales 3,000.00 INR; 300.00 INR in transit (timing difference).",
            input_tokens=120,
            output_tokens=30,
        )


class ClarificationTestGateway:
    def __init__(self) -> None:
        self.provider_id = ProviderId.OPENAI
        self.planner_calls = 0

    def invoke(
        self,
        request: ModelRequest,
        provider_profile: ProviderProfile,
        model_profile: ModelProfile,
        api_key: str,
    ) -> ModelResponse:
        if request.role.value == "planner":
            self.planner_calls += 1
            text = (
                json.dumps(
                    {
                        "needs_clarification": True,
                        "clarification_question": "Which currency should I use?",
                    }
                )
                if self.planner_calls == 1
                else json.dumps(
                    {
                        "goal": "Answer using the clarified currency",
                        "completion_criteria": ["Answer the question"],
                        "tasks": [
                            {
                                "task_id": "t1_analyze",
                                "kind": "data_specialist",
                                "objective": "Answer using the clarified currency",
                                "dependencies": [],
                                "required_inputs": [],
                            }
                        ],
                    }
                )
            )
        elif request.role.value == "answer_composer":
            text = "The analysis used the clarified INR currency."
        else:
            text = "Analysis completed using the clarified INR currency."
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=model_profile.model_id,
            text=text,
            input_tokens=80,
            output_tokens=24,
        )


class TestEndToEndSlice(unittest.TestCase):
    def test_planner_clarification_pauses_and_resumes_the_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            gateway = ClarificationTestGateway()
            container.providers.register(gateway)
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-only-key"
            app = create_app(container)

            with patch.dict(os.environ, {"RUN_EXECUTION_MODE": "inline", "AGENT_RUNTIME": "deepagents"}):
                with TestClient(app) as client:
                    created = client.post(
                        "/api/v1/workspaces/ws1/runs",
                        json={"question": "What is the amount?", "mode": "quick", "execute_inline": True},
                    )
                    self.assertEqual(created.status_code, 202)
                    run_id = created.json()["run_id"]
                    pending = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}").json()
                    self.assertEqual(pending["status"], "awaiting_input")
                    self.assertEqual(pending["clarification_prompt"], "Which currency should I use?")
                    self.assertEqual(pending["clarification_revision"], 0)

                    resumed = client.post(
                        f"/api/v1/workspaces/ws1/runs/{run_id}/clarify",
                        json={"revision": 0, "answer": "INR"},
                    )
                    self.assertEqual(resumed.status_code, 200)
                    self.assertEqual(resumed.json()["revision"], 1)

                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        completed = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}").json()
                        if completed["status"] not in {"queued", "running"}:
                            break
                        time.sleep(0.01)
                    self.assertEqual(completed["status"], "completed")
                    self.assertIn("clarified INR", completed["answer"]["summary_markdown"])
                    self.assertEqual(gateway.planner_calls, 2)
                    event_types = [event.type.value for event in app.state.repo_events.list_after(run_id, 0)]
                    self.assertIn("clarification.required", event_types)
                    self.assertIn("answer.reset", event_types)
                    self.assertEqual(event_types[-1], "run.finished", event_types)

    def test_full_workspace_vertical_slice(self) -> None:
        orders_bytes = (Path(__file__).resolve().parents[2] / "fixtures" / "inr_acceptance" / "orders.csv").read_bytes()
        orders_b64 = base64.b64encode(orders_bytes).decode("ascii")

        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            container.providers.register(DeterministicTestGateway(ProviderId.OPENAI))
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-only-key"

            app = create_app(container)
            client = TestClient(app)

            cfg = {"format": "csv", "table_name": "orders", "raw_base64": orders_b64}
            r_probe = client.post("/api/v1/workspaces/ws1/connectors/probe", json={"family": "uploaded_file", "config": cfg})
            self.assertEqual(r_probe.status_code, 200)
            self.assertTrue(r_probe.json()["ok"])

            r_disc = client.post("/api/v1/workspaces/ws1/connectors/discover", json={"family": "uploaded_file", "config": cfg})
            self.assertEqual(r_disc.status_code, 200)
            self.assertTrue(r_disc.json()["tables"][0]["columns"][0]["is_leading_zero_id"])

            r_ext = client.post(
                "/api/v1/workspaces/ws1/connectors/extract",
                json={"family": "uploaded_file", "config": cfg, "selected_tables": ["orders"]},
            )
            self.assertEqual(r_ext.status_code, 201)
            snap_id = r_ext.json()["snapshot"]["snapshot_id"]
            sem_id = r_ext.json()["semantics"]["semantic_version_id"]

            r_bad_approve = client.post(
                f"/api/v1/workspaces/ws1/semantics/{snap_id}/approve",
                json={"expected_version": 99},
            )
            self.assertEqual(r_bad_approve.status_code, 409)

            r_approve = client.post(
                f"/api/v1/workspaces/ws1/semantics/{snap_id}/approve",
                json={"expected_version": 1},
            )
            self.assertEqual(r_approve.status_code, 200)
            self.assertTrue(r_approve.json()["approved"])

            r_run = client.post(
                "/api/v1/workspaces/ws1/runs",
                json={
                    "question": "Explain INR settlement",
                    "mode": "quick",
                    "execute_inline": True,
                    "snapshot_id": snap_id,
                    "semantic_version_id": sem_id,
                },
            )
            self.assertEqual(r_run.status_code, 202)
            run_id = r_run.json()["run_id"]

            r_get = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}")
            self.assertEqual(r_get.json()["status"], "completed")

            r_pin = client.post(
                "/api/v1/workspaces/ws1/pins",
                json={"title": "INR Settlement", "question_text": "Explain INR settlement", "run_id": run_id},
            )
            self.assertEqual(r_pin.status_code, 201)
            self.assertEqual(len(client.get("/api/v1/workspaces/ws1/pins").json()["pins"]), 1)

            for fmt in ("csv", "html", "pdf", "docx"):
                r_exp = client.post(
                    "/api/v1/workspaces/ws1/exports",
                    json={"format": fmt, "run_id": run_id},
                )
                self.assertEqual(r_exp.status_code, 201)
                dl = client.get(r_exp.json()["download_url"])
                self.assertEqual(dl.status_code, 200)
                self.assertGreater(len(dl.content), 10)

            r_usage = client.get("/api/v1/workspaces/ws1/usage")
            self.assertEqual(r_usage.status_code, 200)
            self.assertEqual(r_usage.json()["run_count"], 1)

            os.environ["APP_MASTER_KEY"] = Fernet.generate_key().decode("utf-8")
            r_key = client.put(
                "/api/v1/workspaces/ws1/settings/provider-key",
                json={"provider_id": "anthropic", "api_key": "sk-ant-secret-value"},
            )
            self.assertEqual(r_key.status_code, 200)
            self.assertNotIn("sk-ant-secret-value", r_key.text)


if __name__ == "__main__":
    unittest.main()
