import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import support
from app.api.main import create_app
from app.bootstrap.container import build_container


class TestSseStreamMaxAge(unittest.TestCase):
    def test_stream_for_a_run_that_never_finishes_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td))
            client = TestClient(app)
            run_id = client.post(
                "/api/v1/workspaces/ws1/runs", json={"question": "q", "mode": "quick", "background_async": False}
            ).json()["run_id"]
            with patch.object(support, "SSE_MAX_STREAM_SECONDS", 0.5):
                res = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}/events?after=0")
            self.assertEqual(res.status_code, 200)
            self.assertIn("run.status", res.text)
            self.assertNotIn("run.finished", res.text)


if __name__ == "__main__":
    unittest.main()
