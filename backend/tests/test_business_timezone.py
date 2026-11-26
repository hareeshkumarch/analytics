import json
import tempfile
import unittest
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.ipc as pa_ipc
from fastapi.testclient import TestClient

from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.api.main import create_app
from app.application.ports.providers import ToolCallResult
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget
from app.bootstrap.container import build_container
from app.domain.errors import ValidationError
from app.domain.timezone import normalize_timezone

TS = pa.timestamp("us", tz="UTC")
OCTOBER = {
    "operation": "compare_periods",
    "purpose": "October vs September sales",
    "table_id": "sales",
    "date_field": "sold_at",
    "metric": {"alias": "total", "func": "sum", "field_id": "amount"},
    "current_start": "2024-10-01",
    "current_end": "2024-10-31",
    "previous_start": "2024-09-01",
    "previous_end": "2024-09-30",
}


def _publish_sales(container) -> dict:
    rows = [
        (datetime(2024, 9, 30, 19, 0, tzinfo=timezone.utc), 10.0),
        (datetime(2024, 10, 15, 12, 0, tzinfo=timezone.utc), 100.0),
        (datetime(2024, 10, 31, 20, 0, tzinfo=timezone.utc), 5.0),
    ]
    table = pa.table({"sold_at": pa.array([r[0] for r in rows], type=TS), "amount": [r[1] for r in rows]})
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    staged = container.artifacts.stage_bytes("jobtz", "sales.arrow", sink.getvalue().to_pybytes(), "application/octet-stream")
    container.artifacts.publish_staged("jobtz", staged, "arttz__sales")
    columns = [{"name": "sold_at", "type": str(TS), "nullable": True}, {"name": "amount", "type": "double", "nullable": True}]
    return {"snapshot_id": "snaptz", "table_artifacts": {"sales": "arttz__sales"}, "table_details": [{"name": "sales", "row_count": 3, "columns": columns}]}


SEMANTICS = {
    "semantic_version_id": "sem_1",
    "approved": True,
    "metrics": [
        {"metric_id": "sales__amount__sum", "display_name": "sales total amount", "expression": "SUM(amount)", "grain": "sales", "unit": "USD"},
        {"metric_id": "orders__amount__sum", "display_name": "orders total", "expression": "SUM(amount)", "grain": "orders", "unit": "USD"},
    ],
}


class TestBusinessTimezone(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.container = build_container(artifact_root=self._td.name)
        self.snapshot = _publish_sales(self.container)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run(self, tz: str, ir: dict) -> tuple[dict, dict]:
        box = AnalyticsToolbox(
            engine=SnapshotQueryEngine(self.container.artifacts, SqlGlotQueryCompiler()),
            snapshot=self.snapshot,
            semantics=SEMANTICS,
            budget=ToolBudget(10, 10),
            business_timezone=tz,
        )
        out = box.execute(ToolCallResult("c", "run_query_ir", ir))
        self.assertFalse(out.is_error, out.content)
        return json.loads(out.content), out.evidence or {}

    def test_october_differs_between_utc_and_kolkata_near_midnight(self) -> None:
        utc, _ = self._run("UTC", OCTOBER)
        ist, evidence = self._run("Asia/Kolkata", OCTOBER)
        self.assertEqual(utc["rows"][0], [105.0, 10.0])
        self.assertEqual(ist["rows"][0], [110.0, None])
        self.assertEqual(evidence["business_timezone"], "Asia/Kolkata")

    def test_date_filters_use_the_business_timezone(self) -> None:
        ir = {
            "operation": "aggregate",
            "purpose": "October total",
            "table_id": "sales",
            "metrics": [{"alias": "total", "func": "sum", "field_id": "amount"}],
            "filters": [{"field_id": "sold_at", "op": "gte", "value": "2024-10-01"}, {"field_id": "sold_at", "op": "lt", "value": "2024-11-01"}],
        }
        self.assertEqual(self._run("UTC", ir)[0]["rows"][0], [105.0])
        self.assertEqual(self._run("Asia/Kolkata", ir)[0]["rows"][0], [110.0])

    def test_evidence_records_metric_lineage(self) -> None:
        _, evidence = self._run("UTC", OCTOBER)
        self.assertEqual(evidence["semantic_version_id"], "sem_1")
        self.assertEqual(evidence["metric_ids"], ["sales__amount__sum"])

    def test_timezone_names_are_validated(self) -> None:
        self.assertEqual(normalize_timezone(""), "UTC")
        self.assertEqual(normalize_timezone("Asia/Kolkata"), "Asia/Kolkata")
        for bad in ("Mars/Olympus", "UTC'; DROP TABLE x; --"):
            with self.assertRaises(ValidationError):
                normalize_timezone(bad)


class TestWorkspaceSettingsApi(unittest.TestCase):
    def test_get_and_update_business_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client = TestClient(create_app(build_container(artifact_root=td)))
            self.assertEqual(client.get("/api/v1/workspaces/ws1/settings").json()["business_timezone"], "UTC")
            saved = client.put("/api/v1/workspaces/ws1/settings", json={"business_timezone": "Asia/Kolkata"})
            self.assertEqual((saved.status_code, saved.json()["business_timezone"]), (200, "Asia/Kolkata"))
            self.assertEqual(client.get("/api/v1/workspaces/ws1/settings").json()["business_timezone"], "Asia/Kolkata")
            self.assertEqual(client.get("/api/v1/workspaces/ws2/settings").json()["business_timezone"], "UTC")
            self.assertEqual(client.put("/api/v1/workspaces/ws1/settings", json={"business_timezone": "Nowhere/Land"}).status_code, 422)

    def test_runs_use_the_workspace_timezone(self) -> None:
        import base64

        from app.domain.providers import ProviderId
        from tests.test_tool_loop import ToolCallingGateway

        csv = base64.b64encode(b"region,amount\nnorth,10\nsouth,5\n").decode("ascii")
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            container.providers.register(ToolCallingGateway())
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            client = TestClient(create_app(container))
            client.put("/api/v1/workspaces/ws1/settings", json={"business_timezone": "Asia/Kolkata"})
            made = client.post(
                "/api/v1/workspaces/ws1/connectors/extract",
                json={"family": "uploaded_file", "config": {"format": "csv", "table_name": "orders", "raw_base64": csv}, "selected_tables": ["orders"]},
            ).json()
            sid = made["snapshot"]["snapshot_id"]
            client.post(f"/api/v1/workspaces/ws1/semantics/{sid}/approve", json={"expected_version": made["semantics"]["version"]})
            run_id = client.post("/api/v1/workspaces/ws1/runs", json={"question": "Sales by region?", "mode": "quick", "execute_inline": True}).json()["run_id"]
            items = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}/evidence").json()["items"]
            self.assertTrue(items)
            self.assertEqual({e["business_timezone"] for e in items}, {"Asia/Kolkata"})
            made_export = client.post("/api/v1/workspaces/ws1/exports", json={"run_id": run_id, "format": "html"}).json()
            html = client.get(made_export["download_url"]).text
            self.assertIn("<h2>Sources</h2>", html)
            self.assertIn("time zone Asia/Kolkata", html)
            self.assertIn(f"snapshot {sid}", html)


if __name__ == "__main__":
    unittest.main()
