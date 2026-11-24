import io
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

import httpx
import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.connectors.destination_policy import DestinationPolicy
from app.adapters.connectors.rest_connector import RestJsonConnector
from app.adapters.connectors.sqlite_connector import UploadedSqliteConnector
from app.adapters.parsers.csv_parser import CsvFileParser
from app.adapters.parsers.xlsx_parser import XlsxFileParser
from app.adapters.providers.openai_adapter import OpenAiResponsesAdapter
from app.adapters.providers.registry import ProviderRegistry
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler, validate_sql_ast
from app.application.ports.clock import SystemClock
from app.application.ports.connectors import ExtractionRequest
from app.application.ports.providers import ModelRequest
from app.application.services.budget_service import AtomicBudgetLedger
from app.application.services.export_service import ExportService
from app.application.services.routing_service import CircuitBreakerManager, RoutingService
from app.domain.errors import (
    ConnectorError,
    LimitExceededError,
    ProviderError,
    SecurityPolicyError,
    ValidationError,
)
from app.domain.limits import DEFAULT_DATA_LIMITS, DataLimits
from app.domain.models import ColumnSchema, TableSchema
from app.domain.providers import (
    AgentRole,
    ApiProfile,
    ModelCapabilities,
    ModelProfile,
    ProviderId,
    ProviderProfile,
    RoutingPolicy,
    RoutingPreset,
)
from app.domain.query_ir import (
    AggregateFunction,
    AggregateMetricSpec,
    ComparePeriodsQueryIR,
    ContributionBreakdownQueryIR,
    DetailQueryIR,
    DistributionQueryIR,
    FilterOperator,
    FilterPredicate,
)


