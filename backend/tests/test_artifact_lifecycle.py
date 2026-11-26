import base64
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.application.services.artifact_lifecycle import sweep_artifacts
from app.bootstrap.container import build_container

CSV = base64.b64encode(b"region,amount\nnorth,10\n").decode("ascii")


class TestArtifactLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.app = create_app(build_container(artifact_root=self.root))
        self.client = TestClient(self.app)
        self.run_id = self.client.post("/api/v1/workspaces/ws1/runs", json={"question": "q", "background_async": False}).json()["run_id"]

    def tearDown(self) -> None:
        self._td.cleanup()

    def _export(self) -> dict:
        return self.client.post("/api/v1/workspaces/ws1/exports", json={"run_id": self.run_id, "format": "html", "summary_markdown": "x"}).json()

    def _sweep(self, now: datetime | None = None, grace: float = 3600):
        return sweep_artifacts(self.app.state.container.artifacts, self.app.state.repo_artifacts, now or datetime.now(timezone.utc), grace)

    def test_exports_are_recorded_and_expire(self) -> None:
        made = self._export()
        record = self.app.state.repo_artifacts.get_export(made["artifact_id"])
        self.assertEqual((record["workspace_id"], record["run_id"], record["format"], record["sha256"]), ("ws1", self.run_id, "html", made["sha256"]))
        self.assertEqual(self.client.get(made["download_url"]).status_code, 200)
        later = datetime.now(timezone.utc) + timedelta(days=8)
        result = self._sweep(now=later)
        self.assertEqual(result.expired_exports, 1)
        self.assertEqual(self.client.get(made["download_url"]).status_code, 404)
        self.assertFalse((self.root / "published" / made["artifact_id"]).exists())
        self.assertIsNone(self.app.state.repo_artifacts.get_export(made["artifact_id"]))

    def test_expired_downloads_return_404_before_the_sweep(self) -> None:
        with patch.dict(os.environ, {"EXPORT_RETENTION_DAYS": "0"}):
            made = self._export()
        self.assertEqual(self.client.get(made["download_url"]).status_code, 404)

    def test_orphans_are_removed_and_referenced_files_are_kept(self) -> None:
        made = self.client.post(
            "/api/v1/workspaces/ws1/connectors/extract",
            json={"family": "uploaded_file", "config": {"format": "csv", "table_name": "orders", "raw_base64": CSV}, "selected_tables": ["orders"]},
        ).json()
        table_files = list(made["snapshot"]["table_artifacts"].values())
        export = self._export()
        published = self.root / "published"
        orphan = published / "art_crashed__orders"
        orphan.write_bytes(b"left behind by a crash")
        fresh_orphan = published / "art_still_importing__orders"
        fresh_orphan.write_bytes(b"import in progress")
        old = time.time() - 7200
        for path in [orphan, *(published / f for f in table_files), published / export["artifact_id"]]:
            os.utime(path, (old, old))
        result = self._sweep()
        self.assertEqual(result.orphans, 1)
        self.assertFalse(orphan.exists())
        self.assertTrue(fresh_orphan.exists())
        for name in [*table_files, export["artifact_id"]]:
            self.assertTrue((published / name).exists(), name)
        self.assertEqual(self.client.get(export["download_url"]).status_code, 200)

    def test_exports_from_another_workspace_are_not_served(self) -> None:
        made = self._export()
        self.assertEqual(self.client.get(made["download_url"].replace("/ws1/", "/ws2/")).status_code, 404)


if __name__ == "__main__":
    unittest.main()
