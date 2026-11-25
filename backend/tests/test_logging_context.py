import json
import logging
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container
from app.bootstrap.logging_setup import ContextFilter, JsonFormatter, clean_request_id, run_context


class TestRequestIds(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(build_container(artifact_root=self._td.name)))

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_request_id_is_echoed_and_used_in_errors_and_logs(self) -> None:
        with self.assertLogs("ledgerline.access", level="INFO") as logs:
            res = self.client.get("/api/v1/workspaces/ws1/snapshots/snap_missing/profile?table=x", headers={"X-Request-ID": "abc-123"})
        self.assertEqual(res.headers["x-request-id"], "abc-123")
        self.assertIn("/snapshots/snap_missing/profile 404", logs.output[0])
        bad = self.client.post("/api/v1/workspaces/ws1/runs", json={"unexpected": 1}, headers={"X-Request-ID": "req-9"})
        self.assertEqual(bad.json()["request_id"], "req-9")

    def test_unsafe_request_ids_are_replaced(self) -> None:
        res = self.client.get("/api/v1/health", headers={"X-Request-ID": "bad id\nwith newline"})
        self.assertRegex(res.headers["x-request-id"], r"^[0-9a-f]{12}$")
        self.assertIsNone(clean_request_id("x" * 65))


class TestJsonLogs(unittest.TestCase):
    def test_json_lines_carry_run_id_and_exceptions(self) -> None:
        record = logging.LogRecord("ledgerline", logging.ERROR, __file__, 1, "step %s failed", ("plan",), None)
        with run_context("run_42"):
            ContextFilter().filter(record)
        try:
            raise ValueError("boom")
        except ValueError:
            record.exc_info = sys.exc_info()
        line = json.loads(JsonFormatter().format(record))
        self.assertEqual((line["message"], line["run_id"], line["level"]), ("step plan failed", "run_42", "ERROR"))
        self.assertIn("ValueError: boom", line["exception"])
        self.assertNotIn("request_id", line)


if __name__ == "__main__":
    unittest.main()
