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

    def test_run_list_carries_only_the_answer_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td))
            client = TestClient(app)
            run_id = client.post("/api/v1/workspaces/ws1/runs", json={"question": "q", "background_async": False}).json()["run_id"]
            full = {"answer_id": "ans_1", "summary_markdown": "Total is 350", "claims": [{"x": 1}], "limitations": ["l"]}
            with app.state.session_factory() as db, db.begin():
                from app.adapters.persistence.orm_models import RunRow

                db.get(RunRow, run_id).answer_json = {**full, "question": "q"}
            listed = client.get("/api/v1/workspaces/ws1/runs").json()["runs"][0]
            self.assertEqual(listed["answer"], {"answer_id": "ans_1", "summary_markdown": "Total is 350"})
            self.assertEqual(listed["question"], "q")


if __name__ == "__main__":
    unittest.main()
