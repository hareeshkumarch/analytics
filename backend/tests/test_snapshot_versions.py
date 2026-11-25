import base64
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.adapters.persistence.orm_models import SnapshotManifestRow
from app.adapters.persistence.pg_repositories import PgSnapshotRepository
from app.api.main import create_app
from app.bootstrap.container import build_container

CSV = base64.b64encode(b"order_id,amount\n001,100\n002,250\n").decode("ascii")


def _extract(client: TestClient, table_name: str = "orders", **extra):
    return client.post(
        "/api/v1/workspaces/ws1/connectors/extract",
        json={
            "family": "uploaded_file",
            "config": {"format": "csv", "table_name": table_name, "raw_base64": CSV},
            "selected_tables": [table_name],
            **extra,
        },
    )


class TestServerAllocatesSnapshotVersions(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(build_container(artifact_root=self._td.name)))

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_reimporting_a_source_creates_the_next_version(self) -> None:
        first = _extract(self.client).json()["snapshot"]
        second = _extract(self.client).json()["snapshot"]
        other = _extract(self.client, table_name="refunds").json()["snapshot"]
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertNotEqual(other["source_id"], first["source_id"])
        self.assertEqual(other["version"], 1)
        listed = self.client.get("/api/v1/workspaces/ws1/snapshots").json()["snapshots"]
        self.assertEqual(sorted(s["version"] for s in listed if s["source_id"] == first["source_id"]), [1, 2])

    def test_import_remembers_non_secret_form_values_for_reimport(self) -> None:
        form = {
            "connector_id": "csv",
            "values": {
                "table_name": "orders",
                "link": "https://user:pw@example.com/data.csv?sig=abc",
                "api_key": "leak",
                "header": True,
                "account": "kept-unless-credential",
            },
        }
        snap = _extract(self.client, form=form, credentials={"account": "x"}).json()["snapshot"]
        self.assertEqual(
            snap["reimport"],
            {
                "connector_id": "csv",
                "values": {"table_name": "orders", "link": "https://example.com/data.csv", "header": True},
                "selected_tables": ["orders"],
            },
        )
        again = _extract(self.client, source_id=snap["source_id"], table_name="orders").json()["snapshot"]
        self.assertEqual((again["source_id"], again["version"]), (snap["source_id"], 2))
        listed = self.client.get("/api/v1/workspaces/ws1/snapshots").json()["snapshots"]
        self.assertEqual(next(s for s in listed if s["snapshot_id"] == snap["snapshot_id"])["reimport"]["connector_id"], "csv")

    def test_client_cannot_choose_the_version(self) -> None:
        self.assertEqual(_extract(self.client, version=7).status_code, 422)


class TestVersionReservationIsAtomic(unittest.TestCase):
    def test_concurrent_reservations_get_distinct_versions(self) -> None:
        repo = PgSnapshotRepository(create_session_factory(create_db_engine()))

        def reserve(_: int) -> int:
            return repo.reserve_snapshot_version("ws1", "src_concurrent", "uploaded_file", "orders", {})

        with ThreadPoolExecutor(max_workers=8) as pool:
            versions = list(pool.map(reserve, range(16)))
        self.assertEqual(sorted(versions), list(range(1, 17)))

    def test_duplicate_source_versions_are_rejected_by_the_database(self) -> None:
        sf = create_session_factory(create_db_engine("sqlite:///:memory:"))
        repo = PgSnapshotRepository(sf)
        repo.reserve_snapshot_version("ws1", "src_dup", "uploaded_file", "orders", {})
        rows = [
            SnapshotManifestRow(
                snapshot_id=f"snap_{n}",
                workspace_id="ws1",
                source_id="src_dup",
                version=1,
                schema_fingerprint="f",
                manifest_json={},
                artifact_id=f"art_{n}",
                sha256="0" * 64,
            )
            for n in (1, 2)
        ]
        with self.assertRaises(IntegrityError):
            with sf() as db, db.begin():
                db.add_all(rows)


if __name__ == "__main__":
    unittest.main()
