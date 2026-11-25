import base64
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.adapters.persistence.orm_models import WorkspaceRow
from app.adapters.persistence.pg_repositories import PgWorkspaceQuota
from app.api.main import create_app
from app.bootstrap.container import build_container
from app.domain.errors import LimitExceededError
from app.domain.limits import DataLimits

CSV = base64.b64encode(b"order_id,amount\n001,100\n002,250\n").decode("ascii")


def _retained(sf, workspace_id: str) -> int:
    with sf() as db:
        row = db.get(WorkspaceRow, workspace_id)
        return int(row.retained_raw_bytes) if row else 0


class TestWorkspaceQuota(unittest.TestCase):
    def test_limit_is_enforced_atomically_and_release_never_goes_negative(self) -> None:
        sf = create_session_factory(create_db_engine())
        quota = PgWorkspaceQuota(sf, DataLimits(max_workspace_retained_raw_bytes=1000))

        def reserve(_: int) -> bool:
            try:
                quota.reserve_workspace_bytes("ws_quota", 100)
                return True
            except LimitExceededError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(15)))
        self.assertEqual(results.count(True), 10)
        self.assertEqual(_retained(sf, "ws_quota"), 1000)
        quota.release_workspace_bytes("ws_quota", 5000)
        self.assertEqual(_retained(sf, "ws_quota"), 0)

    def test_imports_and_deletes_survive_a_restart(self) -> None:
        sf = create_session_factory(create_db_engine("sqlite:///:memory:"))
        with tempfile.TemporaryDirectory() as td:
            client = TestClient(create_app(build_container(artifact_root=td), session_factory=sf))
            snap = client.post(
                "/api/v1/workspaces/ws1/connectors/extract",
                json={"family": "uploaded_file", "config": {"format": "csv", "table_name": "orders", "raw_base64": CSV}, "selected_tables": ["orders"]},
            ).json()["snapshot"]
            used = _retained(sf, "ws1")
            self.assertEqual(used, snap["total_normalized_bytes"])
            restarted = TestClient(create_app(build_container(artifact_root=td), session_factory=sf))
            self.assertEqual(restarted.delete(f"/api/v1/workspaces/ws1/snapshots/{snap['snapshot_id']}").status_code, 200)
            self.assertEqual(_retained(sf, "ws1"), 0)

    def test_failed_save_releases_the_reservation(self) -> None:
        sf = create_session_factory(create_db_engine("sqlite:///:memory:"))
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td), session_factory=sf)
            client = TestClient(app, raise_server_exceptions=False)
            with patch.object(app.state.repo_snapshots, "save_snapshot", side_effect=RuntimeError("boom")):
                res = client.post(
                    "/api/v1/workspaces/ws1/connectors/extract",
                    json={"family": "uploaded_file", "config": {"format": "csv", "table_name": "orders", "raw_base64": CSV}, "selected_tables": ["orders"]},
                )
            self.assertGreaterEqual(res.status_code, 500)
            self.assertEqual(_retained(sf, "ws1"), 0)


if __name__ == "__main__":
    unittest.main()
