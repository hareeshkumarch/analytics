import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container


class TestSampleData(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(build_container(artifact_root=self._td.name)))

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_sample_imports_as_one_dataset_with_four_tables(self) -> None:
        res = self.client.post("/api/v1/workspaces/ws1/samples/inr_acceptance/import")
        self.assertEqual(res.status_code, 201, res.text)
        snap = res.json()["snapshot"]
        self.assertTrue(res.json()["semantics"]["approved"])
        listed = self.client.get("/api/v1/workspaces/ws1/snapshots").json()["snapshots"]
        self.assertTrue(next(s for s in listed if s["snapshot_id"] == snap["snapshot_id"])["approved"])
        self.assertEqual(sorted(snap["tables"]), ["fees", "orders", "payouts", "refunds"])
        self.assertEqual(snap["display_name"], "Sample shop payments (INR)")
        orders = next(t for t in snap["table_details"] if t["name"] == "orders")
        order_id = next(c for c in orders["columns"] if c["name"] == "order_id")
        self.assertTrue(order_id["leading_zero_id"])
        again = self.client.post("/api/v1/workspaces/ws1/samples/inr_acceptance/import").json()["snapshot"]
        self.assertEqual((again["source_id"], again["version"]), (snap["source_id"], 2))

    def test_uploads_keep_the_name_the_client_sends(self) -> None:
        def extract(name: str | None) -> dict:
            body = {"family": "uploaded_file", "config": {"format": "csv", "raw_base64": "YSxiCjEsMgo=", "table_name": "t"}, "selected_tables": ["t"]}
            res = self.client.post("/api/v1/workspaces/ws1/connectors/extract", json={**body, **({"display_name": name} if name is not None else {})})
            self.assertEqual(res.status_code, 201, res.text)
            return res.json()["snapshot"]

        self.assertEqual(extract("  march   orders.csv ")["display_name"], "march orders.csv")
        self.assertIsNone(extract(None)["display_name"])
        listed = self.client.get("/api/v1/workspaces/ws1/snapshots").json()["snapshots"]
        self.assertIn("march orders.csv", [s.get("display_name") for s in listed])

    def test_unknown_samples_and_client_supplied_files_are_rejected(self) -> None:
        self.assertEqual(self.client.post("/api/v1/workspaces/ws1/samples/..%2Fetc/import").status_code, 404)
        self.assertEqual(self.client.post("/api/v1/workspaces/ws1/samples/other/import").status_code, 422)
        res = self.client.post(
            "/api/v1/workspaces/ws1/connectors/extract",
            json={"family": "uploaded_file", "config": {"format": "csv", "files": [["x", "a,b"]]}, "selected_tables": ["x"]},
        )
        self.assertNotEqual(res.status_code, 201)


if __name__ == "__main__":
    unittest.main()
