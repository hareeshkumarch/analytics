import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.api.main import create_app
from app.application.services.charts import MAX_CHARTS, MAX_POINTS, build_answer_charts
from app.application.services.export_service import ExportService
from app.bootstrap.container import build_container
from app.domain.models import Answer, Claim


def _ev(evidence_id: str, operation: str, columns: list[str], rows: list[list[object]], **extra: object) -> dict:
    return {"evidence_id": evidence_id, "operation": operation, "columns": columns, "rows": rows,
            "row_count": len(rows), "truncated": False, **extra}


class TestBuildAnswerCharts(unittest.TestCase):
    def test_grouped_aggregate_becomes_a_bar_chart(self) -> None:
        ev = _ev("ev_a", "aggregate", ["region", "total"], [["north", "12.50"], ["south", 5]], purpose="Sales by region")
        (chart,) = build_answer_charts([ev], {"ev_a"})
        self.assertEqual((chart.chart_type, chart.title, chart.x_field, chart.y_fields), ("bar", "Sales by region", "region", ("total",)))
        self.assertEqual(chart.rows, (("north", 12.5), ("south", 5.0)))

    def test_a_chart_takes_the_currency_its_series_share(self) -> None:
        ev = _ev("ev_c", "aggregate", ["status", "paid", "fees", "orders"], [["a", 1, 2, 3], ["b", 4, 5, 6]],
                 currencies={"paid": "INR", "fees": "INR"})
        (chart,) = build_answer_charts([ev], {"ev_c"})
        self.assertIsNone(chart.currency)
        ev = _ev("ev_i", "aggregate", ["status", "paid", "fees"], [["a", 1, 2], ["b", 4, 5]], currencies={"paid": "INR", "fees": "INR"})
        self.assertEqual(build_answer_charts([ev], {"ev_i"})[0].currency, "INR")
        ev = _ev("ev_m", "aggregate", ["status", "paid", "fees"], [["a", 1, 2], ["b", 4, 5]], currencies={"paid": "INR", "fees": "USD"})
        self.assertIsNone(build_answer_charts([ev], {"ev_m"})[0].currency)

    def test_dates_become_a_sorted_line(self) -> None:
        ev = _ev("ev_d", "aggregate", ["month", "total"], [["2026-10-01", 3], ["2026-09-01", 2]])
        (chart,) = build_answer_charts([ev], {"ev_d"})
        self.assertEqual(chart.chart_type, "line")
        self.assertEqual([r[0] for r in chart.rows], ["2026-09-01", "2026-10-01"])

    def test_only_cited_and_chartable_evidence_is_drawn(self) -> None:
        evidence = [
            _ev("ev_uncited", "aggregate", ["region", "total"], [["n", 1], ["s", 2]]),
            _ev("ev_detail", "detail", ["region", "amount"], [["n", 1], ["s", 2]]),
            _ev("ev_kpi", "aggregate", ["total"], [[3]]),
            _ev("ev_calc", "calculate_ratio", ["result"], [["0.5"]]),
            _ev("ev_text", "aggregate", ["region", "owner"], [["n", "a"], ["s", "b"]]),
        ]
        self.assertEqual(build_answer_charts(evidence, {"ev_detail", "ev_kpi", "ev_calc", "ev_text"}), ())

    def test_compare_periods_distribution_and_reconciliation(self) -> None:
        evidence = [
            _ev("ev_cmp", "compare_periods", ["current_value", "previous_value"], [[700, 300]]),
            _ev("ev_dist", "distribution", ["bucket", "bucket_min", "bucket_max", "row_count", "min_val"],
                [[1, 0.0, 5.0, 4, 0.0], [2, 5.0, 10.0, 1, 0.0]]),
            _ev("ev_rec", "reconciliation_lookup", ["match_key", "left_total", "right_total", "variance"],
                [["k1", 10, 8, 2], ["k2", 5, 5, 0]]),
        ]
        cmp, dist, rec = build_answer_charts(evidence, {"ev_cmp", "ev_dist", "ev_rec"})
        self.assertEqual(cmp.rows, (("Previous period", 300.0), ("Current period", 700.0)))
        self.assertEqual((dist.chart_type, dist.rows[0]), ("histogram", ("0–5", 4.0)))
        self.assertEqual((rec.y_fields, rec.rows[0]), (("left_total", "right_total"), ("k1", 10.0, 8.0)))

    def test_limits_charts_and_points_and_flags_partial_data(self) -> None:
        many = [[f"k{i}", i] for i in range(MAX_POINTS + 5)]
        evidence = [_ev(f"ev_{n}", "aggregate", ["k", "v"], many) for n in range(MAX_CHARTS + 2)]
        charts = build_answer_charts(evidence, {e["evidence_id"] for e in evidence})
        self.assertEqual(len(charts), MAX_CHARTS)
        self.assertTrue(all(len(c.rows) == MAX_POINTS and c.truncated for c in charts))
        capped = _ev("ev_cap", "aggregate", ["k", "v"], [["a", 1], ["b", 2]], row_count=40)
        self.assertTrue(build_answer_charts([capped], {"ev_cap"})[0].truncated)


