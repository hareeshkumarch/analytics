import base64
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.persistence.orm_models import SemanticVersionRow
from app.api.main import create_app
from app.bootstrap.container import build_container

CSV = base64.b64encode(b"order_id,amount_inr\n001,100.00\n002,250.00\n").decode("ascii")


class TestSemanticApproval(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.app = create_app(build_container(artifact_root=self._td.name))
        self.client = TestClient(self.app)
        extracted = self.client.post(
            "/api/v1/workspaces/ws1/connectors/extract",
            json={
                "family": "uploaded_file",
                "config": {"format": "csv", "table_name": "orders", "raw_base64": CSV},
                "selected_tables": ["orders"],
            },
        ).json()
        self.snapshot_id = extracted["snapshot"]["snapshot_id"]
        self.proposed = extracted["semantics"]

    def tearDown(self) -> None:
        self._td.cleanup()

    def _approve(self, expected_version: int):
        return self.client.post(
            f"/api/v1/workspaces/ws1/semantics/{self.snapshot_id}/approve", json={"expected_version": expected_version}
        )

    def test_approval_keeps_the_proposed_version_as_history(self) -> None:
        approved = self._approve(1).json()
        self.assertEqual(approved["version"], 2)
        self.assertNotEqual(approved["semantic_version_id"], self.proposed["semantic_version_id"])
        with self.app.state.session_factory() as db:
            rows = db.scalars(
                select(SemanticVersionRow).where(SemanticVersionRow.snapshot_id == self.snapshot_id)
            ).all()
        self.assertEqual(sorted((r.version, r.approved) for r in rows), [(1, False), (2, True)])
        run = self.client.post(
            "/api/v1/workspaces/ws1/runs",
            json={"question": "Total?", "snapshot_id": self.snapshot_id, "background_async": False},
        ).json()
        detail = self.client.get(f"/api/v1/workspaces/ws1/runs/{run['run_id']}").json()
        self.assertEqual(detail["semantic_version_id"], approved["semantic_version_id"])

    def test_stale_approval_is_refused(self) -> None:
        self.assertEqual(self._approve(1).status_code, 200)
        self.assertEqual(self._approve(1).status_code, 409)

    def test_only_one_concurrent_approval_wins(self) -> None:
        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = sorted(pool.map(lambda _: self._approve(1).status_code, range(4)))
        self.assertEqual(codes.count(200), 1)
        self.assertTrue(all(code == 409 for code in codes if code != 200), codes)


if __name__ == "__main__":
    unittest.main()
