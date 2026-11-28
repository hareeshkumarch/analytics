"""The trend, percentiles and outliers query operations, run against real data in DuckDB."""

import json
import tempfile
import unittest
from datetime import date

import pyarrow as pa
import pyarrow.ipc as pa_ipc

from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application.ports.providers import ToolCallResult
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget
from app.application.services.charts import build_answer_charts
from app.bootstrap.container import build_container
from app.domain.query_ir import QueryIRParseError, parse_query_ir

# Monthly totals: Jan 100, Feb 200, Mar 300, Apr 1000.
ROWS = [
    (date(2026, 1, 5), "north", 40.0), (date(2026, 1, 20), "south", 60.0),
    (date(2026, 2, 3), "north", 120.0), (date(2026, 2, 25), "south", 80.0),
    (date(2026, 3, 9), "north", 100.0), (date(2026, 3, 14), "south", 200.0),
    (date(2026, 4, 1), "north", 50.0), (date(2026, 4, 2), "north", 950.0),
]


def _toolbox(td: str, budget: ToolBudget | None = None) -> AnalyticsToolbox:
    c = build_container(artifact_root=td)
    table = pa.table({"sold_on": [r[0] for r in ROWS], "region": [r[1] for r in ROWS], "amount": [r[2] for r in ROWS]})
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    staged = c.artifacts.stage_bytes("job_s", "sales.arrow", sink.getvalue().to_pybytes(), "application/octet-stream")
    c.artifacts.publish_staged("job_s", staged, "art_s__sales")
    snapshot = {
        "snapshot_id": "snap_sales",
        "table_artifacts": {"sales": "art_s__sales"},
        "table_details": [
            {
                "name": "sales",
                "row_count": len(ROWS),
                "columns": [
                    {"name": "sold_on", "type": "date32[day]", "nullable": True},
                    {"name": "region", "type": "string", "nullable": True},
                    {"name": "amount", "type": "double", "nullable": True},
                ],
            }
        ],
    }
    return AnalyticsToolbox(engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()), snapshot=snapshot, semantics=None, budget=budget or ToolBudget(20, 20))


def _run(box: AnalyticsToolbox, query: dict):
    out = box.execute(ToolCallResult("c", "run_query_ir", {"purpose": "p", "table_id": "sales", **query}))
    return out, json.loads(out.content)


class TestTrend(unittest.TestCase):
    QUERY = {"operation": "trend", "date_field": "sold_on", "metric": {"alias": "revenue", "func": "sum", "field_id": "amount"}, "grain": "month", "window": 2}

    def test_monthly_values_moving_average_change_and_growth(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out, payload = _run(_toolbox(td), self.QUERY)
            self.assertFalse(out.is_error, payload)
            self.assertEqual(payload["columns"], ["period", "revenue", "moving_average", "change", "growth"])
            rows = payload["rows"]
            self.assertEqual([r[0][:7] for r in rows], ["2026-01", "2026-02", "2026-03", "2026-04"])
            self.assertEqual([r[1] for r in rows], [100.0, 200.0, 300.0, 1000.0])
            self.assertEqual([r[2] for r in rows], [100.0, 150.0, 250.0, 650.0])
            self.assertEqual([r[3] for r in rows], [None, 100.0, 100.0, 700.0])
            self.assertEqual(rows[1][4], 1.0)
            self.assertAlmostEqual(rows[3][4], 700 / 300)

    def test_a_trend_is_charted_as_a_line_with_its_moving_average(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out, payload = _run(_toolbox(td), self.QUERY)
            charts = build_answer_charts([out.evidence], {payload["evidence_id"]})
            self.assertEqual(len(charts), 1)
            self.assertEqual((charts[0].chart_type, charts[0].x_field, charts[0].y_fields), ("line", "period", ("revenue", "moving_average")))

    def test_filters_apply_and_bad_arguments_name_the_field(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, north = _run(_toolbox(td), {**self.QUERY, "filters": [{"field_id": "region", "op": "eq", "value": "north"}]})
            self.assertEqual([r[1] for r in north["rows"]], [40.0, 120.0, 100.0, 1000.0])
        for bad, field in (({"grain": "fortnight"}, "grain"), ({"window": 0}, "window"), ({"group_by": ["region"]}, "group_by")):
            with self.assertRaises(QueryIRParseError) as ctx:
                parse_query_ir({"purpose": "p", "table_id": "sales", **self.QUERY, **bad})
            self.assertEqual(ctx.exception.failed_fields, (field,))


class TestPercentiles(unittest.TestCase):
    def test_overall_and_per_group(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            box = _toolbox(td)
            out, overall = _run(box, {"operation": "percentiles", "field_id": "amount", "percentiles": [0.5, 0.9]})
            self.assertFalse(out.is_error, overall)
            self.assertEqual(overall["columns"], ["p50", "p90", "value_count"])
            self.assertEqual(overall["rows"][0][0], 90.0)  # median of 40, 50, 60, 80, 100, 120, 200, 950
            self.assertEqual(overall["rows"][0][2], 8)
            _, grouped = _run(box, {"operation": "percentiles", "field_id": "amount", "percentiles": [0.5], "group_by": ["region"]})
            self.assertEqual(grouped["rows"], [["north", 100.0, 5], ["south", 80.0, 3]])

    def test_text_columns_and_bad_percentiles_are_refused_without_charge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            budget = ToolBudget(5, 5)
            out, payload = _run(_toolbox(td, budget), {"operation": "percentiles", "field_id": "region"})
            self.assertTrue(out.is_error)
            self.assertEqual(payload["failed_fields"], ["field_id"])
            self.assertIn("needs a numeric column", payload["safe_message"])
        with self.assertRaises(QueryIRParseError) as ctx:
            parse_query_ir({"operation": "percentiles", "purpose": "p", "table_id": "sales", "field_id": "amount", "percentiles": [50]})
        self.assertEqual(ctx.exception.failed_fields, ("percentiles",))


class TestOutliers(unittest.TestCase):
    def test_counts_values_beyond_the_interquartile_fences(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            box = _toolbox(td)
            out, payload = _run(box, {"operation": "outliers", "field_id": "amount"})
            self.assertFalse(out.is_error, payload)
            self.assertEqual(payload["columns"], ["q1", "q3", "lower_bound", "upper_bound", "outlier_count", "value_count", "min_value", "max_value"])
            row = dict(zip(payload["columns"], payload["rows"][0]))
            self.assertEqual((row["outlier_count"], row["value_count"], row["max_value"]), (1, 8, 950.0))
            self.assertLess(row["upper_bound"], 950.0)
            _, grouped = _run(box, {"operation": "outliers", "field_id": "amount", "group_by": ["region"]})
            by_region = {r[0]: dict(zip(grouped["columns"], r)) for r in grouped["rows"]}
            self.assertEqual((by_region["north"]["outlier_count"], by_region["south"]["outlier_count"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
