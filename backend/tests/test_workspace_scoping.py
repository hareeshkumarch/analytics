import base64
import tempfile
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container
from app.domain.models import Answer

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

    def test_old_exports_are_removed_when_a_new_one_is_made(self) -> None:
        import os
        import time

        run_id = self.client.post("/api/v1/workspaces/ws_a/runs", json={"question": "q", "mode": "quick"}).json()["run_id"]
        first = self.client.post(
            "/api/v1/workspaces/ws_a/exports", json={"run_id": run_id, "format": "html", "summary_markdown": "x"}
        ).json()
        path = os.path.join(self._td.name, "published", first["artifact_id"])
        old = time.time() - 8 * 86400
        os.utime(path, (old, old))
        self.client.post("/api/v1/workspaces/ws_a/exports", json={"run_id": run_id, "format": "html", "summary_markdown": "y"})
        self.assertEqual(self.client.get(first["download_url"]).status_code, 404)


if __name__ == "__main__":
    unittest.main()
