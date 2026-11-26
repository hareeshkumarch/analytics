import base64
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container

CSV = base64.b64encode(b"order_id,amount\n001,100\n002,50\n").decode("ascii")


class TestProfileAndPreviewDefaultToFirstTable(unittest.TestCase):
    def test_table_parameter_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client = TestClient(create_app(build_container(artifact_root=td)))
            sid = client.post(
                "/api/v1/workspaces/ws1/connectors/extract",
                json={"family": "uploaded_file", "config": {"format": "csv", "table_name": "orders", "raw_base64": CSV}, "selected_tables": ["orders"]},
            ).json()["snapshot"]["snapshot_id"]
            profile = client.get(f"/api/v1/workspaces/ws1/snapshots/{sid}/profile")
            self.assertEqual(profile.status_code, 200)
            self.assertEqual(profile.json()["table"], "orders")
            preview = client.get(f"/api/v1/workspaces/ws1/snapshots/{sid}/preview?limit=5")
            self.assertEqual((preview.status_code, preview.json()["table"]), (200, "orders"))


if __name__ == "__main__":
    unittest.main()
