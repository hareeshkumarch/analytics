import csv
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.services.reconciliation import reconcile_orders_settlement
from app.domain.limits import DEEP_BUDGET_LIMITS, DEFAULT_DATA_LIMITS, QUICK_BUDGET_LIMITS
from app.domain.models import PlanDAG, TaskNode


class TestDomainLimitsAndInr(unittest.TestCase):
    def test_canonical_data_limits(self) -> None:
        self.assertEqual(DEFAULT_DATA_LIMITS.max_uploaded_file_bytes, 40_000_000)
        self.assertEqual(DEFAULT_DATA_LIMITS.max_normalized_source_bytes, 40_000_000)
        self.assertEqual(DEFAULT_DATA_LIMITS.max_workspace_retained_raw_bytes, 200_000_000)
        self.assertEqual(DEFAULT_DATA_LIMITS.max_published_snapshot_bytes, 512 * 1024 * 1024)
        self.assertEqual(DEFAULT_DATA_LIMITS.max_rows_per_dataset, 1_000_000)
        self.assertEqual(DEFAULT_DATA_LIMITS.max_columns_per_dataset, 200)
        self.assertEqual(DEFAULT_DATA_LIMITS.max_workbook_sheets, 20)
        self.assertEqual(DEFAULT_DATA_LIMITS.max_chars_per_cell, 1_000_000)
        self.assertEqual(DEFAULT_DATA_LIMITS.max_expanded_xlsx_bytes, 200_000_000)
        self.assertEqual(DEFAULT_DATA_LIMITS.max_export_bytes, 10 * 1024 * 1024)

    def test_quick_and_deep_budgets(self) -> None:
        self.assertEqual(QUICK_BUDGET_LIMITS.active_seconds, 120)
        self.assertEqual(QUICK_BUDGET_LIMITS.total_model_attempts, 6)
        self.assertEqual(QUICK_BUDGET_LIMITS.max_replans, 0)
        self.assertEqual(DEEP_BUDGET_LIMITS.active_seconds, 600)
        self.assertEqual(DEEP_BUDGET_LIMITS.total_model_attempts, 24)
        self.assertEqual(DEEP_BUDGET_LIMITS.max_replans, 2)

    def test_plan_dag_cycle_and_missing_dep_rejection(self) -> None:
        t1 = TaskNode("t1", "data", "fetch", ("t2",), "s1", "sem1", (), "schema")
        t2 = TaskNode("t2", "data", "fetch2", ("t1",), "s1", "sem1", (), "schema")
        dag = PlanDAG(revision=1, goal="test", completion_criteria=("done",), tasks=(t1, t2))
        with self.assertRaises(ValueError):
            dag.validate_dag()

        t3 = TaskNode("t3", "data", "fetch3", ("missing",), "s1", "sem1", (), "schema")
        dag2 = PlanDAG(revision=1, goal="test", completion_criteria=("done",), tasks=(t3,))
        with self.assertRaises(ValueError):
            dag2.validate_dag()

    def test_inr_acceptance_fixture_exact_reconciliation(self) -> None:
        fix_dir = Path(__file__).resolve().parents[2] / "fixtures" / "inr_acceptance"
        expected = json.loads((fix_dir / "expected_answers.json").read_text())

        def load_csv(name: str) -> list[dict[str, str]]:
            with (fix_dir / name).open("r", encoding="utf-8") as f:
                return list(csv.DictReader(f))

        summary = reconcile_orders_settlement(
            load_csv("orders.csv"),
            load_csv("refunds.csv"),
            load_csv("fees.csv"),
            load_csv("payouts.csv"),
        )
        self.assertEqual(summary.currency, expected["currency"])
        self.assertEqual(summary.gross_paid_orders, Decimal(expected["gross_paid_orders"]))
        self.assertEqual(summary.cancelled_orders_excluded, Decimal(expected["cancelled_orders_excluded"]))
        self.assertEqual(summary.quarantined_duplicate_orders, Decimal(expected["quarantined_duplicate_orders"]))
        self.assertEqual(summary.total_refunds, Decimal(expected["total_refunds"]))
        self.assertEqual(summary.net_sales, Decimal(expected["net_sales"]))
        self.assertEqual(summary.total_fees, Decimal(expected["total_fees"]))
        self.assertEqual(summary.eligible_settlement, Decimal(expected["eligible_settlement"]))
        self.assertEqual(summary.confirmed_payouts, Decimal(expected["confirmed_payouts"]))
        self.assertEqual(summary.in_transit_payouts, Decimal(expected["in_transit_payouts"]))
        self.assertEqual(summary.conclusion, expected["reconciliation_conclusion"])
        self.assertFalse(summary.profit_answerable)

    def test_mixed_currency_rejected(self) -> None:
        with self.assertRaises(ValueError):
            reconcile_orders_settlement(
                [{"order_id": "1", "status": "paid", "amount_inr": "100.00", "currency": "INR"}],
                [{"refund_id": "r1", "amount_inr": "10.00", "currency": "USD"}],
                [],
                [],
            )




    def test_domain_dependency_direction_invariant(self) -> None:
        import ast
        domain_dir = Path(__file__).resolve().parents[1] / "app" / "domain"
        forbidden = {"fastapi", "sqlalchemy", "langgraph", "deepagents", "duckdb", "openai", "anthropic", "google"}
        for py_file in domain_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        self.assertNotIn(top, forbidden, f"Forbidden import {top} in {py_file.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0]
                    self.assertNotIn(top, forbidden, f"Forbidden import {top} in {py_file.name}")

    def test_conflicting_evidence_variant(self) -> None:
        orders = [{"order_id": "001", "status": "paid", "amount_inr": "1000.00", "currency": "INR", "source_key": "K1"}]
        payouts = [{"payout_id": "P1", "status": "confirmed", "amount_inr": "800.00", "currency": "INR"}]
        summary = reconcile_orders_settlement(orders, [], [], payouts)
        self.assertEqual(summary.conclusion, "conflicting_evidence")
        self.assertEqual(summary.unexplained_variance, Decimal("200.00"))

if __name__ == "__main__":
    unittest.main()