class TestStrictInvariants(unittest.TestCase):
    def test_xlsx_rejects_macro_vba_and_external_links(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("xl/vbaProject.bin", b"fake-macro")
        with self.assertRaises(SecurityPolicyError):
            XlsxFileParser().parse(buf.getvalue(), "macro_wb", DEFAULT_DATA_LIMITS)

        buf2 = io.BytesIO()
        with zipfile.ZipFile(buf2, "w") as zf:
            zf.writestr("xl/externalLinks/externalLink1.xml", b"<link/>")
        with self.assertRaises(SecurityPolicyError):
            XlsxFileParser().parse(buf2.getvalue(), "ext_wb", DEFAULT_DATA_LIMITS)

    def test_xlsx_rejects_over_20_sheets(self) -> None:
        wb = openpyxl.Workbook()
        for i in range(21):
            ws = wb.active if i == 0 else wb.create_sheet(f"S{i}")
            ws.append(["a"])
            ws.append([1])
        buf = io.BytesIO()
        wb.save(buf)
        with self.assertRaises(LimitExceededError):
            XlsxFileParser().parse(buf.getvalue(), "many_sheets", DEFAULT_DATA_LIMITS)

    def test_csv_rejects_oversized_cell_and_row_limit(self) -> None:
        tiny_limits = DataLimits(max_chars_per_cell=10, max_rows_per_dataset=2)
        with self.assertRaises(LimitExceededError):
            CsvFileParser().parse(b"a\n01234567890\n", "t", tiny_limits)
        with self.assertRaises(LimitExceededError):
            CsvFileParser().parse(b"a\n1\n2\n3\n", "t", tiny_limits)

    def test_sqlite_rejects_live_wal_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "live.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (id INT)")
            conn.commit()
            conn.close()
            Path(f"{db_path}-wal").write_bytes(b"non-empty-wal-bytes")
            with self.assertRaises(ValidationError):
                UploadedSqliteConnector(SystemClock()).probe({"file_path": str(db_path)}, {})

    def test_rest_blocks_redirects_cross_origin_and_cursor_loops(self) -> None:
        policy = DestinationPolicy(
            allowed_hosts=("localhost",),
            allowed_ports=(8080,),
            allow_private_local_for_tests=True,
            require_https=False,
        )
        t_redir = httpx.MockTransport(lambda req: httpx.Response(302, headers={"Location": "http://evil.com"}))
        conn_redir = RestJsonConnector(SystemClock(), policy=policy, transport=t_redir)
        with self.assertRaises(SecurityPolicyError):
            conn_redir.probe({"url": "http://localhost:8080/data", "collection_path": "items", "safe_get_documented": True}, {})

        t_cross = httpx.MockTransport(lambda req: httpx.Response(200, json={"items": [{"id": 1}], "next": "http://other.local:9000/p2"}))
        conn_cross = RestJsonConnector(SystemClock(), policy=policy, transport=t_cross)
        req = ExtractionRequest("ws", "src", "job", ("rest_records",), {}, DEFAULT_DATA_LIMITS, 1)
        with self.assertRaises(SecurityPolicyError):
            conn_cross.extract(
                {"url": "http://localhost:8080/data", "collection_path": "items", "safe_get_documented": True, "pagination_mode": "next_link"},
                {},
                req,
                lambda: False,
            )

        t_loop = httpx.MockTransport(lambda req: httpx.Response(200, json={"items": [{"id": 1}], "next_cursor": "same_cursor"}))
        conn_loop = RestJsonConnector(SystemClock(), policy=policy, transport=t_loop)
        with self.assertRaises(ConnectorError):
            conn_loop.extract(
                {"url": "http://localhost:8080/data", "collection_path": "items", "safe_get_documented": True, "pagination_mode": "cursor"},
                {},
                req,
                lambda: False,
            )

    def test_query_ir_all_operations_and_sqli_parameterization(self) -> None:
        schemas = {
            "orders": TableSchema(
                "orders",
                (
                    ColumnSchema("order_id", "string"),
                    ColumnSchema("status", "string"),
                    ColumnSchema("order_date", "string"),
                    ColumnSchema("amount_inr", "decimal128(38,2)"),
                ),
                10,
                1024,
            )
        }
        compiler = SqlGlotQueryCompiler()

        sqli = "paid' OR 1=1; DROP TABLE orders; --"
        c_det = compiler.compile(
            DetailQueryIR(
                table_id="orders",
                select_fields=("order_id", "amount_inr"),
                filters=(FilterPredicate("status", FilterOperator.EQ, sqli),),
                limit=5000,
            ),
            schemas,
            max_rows=1000,
        )
        self.assertNotIn("DROP TABLE", c_det.sql)
        self.assertIn("LIMIT 1000", c_det.sql)
        self.assertEqual(c_det.parameters, (sqli,))

        c_cmp = compiler.compile(
            ComparePeriodsQueryIR(
                table_id="orders",
                date_field="order_date",
                metric=AggregateMetricSpec("sales", AggregateFunction.SUM, "amount_inr"),
                current_start="2026-10-01",
                current_end="2026-10-31",
                previous_start="2026-09-01",
                previous_end="2026-09-30",
            ),
            schemas,
        )
        self.assertIn("current_value", c_cmp.sql)

        c_cb = compiler.compile(
            ContributionBreakdownQueryIR(
                table_id="orders",
                metric=AggregateMetricSpec("sales", AggregateFunction.SUM, "amount_inr"),
                dimension_field="status",
            ),
            schemas,
        )
        self.assertIn('GROUP BY "status"', c_cb.sql)

        c_dist = compiler.compile(DistributionQueryIR(table_id="orders", field_id="amount_inr"), schemas)
        self.assertIn("non_null_count", c_dist.sql)

        with self.assertRaises(SecurityPolicyError):
            validate_sql_ast("SELECT * FROM (SELECT * FROM duckdb_tables()) sub")

    def test_manual_override_cannot_bypass_hard_capability_filter(self) -> None:
        reg = ProviderRegistry()
        reg.register(OpenAiResponsesAdapter())
        m_no_struct = ModelProfile(
            "m-basic", ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES,
            (AgentRole.PLANNER,), 8000, 1000,
            ModelCapabilities(structured_output=False, tool_calling=True, streaming=True, structured_with_tools=False, reasoning_tokens=False),
            Decimal("1"), Decimal("2"), "2026-11-17",
        )
        p_oa = ProviderProfile(ProviderId.OPENAI, ApiProfile.OPENAI_RESPONSES, "oa", "https://api.openai.com/v1")
        ledger = AtomicBudgetLedger()
        routing = RoutingService(
            providers=reg,
            provider_profiles={ProviderId.OPENAI: p_oa},
            model_profiles={"m-basic": m_no_struct},
            circuit_breakers=CircuitBreakerManager(SystemClock()),
            budget_ledger=ledger,
            api_keys={ProviderId.OPENAI: "sk-test"},
        )
        policy = RoutingPolicy(1, RoutingPreset.BALANCED, True, {AgentRole.PLANNER: ("m-basic",)}, (ProviderId.OPENAI,))
        req = ModelRequest("r1", "run1", AgentRole.PLANNER, "m-basic", ({"role": "user", "content": "hi"},), structured_schema={"type": "object"})
        with self.assertRaises(ProviderError):
            routing.execute_step(request=req, policy=policy, estimated_input_tokens=100, manual_model_id="m-basic")

    def test_budget_idempotent_settle_and_export_size_cap(self) -> None:
        ledger = AtomicBudgetLedger()
        ledger.configure_run("r_idem", max_attempts=5, max_tokens=10_000, max_cost_usd=Decimal("5.00"))
        res = ledger.reserve("r_idem", "op1", "att1", 500, Decimal("0.10"), False, Decimal("5.00"))
        s1 = ledger.settle(res.reservation_id, 200, Decimal("0.04"))
        s2 = ledger.settle(res.reservation_id, 999, Decimal("0.99"))
        self.assertEqual(s1.actual_tokens, 200)
        self.assertEqual(s2.actual_tokens, 200)

        tiny_export = ExportService(DataLimits(max_export_bytes=20))
        with self.assertRaises(LimitExceededError):
            tiny_export.export_csv(("col",), (("a" * 50,),))


if __name__ == "__main__":
    unittest.main()
