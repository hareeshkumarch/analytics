import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.adapters.persistence.pg_repositories import PgBudgetLedger
from app.api.main import create_app
from app.bootstrap.container import build_container


class TestUsageTotals(unittest.TestCase):
    def test_batched_totals_match_per_run_totals_and_since_filters(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            app = create_app(container)
            ledger = PgBudgetLedger(app.state.session_factory)
            container.budget_ledger = ledger
            client = TestClient(app)
            ids = [
                client.post("/api/v1/workspaces/ws1/runs", json={"question": f"q{i}", "background_async": False}).json()["run_id"]
                for i in range(3)
            ]
            first = ledger.reserve(ids[0], "op1", "a1", 1000, Decimal("0.01"), False, None)
            ledger.settle(first.reservation_id, 800, Decimal("0.008"))
            ledger.reserve(ids[0], "op2", "a2", 500, Decimal("0.005"), False, None)
            ledger.reserve(ids[1], "op3", "a3", 200, None, True, None)
            batched = ledger.get_runs_totals(ids)
            for run_id in ids:
                single = ledger.get_run_totals(run_id)
                self.assertEqual(
                    {k: str(v) for k, v in batched[run_id].items()},
                    {k: str(v) for k, v in single.items()},
                )
            self.assertEqual(batched[ids[0]]["total_tokens"], 1300)
            self.assertTrue(batched[ids[0]]["incomplete"])
            self.assertEqual(batched[ids[2]]["used_attempts"], 0)
            usage = client.get("/api/v1/workspaces/ws1/usage").json()
            self.assertEqual(usage["run_count"], 3)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            self.assertEqual(client.get("/api/v1/workspaces/ws1/usage", params={"since": future}).json()["run_count"], 0)


if __name__ == "__main__":
    unittest.main()