class TestChartsArePersistedAndExported(unittest.TestCase):
    def test_round_trip_through_run_list_and_export(self) -> None:
        sf = create_session_factory(create_db_engine("sqlite:///:memory:"))
        with tempfile.TemporaryDirectory() as td:
            app = create_app(build_container(artifact_root=td), session_factory=sf)
            client = TestClient(app)
            run_id = client.post("/api/v1/workspaces/ws1/runs", json={"question": "Sales by region?", "mode": "quick"}).json()["run_id"]
            charts = build_answer_charts(
                [_ev("ev_a", "aggregate", ["region", "total"], [["north", 12.5], ["<b>south</b>", 5]])], {"ev_a"}
            )
            app.state.repo_runs.save_answer(Answer(
                answer_id="ans_1", run_id=run_id, summary_markdown="North leads [ev_a].", claims=(), charts=charts,
                limitations=(), snapshot_id="snap_1", semantic_version_id="sem_1", created_at=datetime.now(timezone.utc),
            ))
            app.state.answers.pop(run_id, None)
            listed = client.get("/api/v1/workspaces/ws1/runs").json()["runs"][0]["answer"]
            self.assertEqual(listed["charts"][0]["rows"], [["north", 12.5], ["<b>south</b>", 5.0]])
            created = client.post("/api/v1/workspaces/ws1/exports", json={"run_id": run_id, "format": "html"}).json()
            html = client.get(created["download_url"]).text
            self.assertIn("<th>region</th><th>total</th>", html)
            self.assertIn("&lt;b&gt;south&lt;/b&gt;", html)
            self.assertEqual(client.get("/api/v1/workspaces/ws1/exports/art1__orders").status_code, 404)
            for fmt in ("pdf", "docx"):
                self.assertEqual(client.post("/api/v1/workspaces/ws1/exports", json={"run_id": run_id, "format": fmt}).status_code, 201)

    def test_pdf_export_handles_several_claims(self) -> None:
        claims = tuple(Claim(f"c{i}", "observation", f"Fact {i}", Decimal(i), None, None, ("ev_a",)) for i in range(3))
        answer = Answer(answer_id="a", run_id="r", summary_markdown="x", claims=claims, charts=(), limitations=("l1", "l2"),
                        snapshot_id="s", semantic_version_id="v", created_at=datetime.now(timezone.utc))
        self.assertTrue(ExportService().export_pdf(answer).startswith(b"%PDF"))

    def test_exports_without_charts_are_unchanged(self) -> None:
        answer = Answer(answer_id="a", run_id="r", summary_markdown="x", claims=(), charts=(), limitations=(),
                        snapshot_id="s", semantic_version_id="v", created_at=datetime.now(timezone.utc))
        self.assertNotIn(b"<h2>Charts</h2>", ExportService().export_html(answer))


if __name__ == "__main__":
    unittest.main()
