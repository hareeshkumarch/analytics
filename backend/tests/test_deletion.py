import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container
from app.domain.models import RunStatus, TaskRecord

CSV = b"order_id,amount_inr\n001,100.00\n002,250.00\n"
B = "/api/v1/workspaces/ws1"


class TestDeletion(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.container = build_container(artifact_root=self._td.name)
        self.app = create_app(self.container)
        self.client = TestClient(self.app)
        upload = self.client.post(f"{B}/uploads", content=CSV, headers={"Content-Type": "application/octet-stream"}).json()
        extracted = self.client.post(f"{B}/connectors/extract", json={
            "family": "uploaded_file",
            "config": {"format": "csv", "table_name": "orders", "upload_id": upload["upload_id"]},
            "selected_tables": ["orders"],
        }).json()
        self.snapshot = extracted["snapshot"]
        self.client.post(f"{B}/semantics/{self.snapshot['snapshot_id']}/approve", json={"expected_version": 1})

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run(self, status: RunStatus) -> str:
        run_id = self.client.post(f"{B}/runs", json={"question": "q", "snapshot_id": self.snapshot["snapshot_id"], "background_async": False}).json()["run_id"]
        self.app.state.repo_runs.update_status(run_id, status)
        self.app.state.runs.pop(run_id, None)
        return run_id

    def test_finished_run_is_deleted_with_its_events_and_pin(self) -> None:
        run_id = self._run(RunStatus.COMPLETED)
        self.assertEqual(self.client.post(f"{B}/pins", json={"title": "t", "question_text": "q", "run_id": run_id}).status_code, 201)
        self.assertTrue(self.app.state.repo_events.list_after(run_id, 0))
        self.container.task_store.add_tasks([
            TaskRecord(run_id=run_id, task_id="t1", plan_revision=1, kind="data_specialist", objective="o", acceptance_criteria=(),
                       dependencies=(), status="pending", attempt=0, input_fingerprint="f", version=1)
        ])
        self.assertEqual([t["task_id"] for t in self.client.get(f"{B}/runs/{run_id}/tasks").json()["tasks"]], ["t1"])
        self.assertEqual(self.client.delete(f"{B}/runs/{run_id}").status_code, 200)
        self.assertEqual(self.container.task_store.list_for_run(run_id), [])
        self.assertEqual(self.client.get(f"{B}/runs/{run_id}").status_code, 404)
        self.assertEqual(self.app.state.repo_events.list_after(run_id, 0), ())
        self.assertEqual(self.client.get(f"{B}/pins").json()["pins"], [])
        self.assertEqual(self.client.delete(f"{B}/runs/{run_id}").status_code, 404)

    def test_active_run_must_be_cancelled_first(self) -> None:
        run_id = self._run(RunStatus.RUNNING)
        self.assertEqual(self.client.delete(f"{B}/runs/{run_id}").status_code, 409)

    def test_pinned_dataset_needs_force_and_its_files_are_removed(self) -> None:
        run_id = self._run(RunStatus.COMPLETED)
        self.client.post(f"{B}/pins", json={"title": "t", "question_text": "q", "run_id": run_id})
        snap_id = self.snapshot["snapshot_id"]
        artifact_ids = list(self.snapshot["table_artifacts"].values())
        self.assertTrue(all(self.container.artifacts.exists(a) for a in artifact_ids))
        self.assertEqual(self.client.delete(f"{B}/snapshots/{snap_id}").status_code, 409)
        self.assertEqual(self.client.delete(f"{B}/snapshots/{snap_id}", params={"force": "true"}).status_code, 200)
        self.assertFalse(any(self.container.artifacts.exists(a) for a in artifact_ids))
        self.assertEqual(self.client.get(f"{B}/snapshots").json()["snapshots"], [])
        self.assertEqual(self.client.get(f"{B}/runs/{run_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"{B}/snapshots/{snap_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
