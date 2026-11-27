import base64
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container
from app.domain.models import Answer
from app.domain.providers import ProviderId
from tests.test_run_fencing import _Gateway

CSV = base64.b64encode(b"order_id,amount\n001,100\n").decode("ascii")


class TestObjectsStayInTheirWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.app = create_app(build_container(artifact_root=self._td.name))
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_definitions_and_exports_are_not_reachable_from_another_workspace(self) -> None:
        snap = self.client.post(
            "/api/v1/workspaces/ws_a/connectors/extract",
            json={"family": "uploaded_file", "config": {"format": "csv", "table_name": "orders", "raw_base64": CSV}, "selected_tables": ["orders"]},
        ).json()
        sid, version = snap["snapshot"]["snapshot_id"], snap["semantics"]["version"]
        self.assertEqual(self.client.get(f"/api/v1/workspaces/ws_b/semantics/{sid}").status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/v1/workspaces/ws_b/semantics/{sid}/approve", json={"expected_version": version}).status_code, 404
        )
        self.assertFalse(self.client.get(f"/api/v1/workspaces/ws_a/semantics/{sid}").json()["approved"])
        self.assertEqual(self.client.get(f"/api/v1/workspaces/ws_a/semantics/{sid}").status_code, 200)

        run_id = self.client.post("/api/v1/workspaces/ws_a/runs", json={"question": "q", "mode": "quick"}).json()["run_id"]
        self.app.state.repo_runs.save_answer(Answer(
            answer_id="ans_1", run_id=run_id, summary_markdown="Total 100.", claims=(), charts=(), limitations=(),
            snapshot_id=sid, semantic_version_id="sem", created_at=datetime.now(timezone.utc),
        ))
        self.assertEqual(self.client.post("/api/v1/workspaces/ws_b/exports", json={"run_id": run_id, "format": "html"}).status_code, 404)
        made = self.client.post("/api/v1/workspaces/ws_a/exports", json={"run_id": run_id, "format": "html"}).json()
        self.assertEqual(self.client.get(made["download_url"]).status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/workspaces/ws_b/exports/{made['artifact_id']}").status_code, 404)


BODIES = {
    ("post", "/clarify"): {"revision": 0, "answer": "October"},
    ("post", "/approve"): {"expected_version": 1},
    ("patch", "/pins/{pin_id}"): {"auto_refresh": True},
    ("post", "/notes"): {"text": "Revenue means paid orders only."},
}


class TestEveryObjectRouteIsScoped(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        container = build_container(artifact_root=self._td.name)
        container.providers.register(_Gateway())
        container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
        self.app = create_app(container)
        self.client = TestClient(self.app)
        a = "/api/v1/workspaces/ws_a"
        self.snapshot_id = self.client.post(f"{a}/samples/inr_acceptance/import").json()["snapshot"]["snapshot_id"]
        self.run_id = self.client.post(f"{a}/runs", json={"question": "Total fees?", "snapshot_id": self.snapshot_id, "execute_inline": True}).json()["run_id"]
        self.pin_id = self.client.post(f"{a}/pins", json={"title": "Fees", "question_text": "Total fees?", "run_id": self.run_id}).json()["pin_id"]
        self.export_id = self.client.post(f"{a}/exports", json={"run_id": self.run_id, "format": "html"}).json()["artifact_id"]
        self.upload_id = self.client.post(f"{a}/uploads", content=b"x,y\n1,2\n", headers={"Content-Type": "application/octet-stream"}).json()["upload_id"]

        self.note_id = self.client.post(f"{a}/snapshots/{self.snapshot_id}/notes", json={"text": "Fees are gateway fees only."}).json()["note_id"]

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_path_ids_from_another_workspace_are_not_found(self) -> None:
        ids = {
            "note_id": self.note_id,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "pin_id": self.pin_id,
            "artifact_id": self.export_id,
            "evidence_id": "ev_000000000000",
        }
        paths = self.client.get("/api/openapi.json").json()["paths"]
        checked = []
        for path, operations in paths.items():
            params = set(re.findall(r"{(\w+)}", path))
            if "workspace_id" not in params or not params & ids.keys():
                continue
            for method in operations:
                body = next((b for (m, suffix), b in BODIES.items() if m == method and path.endswith(suffix)), None)
                url = path.format(workspace_id="ws_b", **ids)
                res = self.client.request(method.upper(), url, json=body)
                checked.append((method, path))
                self.assertEqual(res.status_code, 404, f"{method.upper()} {path}: {res.status_code} {res.text[:200]}")
        self.assertGreaterEqual(len(checked), 18, checked)
        a = "/api/v1/workspaces/ws_a"
        self.assertEqual(self.client.get(f"{a}/runs/{self.run_id}").status_code, 200)
        self.assertEqual([p["pin_id"] for p in self.client.get(f"{a}/pins").json()["pins"]], [self.pin_id])
        self.assertEqual(self.client.get(f"{a}/snapshots/{self.snapshot_id}/diagnostics").status_code, 200)
        self.assertEqual(self.client.get(f"{a}/exports/{self.export_id}").status_code, 200)

    def test_ids_in_request_bodies_from_another_workspace_are_not_found(self) -> None:
        b = "/api/v1/workspaces/ws_b"
        cases = [
            (f"{b}/runs", {"question": "q", "snapshot_id": self.snapshot_id}),
            (f"{b}/runs", {"question": "q", "follow_up_of": self.run_id}),
            (f"{b}/pins", {"title": "t", "question_text": "q", "run_id": self.run_id}),
            (f"{b}/exports", {"run_id": self.run_id, "format": "html"}),
            (f"{b}/connectors/extract", {"family": "uploaded_file", "config": {"format": "csv", "table_name": "t", "upload_id": self.upload_id}, "selected_tables": ["t"]}),
        ]
        for url, body in cases:
            res = self.client.post(url, json=body)
            self.assertEqual(res.status_code, 404, f"{url} {body}: {res.status_code} {res.text[:200]}")
        self.assertEqual(self.client.get(f"{b}/runs").json()["runs"], [])
        self.assertEqual(self.client.get(f"{b}/snapshots").json()["snapshots"], [])


class TestProviderKeysStayInTheirWorkspace(unittest.TestCase):
    def test_a_key_saved_in_one_workspace_is_not_used_by_another(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"APP_MASTER_KEY": Fernet.generate_key().decode()}):
            for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "XAI_API_KEY", "MOONSHOT_API_KEY", "NVIDIA_NIM_API_KEY"):
                os.environ.pop(name, None)
            container = build_container(artifact_root=td)
            container.providers.register(_Gateway())
            client = TestClient(create_app(container))
            saved = client.put("/api/v1/workspaces/ws_a/settings/provider-key", json={"provider_id": "openai", "api_key": "sk-ws-a-only"})
            self.assertEqual(saved.status_code, 200, saved.text)

            def openai_status(ws: str) -> bool:
                providers = client.get(f"/api/v1/workspaces/{ws}/providers").json()["providers"]
                return next(p for p in providers if p["provider_id"] == "openai")["configured"]

            self.assertTrue(openai_status("ws_a"))
            self.assertFalse(openai_status("ws_b"))
            run_b = client.post("/api/v1/workspaces/ws_b/runs", json={"question": "Fees?", "execute_inline": True}).json()["run_id"]
            self.assertEqual(client.get(f"/api/v1/workspaces/ws_b/runs/{run_b}").json()["status"], "failed")
            run_a = client.post("/api/v1/workspaces/ws_a/runs", json={"question": "Fees?", "execute_inline": True}).json()["run_id"]
            self.assertEqual(client.get(f"/api/v1/workspaces/ws_a/runs/{run_a}").json()["status"], "completed")
            self.assertEqual(container.routing_service._api_keys.get(ProviderId.OPENAI), "")

            restarted = build_container(artifact_root=td)
            create_app(restarted, session_factory=client.app.state.session_factory)
            self.assertEqual(restarted.routing_service.api_key_for(ProviderId.OPENAI, "ws_a"), "sk-ws-a-only")
            self.assertEqual(restarted.routing_service.api_key_for(ProviderId.OPENAI, "ws_b"), "")


if __name__ == "__main__":
    unittest.main()
