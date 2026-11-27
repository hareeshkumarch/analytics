import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routes import usage
from app.application.services.usage_summary import summarize_usage
from app.bootstrap.container import build_container


class TestUsageTotals(unittest.TestCase):
    def test_totals_cover_every_run_in_the_window_while_the_list_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td))
            client = TestClient(app)
            for i in range(5):
                app.state.repo_runs.create_run(
                    run_id=f"run_u{i}", workspace_id="ws1", conversation_id=f"c{i}", mode="deep" if i % 2 else "quick",
                    status="completed" if i < 4 else "failed", question=f"q{i}",
                )
            with patch.object(usage, "RUN_PAGE_MAX", 3):
                body = client.get("/api/v1/workspaces/ws1/usage").json()
        self.assertEqual(body["run_count"], 5)
        self.assertEqual(len(body["runs"]), 3)
        self.assertTrue(body["runs_truncated"])
        summary = body["summary"]
        self.assertEqual((summary["runs"], summary["finished"], summary["completed"]), (5, 5, 4))
        self.assertEqual({m["mode"]: m["runs"] for m in summary["by_mode"]}, {"quick": 3, "deep": 2})

    def test_summary_adds_cost_tokens_and_unknown_modes(self) -> None:
        totals = {
            "a": {"total_tokens": 10, "total_cost_usd": "0.5", "used_attempts": 2, "incomplete": False},
            "b": {"total_tokens": 5, "total_cost_usd": "0.25", "used_attempts": 1, "incomplete": True},
        }
        summary = summarize_usage([("a", "quick", "completed"), ("b", "batch", "running")], totals)
        self.assertEqual((summary["tokens"], Decimal(summary["cost_usd"]), summary["attempts"], summary["incomplete"]), (15, Decimal("0.75"), 3, 1))
        self.assertEqual([m["mode"] for m in summary["by_mode"]], ["quick", "deep", "batch"])
        self.assertEqual((summary["finished"], summary["completed"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
