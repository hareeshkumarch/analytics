import unittest
from datetime import date, datetime

import pyarrow as pa

from app.adapters.query.duckdb_executor import DuckDbQueryExecutor
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.domain.errors import QueryValidationError
from app.domain.models import ColumnSchema, TableSchema
from app.domain.query_ir import (
    AggregateFunction,
    AggregateMetricSpec,
    AggregateQueryIR,
    ComparePeriodsQueryIR,
    DetailQueryIR,
    FilterOperator,
    FilterPredicate,
)

SCHEMAS = {
    "orders": TableSchema(
        table_name="orders",
        columns=(
            ColumnSchema("order_id", "string"),
            ColumnSchema("amount", "int64"),
            ColumnSchema("paid_at", "timestamp[us]"),
        ),
        row_count=5,
        normalized_bytes=1024,
    )
}
ORDERS = pa.table(
    {
        "order_id": ["o1", "o2", "o3", "o4", "o5"],
        "amount": [100, 200, 300, 400, 500],
        "paid_at": [
            datetime(2026, 9, 15, 9, 0),
            datetime(2026, 9, 30, 23, 30),
            datetime(2026, 10, 1, 0, 0),
            datetime(2026, 10, 31, 15, 45),
            datetime(2026, 11, 1, 0, 0),
        ],
    }
)


def _detail(limit: int) -> DetailQueryIR:
    return DetailQueryIR(table_id="orders", select_fields=("order_id",), limit=limit)


def _total(op: FilterOperator, value: object) -> AggregateQueryIR:
    return AggregateQueryIR(
        table_id="orders",
        metrics=(AggregateMetricSpec("total", AggregateFunction.SUM, "amount"),),
        filters=(FilterPredicate("amount", op, value),),
    )


def _compare(**overrides: str) -> ComparePeriodsQueryIR:
    bounds = {
        "current_start": "2026-10-01",
        "current_end": "2026-10-31",
        "previous_start": "2026-09-01",
        "previous_end": "2026-09-30",
    }
    bounds.update(overrides)
    return ComparePeriodsQueryIR(
        table_id="orders",
        date_field="paid_at",
        metric=AggregateMetricSpec("total", AggregateFunction.SUM, "amount"),
        **bounds,
    )


class TestTruncationIsReported(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = SqlGlotQueryCompiler()
        self.executor = DuckDbQueryExecutor()

    def _run(self, ir):
        return self.executor.execute_tables(self.compiler.compile(ir, SCHEMAS), {"orders": ORDERS}, "snap", "sem")

    def test_result_cut_off_at_limit_is_flagged(self) -> None:
        result = self._run(_detail(3))
        self.assertEqual(result.row_count, 3)
        self.assertTrue(result.truncated)

    def test_result_exactly_at_limit_is_not_flagged(self) -> None:
        result = self._run(_detail(5))
        self.assertEqual(result.row_count, 5)
        self.assertFalse(result.truncated)

    def test_executor_cap_still_applies_below_requested_limit(self) -> None:
        result = DuckDbQueryExecutor(max_rows=2).execute_tables(
            self.compiler.compile(_detail(5), SCHEMAS), {"orders": ORDERS}, "snap", "sem"
        )
        self.assertEqual(result.row_count, 2)
        self.assertTrue(result.truncated)


class TestFilterValuesAreChecked(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = SqlGlotQueryCompiler()

    def test_comparison_without_value_is_rejected(self) -> None:
        for op in (FilterOperator.EQ, FilterOperator.NEQ, FilterOperator.GT, FilterOperator.LTE):
            with self.assertRaises(QueryValidationError) as ctx:
                self.compiler.compile(_total(op, None), SCHEMAS)
            self.assertIn("is_null", str(ctx.exception))

    def test_comparison_with_list_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            self.compiler.compile(_total(FilterOperator.GT, [1, 2]), SCHEMAS)

    def test_null_checks_and_in_lists_still_compile(self) -> None:
        self.compiler.compile(_total(FilterOperator.IS_NULL, None), SCHEMAS)
        compiled = self.compiler.compile(_total(FilterOperator.IN, [100, 200]), SCHEMAS)
        self.assertEqual(compiled.parameters, (100, 200))


class TestComparePeriodBounds(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = SqlGlotQueryCompiler()

    def test_non_dates_are_rejected(self) -> None:
        with self.assertRaises(QueryValidationError) as ctx:
            self.compiler.compile(_compare(current_start="last month"), SCHEMAS)
        self.assertIn("current_start", str(ctx.exception))

    def test_reversed_period_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            self.compiler.compile(_compare(previous_start="2026-09-30", previous_end="2026-09-01"), SCHEMAS)

    def test_date_only_end_includes_the_whole_last_day(self) -> None:
        compiled = self.compiler.compile(_compare(), SCHEMAS)
        self.assertEqual(
            compiled.parameters, (date(2026, 10, 1), date(2026, 11, 1), date(2026, 9, 1), date(2026, 10, 1))
        )
        result = DuckDbQueryExecutor().execute_tables(compiled, {"orders": ORDERS}, "snap", "sem")
        current_value, previous_value = result.rows[0]
        self.assertEqual(current_value, 300 + 400)
        self.assertEqual(previous_value, 100 + 200)

    def test_text_dates_are_compared_as_timestamps(self) -> None:
        schemas = {
            "orders": TableSchema(
                table_name="orders",
                columns=(ColumnSchema("amount", "int64"), ColumnSchema("paid_at", "string")),
                row_count=5,
                normalized_bytes=1024,
            )
        }
        text_orders = pa.table(
            {"amount": ORDERS["amount"], "paid_at": [d.isoformat(sep=" ") for d in ORDERS["paid_at"].to_pylist()]}
        )
        compiled = self.compiler.compile(_compare(), schemas)
        self.assertIn('TRY_CAST("paid_at" AS TIMESTAMP)', compiled.sql)
        result = DuckDbQueryExecutor().execute_tables(compiled, {"orders": text_orders}, "snap", "sem")
        self.assertEqual(result.rows[0], (700, 300))

    def test_time_zone_offsets_are_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            self.compiler.compile(_compare(current_end="2026-10-31T12:00:00+05:30"), SCHEMAS)

    def test_datetime_end_is_inclusive(self) -> None:
        compiled = self.compiler.compile(_compare(current_end="2026-10-31T12:00:00"), SCHEMAS)
        result = DuckDbQueryExecutor().execute_tables(compiled, {"orders": ORDERS}, "snap", "sem")
        self.assertEqual(result.rows[0][0], 300)


if __name__ == "__main__":
    unittest.main()
