import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.adapters.persistence.pg_repositories import PgArtifactRepository, PgRestoreReconciliation
from app.api.main import create_app
from app.application.services.restore_reconciliation import reconcile_after_restore
from app.bootstrap.container import build_container
from app.domain.models import Answer

A = "/api/v1/workspaces/ws1"


class TestReconcileAfterRestore(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.container = build_container(artifact_root=self._td.name)
        self.app = create_app(self.container)
        self.client = TestClient(self.app)
        sf = self.app.state.session_factory
        self.state = PgRestoreReconciliation(sf)
        self.index = PgArtifactRepository(sf)
        imported = self.client.post(f"{A}/samples/inr_acceptance/import").json()["snapshot"]
        self.snapshot_id = imported["snapshot_id"]
        self.tables = self.app.state.repo_snapshots.get_snapshot(self.snapshot_id)["table_artifacts"]

    def tearDown(self) -> None:
        self._td.cleanup()

    def _reconcile(self, requeue_runs: bool = True):
        return reconcile_after_restore(
            self.state, self.index, self.container.artifacts, now=datetime.now(timezone.utc), requeue_runs=requeue_runs
        )

    def _export(self) -> str:
        run_id = self.app.state.repo_runs.create_run(
            run_id="run_done", workspace_id="ws1", conversation_id="c_done", mode="quick", status="queued", question="q"
        )["run_id"]
        self.app.state.repo_runs.save_answer(Answer(
            answer_id="ans_1", run_id=run_id, summary_markdown="Fees were 100.", claims=(), charts=(), limitations=(),
            snapshot_id=self.snapshot_id, semantic_version_id="sem", created_at=datetime.now(timezone.utc),
        ))
        return self.client.post(f"{A}/exports", json={"run_id": run_id, "format": "html"}).json()["artifact_id"]

    def test_a_consistent_restore_has_nothing_to_do(self) -> None:
        report = self._reconcile()
        self.assertFalse(report.changed, report.summary())
        self.assertIn("Nothing to reconcile", report.summary())

    def test_missing_files_stale_exports_and_interrupted_runs_are_repaired_once(self) -> None:
        export_id = self._export()
        self.container.artifacts.delete(self.tables["fees"])
        self.container.artifacts.delete(export_id)
        repo_runs = self.app.state.repo_runs
        for run_id, status in (("run_q", "queued"), ("run_r", "running")):
            repo_runs.create_run(run_id=run_id, workspace_id="ws1", conversation_id=f"c_{run_id}", mode="quick", status="queued", question="q")
            if status == "running":
                repo_runs.claim_run(run_id)
        orphan = self.container.artifacts.stage_bytes("job_orphan", "x.bin", b"x", "application/octet-stream")
        self.container.artifacts.publish_staged("job_orphan", orphan, "art_orphan")
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).timestamp()
        os.utime(self.container.artifacts._published_path("art_orphan"), (old, old))

        report = self._reconcile()
        self.assertEqual(report.datasets_missing_files, {self.snapshot_id: ["fees"]})
        self.assertEqual(report.exports_removed, [export_id])
        self.assertEqual(sorted(report.runs_requeued), ["run_q", "run_r"])
        self.assertEqual(report.orphans_removed, 1)
        self.assertIsNone(self.index.get_export(export_id))
        self.assertEqual(self.state.active_runs_without_job(), [])
        self.assertIn("missing files for fees", report.summary())

        self.app.state.snapshots.clear()
        refused = self.client.post(f"{A}/runs", json={"question": "Total fees?", "snapshot_id": self.snapshot_id})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("fees", refused.json()["detail"])
        listed = self.client.get(f"{A}/snapshots").json()["snapshots"]
        self.assertEqual(next(s for s in listed if s["snapshot_id"] == self.snapshot_id)["unavailable_tables"], ["fees"])

        again = self._reconcile()
        self.assertFalse(again.changed, again.summary())

    def test_runs_are_left_to_the_api_in_inline_mode_and_restored_files_clear_the_flag(self) -> None:
        self.app.state.repo_runs.create_run(run_id="run_q", workspace_id="ws1", conversation_id="c_q", mode="quick", status="queued", question="q")
        data = self.container.artifacts.read_bytes(self.tables["fees"])
        self.container.artifacts.delete(self.tables["fees"])
        self.assertEqual(self._reconcile(requeue_runs=False).runs_requeued, [])
        self.assertEqual(self.state.active_runs_without_job(), ["run_q"])
        staged = self.container.artifacts.stage_bytes("job_back", "fees.arrow", data, "application/octet-stream")
        self.container.artifacts.publish_staged("job_back", staged, self.tables["fees"])
        report = self._reconcile(requeue_runs=False)
        self.assertEqual(report.datasets_recovered, [self.snapshot_id])
        self.app.state.snapshots.clear()
        self.assertNotIn("unavailable_tables", self.app.state.repo_snapshots.get_snapshot(self.snapshot_id))


if __name__ == "__main__":
    unittest.main()
