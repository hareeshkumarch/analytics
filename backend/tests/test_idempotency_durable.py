import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container

BODY = {"question": "q", "background_async": False}


class TestDurableIdempotency(unittest.TestCase):
    def test_key_survives_restart_and_is_scoped_to_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env = patch.dict(os.environ, {"TESTING": "0", "DATABASE_URL": f"sqlite:///{td}/app.db"})
            env.start()
            self.addCleanup(env.stop)
            first = TestClient(create_app(build_container(artifact_root=td)))
            run_id = first.post("/api/v1/workspaces/ws1/runs", headers={"Idempotency-Key": "k"}, json=BODY).json()["run_id"]

            restarted = TestClient(create_app(build_container(artifact_root=td)))
            again = restarted.post("/api/v1/workspaces/ws1/runs", headers={"Idempotency-Key": "k"}, json=BODY)
            self.assertEqual(again.json()["run_id"], run_id)
            conflict = restarted.post(
                "/api/v1/workspaces/ws1/runs", headers={"Idempotency-Key": "k"}, json={**BODY, "question": "other"}
            )
            self.assertEqual(conflict.status_code, 409)
            other_ws = restarted.post("/api/v1/workspaces/ws2/runs", headers={"Idempotency-Key": "k"}, json=BODY)
            self.assertNotEqual(other_ws.json()["run_id"], run_id)


if __name__ == "__main__":
    unittest.main()
