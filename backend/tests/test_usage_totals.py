import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.adapters.persistence.pg_repositories import PgBudgetLedger
from app.application.services.budget_service import AtomicBudgetLedger
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


class TestUsageByModel(unittest.TestCase):
    def _record(self, ledger, run_ids: list[str]) -> None:
        a = ledger.reserve(run_ids[0], "op1", "a1", 1000, Decimal("0.01"), False, None, provider_id="openai", model_id="gpt-x", role="planner")
        ledger.settle(a.reservation_id, 800, Decimal("0.008"), latency_ms=300)
        b = ledger.reserve(run_ids[1], "op2", "a2", 1000, Decimal("0.01"), False, None, provider_id="openai", model_id="gpt-x", role="analyst")
        ledger.settle(b.reservation_id, 600, Decimal("0.004"), latency_ms=500)
        ledger.reserve(run_ids[1], "op3", "a3", 400, None, True, None, provider_id="moonshot", model_id="kimi", role="analyst")
        ledger.reserve(run_ids[1], "op4", "a4", 100, Decimal("0.001"), False, None)

    def _expected(self) -> list[dict]:
        return [
            {"provider_id": "openai", "model_id": "gpt-x", "calls": 2, "unsettled_calls": 0, "total_tokens": 1400,
             "total_cost_usd": "0.012", "cost_unknown": False, "avg_latency_ms": 400},
            {"provider_id": "unknown", "model_id": "unknown", "calls": 1, "unsettled_calls": 1, "total_tokens": 100,
             "total_cost_usd": "0.001", "cost_unknown": False, "avg_latency_ms": None},
            {"provider_id": "moonshot", "model_id": "kimi", "calls": 1, "unsettled_calls": 1, "total_tokens": 400,
             "total_cost_usd": "0", "cost_unknown": True, "avg_latency_ms": None},
        ]

    def _normalize(self, rows: list[dict]) -> list[dict]:
        return [{**r, "total_cost_usd": str(Decimal(str(r["total_cost_usd"])).normalize())} for r in rows]

    def test_memory_and_database_ledgers_agree(self) -> None:
        memory = AtomicBudgetLedger()
        self._record(memory, ["r1", "r2"])
        self.assertEqual(self._normalize(memory.get_model_usage(["r1", "r2"])), self._expected())
        self.assertEqual(memory.get_model_usage(["other"]), [])
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            app = create_app(container)
            ledger = PgBudgetLedger(app.state.session_factory)
            container.budget_ledger = ledger
            client = TestClient(app)
            ids = [
                client.post("/api/v1/workspaces/ws1/runs", json={"question": f"q{i}", "background_async": False}).json()["run_id"]
                for i in range(2)
            ]
            self._record(ledger, ids)
            self.assertEqual(self._normalize(client.get("/api/v1/workspaces/ws1/usage").json()["models"]), self._expected())


class TestDatabaseLedgerTokenCeiling(unittest.TestCase):
    def test_reservations_beyond_the_run_token_limit_are_refused(self) -> None:
        from app.domain.errors import BudgetExhaustedError
        from app.domain.limits import RUN_TOKEN_LIMIT

        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td))
            ledger = PgBudgetLedger(app.state.session_factory)
            run_id = TestClient(app).post("/api/v1/workspaces/ws1/runs", json={"question": "q", "background_async": False}).json()["run_id"]
            first = ledger.reserve(run_id, "op1", "a1", RUN_TOKEN_LIMIT - 1000, None, False, None)
            with self.assertRaises(BudgetExhaustedError):
                ledger.reserve(run_id, "op2", "a2", 2000, None, False, None)
            ledger.settle(first.reservation_id, 1000, None)
            ledger.reserve(run_id, "op3", "a3", 2000, None, False, None)


class TestUnresolvedReservationsAreReleased(unittest.TestCase):
    def _check(self, ledger, run_id: str) -> None:
        failed = ledger.reserve(run_id, "op1", "a1", 4000, Decimal("1.50"), False, Decimal("2.00"))
        ledger.release_unresolved(failed.reservation_id, "provider timeout")
        totals = ledger.get_run_totals(run_id)
        self.assertEqual((totals["total_tokens"], Decimal(str(totals["total_cost_usd"]))), (0, Decimal("0")))
        self.assertTrue(totals["incomplete"])
        ledger.reserve(run_id, "op2", "a2", 4000, Decimal("1.50"), False, Decimal("2.00"))

    def test_memory_ledger(self) -> None:
        self._check(AtomicBudgetLedger(), "run_mem")

    def test_database_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td))
            run_id = TestClient(app).post("/api/v1/workspaces/ws1/runs", json={"question": "q", "background_async": False}).json()["run_id"]
            ledger = PgBudgetLedger(app.state.session_factory)
            self._check(ledger, run_id)
            self.assertEqual(Decimal(str(ledger.get_runs_totals([run_id])[run_id]["total_cost_usd"])), Decimal("1.50"))

    def test_memory_settle_keeps_cost_when_none_was_reserved(self) -> None:
        ledger = AtomicBudgetLedger()
        res = ledger.reserve("r", "op", "a", 100, None, False, None)
        ledger.settle(res.reservation_id, 90, Decimal("0.25"))
        self.assertEqual(ledger.get_run_totals("r")["total_cost_usd"], "0.25")


if __name__ == "__main__":
    unittest.main()
