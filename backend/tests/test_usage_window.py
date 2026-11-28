import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routes import usage
from app.application.services.usage_summary import bucket_usage, summarize_usage
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

    def test_sorting_filtering_and_paging_cover_the_whole_window(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td))
            client = TestClient(app)
            for i in range(5):
                app.state.repo_runs.create_run(
                    run_id=f"run_s{i}", workspace_id="ws1", conversation_id=f"c{i}", mode="deep" if i % 2 else "quick",
                    status="completed" if i < 4 else "failed", question=f"refunds {i}" if i < 2 else f"revenue {i}",
                )
            tokens = {"run_s0": 50, "run_s1": 10, "run_s2": 40, "run_s3": 20, "run_s4": 30}
            totals = {
                run_id: {"run_id": run_id, "used_attempts": 1, "total_tokens": n, "total_cost_usd": str(n / 100), "incomplete": False}
                for run_id, n in tokens.items()
            }
            with patch.object(app.state.container.budget_ledger, "get_runs_totals", lambda ids: totals):
                top = client.get("/api/v1/workspaces/ws1/usage", params={"sort": "tokens", "limit": 2}).json()
                rest = client.get("/api/v1/workspaces/ws1/usage", params={"sort": "tokens", "limit": 2, "offset": 2}).json()
                cheapest = client.get("/api/v1/workspaces/ws1/usage", params={"sort": "cost", "order": "asc", "limit": 1}).json()
                failed = client.get("/api/v1/workspaces/ws1/usage", params={"status": "failed"}).json()
                searched = client.get("/api/v1/workspaces/ws1/usage", params={"q": "REFUNDS", "mode": "quick"}).json()
        self.assertEqual([r["run_id"] for r in top["runs"]], ["run_s0", "run_s2"])
        self.assertEqual((top["matched_count"], top["runs_truncated"]), (5, True))
        self.assertEqual([r["run_id"] for r in rest["runs"]], ["run_s4", "run_s3"])
        self.assertEqual([r["run_id"] for r in cheapest["runs"]], ["run_s1"])
        self.assertEqual(([r["run_id"] for r in failed["runs"]], failed["matched_count"], failed["run_count"]), (["run_s4"], 1, 5))
        self.assertEqual([r["run_id"] for r in searched["runs"]], ["run_s0"])
        # The summary and chart ignore the table's filters.
        self.assertEqual(failed["summary"]["runs"], 5)
        self.assertEqual(sum(b["runs"] for b in failed["buckets"]), 5)
        self.assertEqual(sum(b["tokens"] for b in top["buckets"]), 150)
        self.assertEqual(client.get("/api/v1/workspaces/ws1/usage", params={"sort": "bogus"}).status_code, 422)

    def test_buckets_follow_the_viewers_clock(self) -> None:
        rows = [
            {"run_id": "a", "mode": "quick", "status": "completed", "created_at": "2026-03-01T23:30:00+00:00", "question": "", "error": None},
            {"run_id": "b", "mode": "quick", "status": "completed", "created_at": "2026-03-02T00:30:00+00:00", "question": "", "error": None},
        ]
        totals = {"a": {"total_tokens": 1, "total_cost_usd": "0.1"}, "b": {"total_tokens": 2, "total_cost_usd": "0.2"}}
        utc = bucket_usage(rows, totals)
        self.assertEqual([(b["start"], b["runs"]) for b in utc], [("2026-03-01T00:00:00+00:00", 1), ("2026-03-02T00:00:00+00:00", 1)])
        # One hour east of UTC both runs fall on the 2nd, whose local midnight is 23:00 UTC on the 1st.
        east = bucket_usage(rows, totals, tz_offset_minutes=60)
        self.assertEqual([(b["start"], b["runs"], b["tokens"], b["cost_usd"]) for b in east], [("2026-03-01T23:00:00+00:00", 2, 3, "0.3")])
        self.assertEqual(len(bucket_usage(rows, totals, hourly=True)), 2)

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
