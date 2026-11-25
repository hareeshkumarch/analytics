import dataclasses
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container
from app.domain.limits import DEFAULT_DATA_LIMITS

CSV = b"order_id,amount_inr\n001,100.00\n002,250.00\n"


class TestUploads(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(build_container(artifact_root=self._td.name)))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _upload(self, workspace: str = "ws1", data: bytes = CSV):
        return self.client.post(
            f"/api/v1/workspaces/{workspace}/uploads", content=data, headers={"Content-Type": "application/octet-stream"}
        )

    def _post(self, step: str, upload_id: str, workspace: str = "ws1"):
        body = {"family": "uploaded_file", "config": {"format": "csv", "table_name": "orders", "upload_id": upload_id}}
        if step == "extract":
            body["selected_tables"] = ["orders"]
        return self.client.post(f"/api/v1/workspaces/{workspace}/connectors/{step}", json=body)

    def test_one_upload_serves_discover_and_extract_then_is_removed(self) -> None:
        created = self._upload()
        self.assertEqual(created.status_code, 201)
        upload_id = created.json()["upload_id"]
        self.assertEqual(created.json()["byte_size"], len(CSV))
        discovered = self._post("discover", upload_id)
        self.assertEqual(discovered.status_code, 200, discovered.text)
        extracted = self._post("extract", upload_id)
        self.assertEqual(extracted.status_code, 201, extracted.text)
        self.assertEqual(extracted.json()["snapshot"]["total_rows"], 2)
        self.assertEqual(self._post("extract", upload_id).status_code, 404)

    def test_reuploading_a_dataset_creates_its_next_version(self) -> None:
        first = self._post("extract", self._upload().json()["upload_id"]).json()["snapshot"]
        second = self._post("extract", self._upload().json()["upload_id"]).json()["snapshot"]
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertEqual(second["version"], 2)

    def test_uploads_are_scoped_to_their_workspace(self) -> None:
        upload_id = self._upload("ws1").json()["upload_id"]
        self.assertEqual(self._post("discover", upload_id, workspace="ws2").status_code, 404)

    def test_oversized_empty_and_malformed_uploads_are_rejected(self) -> None:
        with patch("app.api.routes.connectors.DEFAULT_DATA_LIMITS", dataclasses.replace(DEFAULT_DATA_LIMITS, max_uploaded_file_bytes=10)):
            self.assertEqual(self._upload().status_code, 413)
        self.assertEqual(self._upload(data=b"").status_code, 422)
        self.assertEqual(self._post("discover", "upl_../../etc").status_code, 422)


if __name__ == "__main__":
    unittest.main()
