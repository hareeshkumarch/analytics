import os
import unittest
import uuid
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapters.persistence.orm_models import Base
from app.adapters.persistence.pg_repositories import PgBudgetLedger, PgRunRepository, PgToolExecutionLedger, PgWorkspaceSettings
from app.application.ports.tools import ToolExecution
from app.domain.errors import ValidationError

PG_URL = os.environ.get("TEST_POSTGRES_URL", "")


@unittest.skipUnless(PG_URL, "set TEST_POSTGRES_URL to run against a real PostgreSQL")
class TestRepositoriesOnPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        engine = create_engine(PG_URL)
        Base.metadata.create_all(engine)
        cls.sf = sessionmaker(bind=engine)

    def _run(self) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        PgRunRepository(self.sf).create_run(run_id=run_id, workspace_id="ws_pg", conversation_id=f"c_{run_id}", mode="quick", status="queued", question="q")
        return run_id

    def test_tool_execution_ledger_round_trip(self) -> None:
        run_id = self._run()
        ledger = PgToolExecutionLedger(self.sf)
        ledger.record(ToolExecution("tex_" + uuid.uuid4().hex[:12], run_id, "t1", "run_query_ir", "1", "abc", "failed", "TIMEOUT", 15000, None))
        (row,) = ledger.list_for_runs([run_id])
        self.assertEqual((row.status, row.error_code, row.duration_ms), ("failed", "TIMEOUT", 15000))
        self.assertIsNotNone(row.created_at.tzinfo)

    def test_workspace_timezone(self) -> None:
        settings = PgWorkspaceSettings(self.sf)
        ws = f"ws_{uuid.uuid4().hex[:8]}"
        self.assertEqual(settings.get_business_timezone(ws), "UTC")
        self.assertEqual(settings.set_business_timezone(ws, "Asia/Kolkata"), "Asia/Kolkata")
        self.assertEqual(settings.get_business_timezone(ws), "Asia/Kolkata")
        with self.assertRaises(ValidationError):
            settings.set_business_timezone(ws, "Nowhere/Land")

    def test_budget_release_and_ceiling(self) -> None:
        run_id = self._run()
        ledger = PgBudgetLedger(self.sf)
        failed = ledger.reserve(run_id, "op1", "a1", 4000, Decimal("1.50"), False, Decimal("2.00"))
        ledger.release_unresolved(failed.reservation_id, "timeout")
        ledger.reserve(run_id, "op2", "a2", 4000, Decimal("1.50"), False, Decimal("2.00"))
        totals = ledger.get_run_totals(run_id)
        self.assertEqual((totals["total_tokens"], Decimal(str(totals["total_cost_usd"])), totals["incomplete"]), (4000, Decimal("1.50"), True))


if __name__ == "__main__":
    unittest.main()
