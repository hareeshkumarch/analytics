import json
import tempfile
import unittest

from app.adapters.providers.http_utils import classify_http_error, parse_retry_after
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application.ports.providers import ToolCallResult
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget
from app.bootstrap.container import build_container
from app.domain.query_ir import parse_query_ir
from tests.test_tool_loop import QUERY, _publish_orders


class TestOptionalFieldsNeverBroadenScope(unittest.TestCase):
    def test_omitted_null_and_empty_lists_mean_the_same(self) -> None:
        base = {"operation": "aggregate", "purpose": "p", "table_id": "orders", "metrics": [{"alias": "t", "func": "sum", "field_id": "amount"}]}
        for key in ("filters", "group_by", "order_by"):
            shapes = [parse_query_ir(base), parse_query_ir({**base, key: None}), parse_query_ir({**base, key: []})]
            self.assertEqual({getattr(ir, key) for ir in shapes}, {()}, key)


class TestUndefinedArithmetic(unittest.TestCase):
    def test_ratio_against_a_zero_baseline_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            box = AnalyticsToolbox(
                engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()), snapshot=_publish_orders(c), semantics=None, budget=ToolBudget(10, 10)
            )
            zero = {**QUERY, "purpose": "zero", "metrics": [{"alias": "none", "func": "count", "field_id": "amount"}], "filters": [{"field_id": "region", "op": "eq", "value": "west"}], "group_by": [], "order_by": []}
            total = json.loads(box.execute(ToolCallResult("a", "run_query_ir", {**QUERY, "group_by": [], "order_by": []})).content)
            empty = json.loads(box.execute(ToolCallResult("b", "run_query_ir", zero)).content)
            self.assertEqual(empty["rows"][0], [0])

            def ref(result: dict) -> dict:
                return {"evidence_id": result["evidence_id"], "row": 0, "column": result["columns"][0]}

            out = json.loads(box.execute(ToolCallResult("c", "calculate", {"operation": "ratio", "purpose": "share", "values": [ref(total), ref(empty)]})).content)
            self.assertEqual((out["status"], out["code"]), ("failed", "INVALID_ARGUMENT"))
            self.assertIn("baseline value is zero", out["safe_message"])


class TestRateLimits(unittest.TestCase):
    def test_429_is_retryable_and_retry_after_is_parsed(self) -> None:
        error = classify_http_error(429, '{"error": "slow down"}', "OpenAI")
        self.assertTrue(error.retryable)
        self.assertEqual(parse_retry_after("7"), 7.0)
        self.assertIsNone(parse_retry_after("soon"))
        self.assertFalse(classify_http_error(403, "forbidden", "OpenAI").retryable)


if __name__ == "__main__":
    unittest.main()
