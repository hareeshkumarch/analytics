import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler, validate_sql_ast
from app.domain.errors import QueryValidationError, SecurityPolicyError
from app.domain.models import ColumnSchema, TableSchema
from app.domain.query_ir import (
    AggregateFunction,
    AggregateMetricSpec,
    AggregateQueryIR,
    FilterOperator,
    FilterPredicate,
    ReconciliationLookupQueryIR,
)


class TestQueryIrAndAst(unittest.TestCase):
    def setUp(self) -> None:
        self.schemas = {
            "orders": TableSchema(
                table_name="orders",
                columns=(
                    ColumnSchema("order_id", "string"),
                    ColumnSchema("status", "string"),
                    ColumnSchema("amount_inr", "decimal128(38,2)"),
                ),
                row_count=5,
                normalized_bytes=1024,
            ),
            "refunds": TableSchema(
                table_name="refunds",
                columns=(
                    ColumnSchema("order_id", "string"),
                    ColumnSchema("amount_inr", "decimal128(38,2)"),
                ),
                row_count=2,
                normalized_bytes=512,
            ),
        }
        self.compiler = SqlGlotQueryCompiler()

    def test_aggregate_query_parameterized_and_quoted(self) -> None:
        ir = AggregateQueryIR(
            table_id="orders",
            metrics=(AggregateMetricSpec("total_sales", AggregateFunction.SUM, "amount_inr"),),
            group_by=("status",),
            filters=(FilterPredicate("status", FilterOperator.EQ, "paid"),),
            limit=100,
        )
        compiled = self.compiler.compile(ir, self.schemas)
        self.assertIn('SUM("amount_inr") AS "total_sales"', compiled.sql)
        self.assertEqual(compiled.parameters, ("paid",))

    def test_unknown_field_rejected(self) -> None:
        ir = AggregateQueryIR(
            table_id="orders",
            metrics=(AggregateMetricSpec("x", AggregateFunction.SUM, "nonexistent"),),
        )
        with self.assertRaises(QueryValidationError):
            self.compiler.compile(ir, self.schemas)

    def test_reconciliation_lookup_compiles(self) -> None:
        ir = ReconciliationLookupQueryIR(
            left_table_id="orders",
            right_table_id="refunds",
            left_key="order_id",
            right_key="order_id",
            left_amount_field="amount_inr",
            right_amount_field="amount_inr",
        )
        compiled = self.compiler.compile(ir, self.schemas)
        self.assertIn("FULL OUTER JOIN", compiled.sql)

    def test_forbidden_sql_ast_nodes_rejected(self) -> None:
        bad_queries = [
            "SELECT * FROM orders; DROP TABLE orders",
            "ATTACH '/tmp/x.db' AS x",
            "COPY orders TO '/tmp/out.csv'",
            "SELECT * FROM read_csv_auto('/etc/passwd')",
            "SELECT * FROM duckdb_tables()",
            "PRAGMA database_list",
            "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t) SELECT * FROM t",
        ]
        for sql in bad_queries:
            with self.assertRaises(SecurityPolicyError, msg=f"Failed to reject: {sql}"):
                validate_sql_ast(sql)

    def test_missing_sqlglot_fails_closed(self) -> None:
        with patch.dict(sys.modules, {"sqlglot": None}):
            with self.assertRaises(SecurityPolicyError):
                validate_sql_ast("SELECT 1")


if __name__ == "__main__":
    unittest.main()
