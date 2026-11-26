import base64
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container
from app.domain.providers import ProviderId
from app.jobs import runner
from tests.test_tool_loop import ToolCallingGateway


def _csv(rows: str) -> str:
    return base64.b64encode(f"region,amount\n{rows}".encode()).decode("ascii")


class TestRunStaysOnItsSnapshot(unittest.TestCase):
    def test_reimport_after_the_run_was_created_does_not_change_its_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td))
            client = TestClient(app)

            def import_version(rows: str) -> str:
                made = client.post(
                    "/api/v1/workspaces/ws1/connectors/extract",
                    json={"family": "uploaded_file", "config": {"format": "csv", "table_name": "orders", "raw_base64": _csv(rows)}, "selected_tables": ["orders"]},
                ).json()
                sid = made["snapshot"]["snapshot_id"]
                client.post(f"/api/v1/workspaces/ws1/semantics/{sid}/approve", json={"expected_version": made["semantics"]["version"]})
                return sid

            v1 = import_version("north,10\nsouth,5\n")
            run = client.post("/api/v1/workspaces/ws1/runs", json={"question": "Sales by region?", "mode": "quick", "background_async": False}).json()
            v2 = import_version("north,999\nsouth,888\n")
            self.assertNotEqual(v1, v2)

            def build():
                c = build_container(artifact_root=td)
                c.providers.register(ToolCallingGateway())
                c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
                return c

            with patch.object(runner, "get_worker_session_factory", return_value=app.state.session_factory), patch.object(runner, "build_container", build):
                result = runner.execute_persisted_run(run["run_id"])
            self.assertEqual(result["status"], "completed")
            stored = client.get(f"/api/v1/workspaces/ws1/runs/{run['run_id']}").json()
            self.assertEqual(stored["snapshot_id"], v1)
            items = client.get(f"/api/v1/workspaces/ws1/runs/{run['run_id']}/evidence").json()["items"]
            query = [e for e in items if e.get("operation") == "aggregate"]
            self.assertTrue(query)
            self.assertEqual({e["snapshot_id"] for e in query}, {v1})
            rows = client.get(f"/api/v1/workspaces/ws1/runs/{run['run_id']}/evidence/{query[0]['evidence_id']}").json()["rows"]
            self.assertIn(["north", 10], [list(r) for r in rows])


if __name__ == "__main__":
    unittest.main()
