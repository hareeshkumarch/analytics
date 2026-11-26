import json
import tempfile
import unittest
from unittest.mock import patch

from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application.ports.providers import ToolCallResult
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget, ToolErrorCode
from app.bootstrap.container import build_container
from app.domain.errors import QueryTimeoutError
from tests.test_tool_loop import QUERY, _publish_orders


class TestToolEnvelope(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        c = build_container(artifact_root=self._td.name)
        self.snapshot = _publish_orders(c)
        self.box = AnalyticsToolbox(
            engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()), snapshot=self.snapshot, semantics=None, budget=ToolBudget(20, 20)
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _call(self, name: str, args: dict) -> dict:
        return json.loads(self.box.execute(ToolCallResult("c", name, args)).content)

    def test_success_empty_and_partial(self) -> None:
        ok = self._call("run_query_ir", QUERY)
        self.assertEqual(ok["status"], "success")
        self.assertEqual(ok["evidence_refs"], [ok["evidence_id"]])
        self.assertEqual(ok["freshness"]["snapshot_id"], self.snapshot["snapshot_id"])
        self.assertEqual(ok["coverage"], {"rows_returned": 2, "total_rows_known": True, "truncated": False})
        empty = self._call("run_query_ir", {**QUERY, "filters": [{"field_id": "region", "op": "eq", "value": "west"}]})
        self.assertEqual((empty["status"], empty["row_count"]), ("empty", 0))
        partial = self._call("run_query_ir", {**QUERY, "limit": 1})
        self.assertEqual(partial["status"], "partial")
        self.assertTrue(partial["coverage"]["truncated"])
        self.assertEqual(self._call("inspect_schema", {})["status"], "success")
        calc = self._call("calculate", {"operation": "sum", "purpose": "t", "values": [{"evidence_id": ok["evidence_id"], "row": 0, "column": "total"}]})
        self.assertEqual(calc["status"], "success")
        self.assertEqual(calc["evidence_refs"], [calc["evidence_id"], ok["evidence_id"]])

    def test_error_classes(self) -> None:
        def code(name: str, args: dict) -> tuple[str, str]:
            out = self._call(name, args)
            return out["status"], out["code"]

        self.assertEqual(code("nope", {}), ("failed", ToolErrorCode.UNKNOWN_TOOL.value))
        invalid = self._call("run_query_ir", {"operation": "aggregate", "purpose": "x"})
        self.assertEqual((invalid["status"], invalid["code"], invalid["retryable"]), ("failed", "INVALID_ARGUMENT", False))
        self.assertEqual(invalid["failed_fields"], ["metrics"])
        bad_col = self._call(
            "run_query_ir",
            {**QUERY, "metrics": [{"alias": "t", "func": "sum", "field_id": "missing_col"}]},
        )
        self.assertEqual(bad_col["failed_fields"], ["missing_col"])
        self.assertEqual(code("inspect_schema", {"table_id": "missing"}), ("failed", "NOT_FOUND"))
        self.assertEqual(code("calculate", {"operation": "sum", "purpose": "t", "values": [{"evidence_id": "ev_x", "row": 0, "column": "a"}]}), ("failed", "NOT_FOUND"))
        with patch.object(SnapshotQueryEngine, "run", side_effect=QueryTimeoutError("Query exceeded execution deadline")):
            self.assertEqual(code("run_query_ir", {**QUERY, "purpose": "slow"}), ("failed", "TIMEOUT"))
        self.assertEqual(code("run_query_ir", {"operation": "aggregate", "purpose": "x"}), ("denied", "DUPLICATE_FAILED_CALL"))

    def test_budget_exhausted_is_denied(self) -> None:
        self.box.budget = ToolBudget(0, 0)
        out = self._call("inspect_schema", {})
        self.assertEqual((out["status"], out["code"]), ("denied", "BUDGET_EXHAUSTED"))

    def test_changed_call_after_failure_is_dispatched(self) -> None:
        bad = {"operation": "aggregate", "purpose": "x"}
        self._call("run_query_ir", bad)
        fixed = self._call("run_query_ir", {**QUERY, "purpose": "x"})
        self.assertEqual(fixed["status"], "success")


if __name__ == "__main__":
    unittest.main()
