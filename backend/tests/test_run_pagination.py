import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container


class TestRunPagination(unittest.TestCase):
    def test_runs_are_paged_newest_first_with_a_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client = TestClient(create_app(build_container(artifact_root=td)))
            created = [
                client.post("/api/v1/workspaces/ws1/runs", json={"question": f"q{i}", "background_async": False}).json()["run_id"]
                for i in range(5)
            ]
            seen: list[str] = []
            cursor = None
            pages = 0
            while True:
                params = {"limit": 2, **({"before": cursor} if cursor else {})}
                body = client.get("/api/v1/workspaces/ws1/runs", params=params).json()
                pages += 1
                self.assertLessEqual(len(body["runs"]), 2)
                seen.extend(r["run_id"] for r in body["runs"])
                cursor = body["next_before"]
                if cursor is None:
                    break
            self.assertEqual(pages, 3)
            self.assertEqual(seen, list(reversed(created)))
            self.assertEqual(client.get("/api/v1/workspaces/ws1/runs", params={"limit": 501}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
