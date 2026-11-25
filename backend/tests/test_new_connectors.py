import dataclasses
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import pyarrow as pa
import pyarrow.ipc as pa_ipc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.connectors.destination_policy import DestinationPolicy
from app.adapters.connectors.duckdb_file_connector import UploadedDuckDbConnector
from app.adapters.connectors.remote_file_connector import RemoteFileConnector, detect_remote_format
from app.adapters.connectors.rest_connector import RestJsonConnector, _next_link_from_header
from app.adapters.parsers.arrow_parser import ArrowIpcFileParser
from app.adapters.parsers.csv_parser import CsvFileParser, TsvFileParser
from app.adapters.parsers.registry import ParserRegistry
from app.api.support import _redacted_source_config
from app.application.ports.clock import SystemClock
from app.application.ports.connectors import ExtractionRequest
from app.application.services.orchestration_service import _strip_unknown_citations
from app.domain.errors import LimitExceededError, ValidationError
from app.domain.limits import DEFAULT_DATA_LIMITS

LOCAL_POLICY = DestinationPolicy(
    allowed_hosts=("localhost", "127.0.0.1"),
    allowed_ports=(8080, 9090),
    allow_private_local_for_tests=True,
    require_https=False,
)


def _parsers() -> ParserRegistry:
    reg = ParserRegistry()
    reg.register("csv", CsvFileParser())
    reg.register("tsv", TsvFileParser())
    reg.register("arrow", ArrowIpcFileParser())
    return reg


def _request(tables: tuple[str, ...] = (), limits=DEFAULT_DATA_LIMITS) -> ExtractionRequest:
    return ExtractionRequest("ws1", "src1", "job1", tables, {}, limits, 1)


class TestNewParsers(unittest.TestCase):
    def test_tsv_uses_tab_delimiter_and_keeps_leading_zeros(self) -> None:
        raw = b"order_id\tamount\n00101\t10.50\n00102\t20.00\n"
        parsed = TsvFileParser().parse(raw, "orders", DEFAULT_DATA_LIMITS)
        self.assertEqual(parsed[0].arrow_table["order_id"].to_pylist(), ["00101", "00102"])
        self.assertEqual(parsed[0].arrow_table.num_columns, 2)

    def test_arrow_ipc_file_and_stream(self) -> None:
        table = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})
        file_sink = io.BytesIO()
        with pa_ipc.new_file(file_sink, table.schema) as writer:
            writer.write_table(table)
        stream_sink = io.BytesIO()
        with pa_ipc.new_stream(stream_sink, table.schema) as writer:
            writer.write_table(table)
        for raw in (file_sink.getvalue(), stream_sink.getvalue()):
            parsed = ArrowIpcFileParser().parse(raw, "items", DEFAULT_DATA_LIMITS)
            self.assertEqual(parsed[0].row_count, 3)
        with self.assertRaises(ValidationError):
            ArrowIpcFileParser().parse(b"not arrow at all", "items", DEFAULT_DATA_LIMITS)


class TestRemoteFile(unittest.TestCase):
    def test_format_detection(self) -> None:
        sheets = "https://docs.google.com/spreadsheets/d/abc/export?format=csv&gid=0"
        self.assertEqual(detect_remote_format(sheets, None, "auto"), "csv")
        self.assertEqual(detect_remote_format("https://x.example/data/orders.parquet?X-Amz-Signature=1", None, None), "parquet")
        self.assertEqual(detect_remote_format("https://x.example/download", "application/json; charset=utf-8", None), "json")
        self.assertEqual(detect_remote_format("https://x.example/download", None, "tsv"), "tsv")
        with self.assertRaises(ValidationError):
            detect_remote_format("https://x.example/download", "application/octet-stream", None)

    def test_follows_redirect_and_only_sends_token_to_first_origin(self) -> None:
        seen: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.headers.get("host", "")
            seen.append((host, request.headers.get("authorization")))
            if host.startswith("localhost"):
                return httpx.Response(302, headers={"Location": "http://127.0.0.1:9090/files/orders.csv"})
            return httpx.Response(200, content=b"id,amount\n1,10\n2,20\n", headers={"content-type": "text/csv"})

        conn = RemoteFileConnector(_parsers(), SystemClock(), policy=LOCAL_POLICY, transport=httpx.MockTransport(handler))
        config = {"url": "http://localhost:8080/share/orders.csv", "format": "auto"}
        tables = conn.discover(config, {"bearer_token": "secret"})
        self.assertEqual(tables[0].name, "orders")
        self.assertEqual(tables[0].estimated_rows, 2)
        self.assertEqual(seen[0][1], "Bearer secret")
        self.assertIsNone(seen[1][1], "token must not follow a cross-origin redirect")

        batches, manifest = conn.extract(config, {}, _request(("orders",)), lambda: False)
        self.assertEqual(manifest.total_rows, 2)
        self.assertNotIn("share", str(manifest.source_config_summary))

    def test_html_page_is_rejected(self) -> None:
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, content=b"<!DOCTYPE html><html>Sign in</html>", headers={"content-type": "text/html"})
        )
        conn = RemoteFileConnector(_parsers(), SystemClock(), policy=LOCAL_POLICY, transport=transport)
        with self.assertRaises(ValidationError):
            conn.discover({"url": "http://localhost:8080/export?format=csv"}, {})

    def test_download_cap(self) -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"id\n" + b"1\n" * 100))
        conn = RemoteFileConnector(_parsers(), SystemClock(), policy=LOCAL_POLICY, transport=transport)
        tiny = dataclasses.replace(DEFAULT_DATA_LIMITS, max_uploaded_file_bytes=50)
        with self.assertRaises(LimitExceededError):
            conn.extract({"url": "http://localhost:8080/big.csv"}, {}, _request(limits=tiny), lambda: False)


class TestRestPagination(unittest.TestCase):
    def test_link_header_parser(self) -> None:
        header = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=5>; rel="last"'
        self.assertEqual(_next_link_from_header(header), "https://api.github.com/x?page=2")
        self.assertIsNone(_next_link_from_header('<https://api.github.com/x?page=1>; rel="prev"'))

    def test_link_header_mode_with_root_array(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "page=2" in str(request.url):
                return httpx.Response(200, json=[{"id": 3}])
            return httpx.Response(
                200,
                json=[{"id": 1}, {"id": 2}],
                headers={"Link": '<http://localhost:8080/items?page=2>; rel="next"'},
            )

        conn = RestJsonConnector(SystemClock(), policy=LOCAL_POLICY, transport=httpx.MockTransport(handler))
        config = {
            "url": "http://localhost:8080/items",
            "collection_path": "$",
            "safe_get_documented": True,
            "pagination_mode": "link_header",
        }
        _, manifest = conn.extract(config, {}, _request(("rest_records",)), lambda: False)
        self.assertEqual(manifest.total_rows, 3)

    def test_last_id_mode(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "starting_after=b" in str(request.url):
                return httpx.Response(200, json={"data": [{"id": "c"}], "has_more": False})
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}], "has_more": True})

        conn = RestJsonConnector(SystemClock(), policy=LOCAL_POLICY, transport=httpx.MockTransport(handler))
        config = {
            "url": "http://localhost:8080/v1/charges?limit=2",
            "collection_path": "data",
            "safe_get_documented": True,
            "pagination_mode": "last_id",
            "cursor_param": "starting_after",
        }
        _, manifest = conn.extract(config, {}, _request(("rest_records",)), lambda: False)
        self.assertEqual(manifest.total_rows, 3)


    def test_records_keep_numeric_types_and_json_for_nested_values(self) -> None:
        records = [
            {"id": "ch_1", "amount": 125000, "paid": True, "metadata": {"order": "00101"}},
            {"id": "ch_2", "amount": 50000, "paid": False, "metadata": {"order": "00102"}},
            {"id": "ch_3", "amount": None, "paid": True},
        ]
        conn = RestJsonConnector(
            SystemClock(), policy=LOCAL_POLICY, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=records))
        )
        config = {"url": "http://localhost:8080/charges", "collection_path": "$", "safe_get_documented": True}
        batches, manifest = conn.extract(config, {}, _request(("rest_records",)), lambda: False)
        types = {c.name: c.arrow_type for c in manifest.tables[0].columns}
        self.assertEqual(types["amount"], "int64")
        self.assertEqual(types["paid"], "string")
        table = pa_ipc.open_stream(list(batches)[0].arrow_ipc_bytes).read_all()
        self.assertEqual(table["amount"].to_pylist(), [125000, 50000, None])
        self.assertEqual(table["paid"].to_pylist(), ["true", "false", "true"])
        self.assertEqual(table["metadata"].to_pylist()[0], '{"order": "00101"}')


class TestRemoteFileDownloadReuse(unittest.TestCase):
    def test_extract_reuses_the_discover_download_once(self) -> None:
        fetches: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            fetches.append(request.headers.get("authorization"))
            return httpx.Response(200, content=b"id,amount\n1,10\n2,20\n", headers={"content-type": "text/csv"})

        conn = RemoteFileConnector(_parsers(), SystemClock(), policy=LOCAL_POLICY, transport=httpx.MockTransport(handler))
        config = {"url": "http://localhost:8080/files/orders.csv", "format": "auto"}
        conn.discover(config, {"bearer_token": "a"})
        _, manifest = conn.extract(config, {"bearer_token": "a"}, _request(("orders",)), lambda: False)
        self.assertEqual(manifest.total_rows, 2)
        self.assertEqual(len(fetches), 1)
        conn.extract(config, {"bearer_token": "a"}, _request(("orders",)), lambda: False)
        self.assertEqual(len(fetches), 2)
        conn.discover(config, {"bearer_token": "a"})
        conn.extract(config, {"bearer_token": "b"}, _request(("orders",)), lambda: False)
        self.assertEqual(fetches[-1], "Bearer b")


class TestDuckDbUpload(unittest.TestCase):
    def test_imports_base_tables_and_ignores_views(self) -> None:
        import duckdb

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "sample.duckdb"
            con = duckdb.connect(str(db_path))
            con.execute("CREATE TABLE orders (id VARCHAR, amount DECIMAL(10,2), tags VARCHAR[])")
            con.execute("INSERT INTO orders VALUES ('001', 10.50, ['a','b']), ('002', 20.00, [])")
            con.execute("CREATE VIEW big_orders AS SELECT * FROM orders WHERE amount > 15")
            con.close()

            conn = UploadedDuckDbConnector(SystemClock())
            probe = conn.probe({"file_path": str(db_path)}, {})
            self.assertTrue(probe.ok)
            self.assertEqual(probe.details["view_count"], "1")
            tables = conn.discover({"file_path": str(db_path)}, {})
            self.assertEqual([t.name for t in tables], ["orders"])
            batches, manifest = conn.extract({"file_path": str(db_path)}, {}, _request(("orders",)), lambda: False)
            self.assertEqual(manifest.total_rows, 2)
            tags_type = next(c.arrow_type for c in manifest.tables[0].columns if c.name == "tags")
            self.assertEqual(tags_type, "string")


class TestRestartAndRefresh(unittest.TestCase):
    def test_active_runs_resume_and_pin_refresh_runs_twice(self) -> None:
        from fastapi.testclient import TestClient

        from app.application.ports.providers import ModelResponse
        from app.adapters.persistence.database import create_db_engine, create_session_factory
        from app.api.main import create_app
        from app.bootstrap.container import build_container
        from app.domain.providers import ProviderId

        class RestartGateway:
            provider_id = ProviderId.OPENAI

            def invoke(self, request, provider_profile, model_profile, api_key):
                return ModelResponse(
                    provider_id=self.provider_id,
                    model_id=model_profile.model_id,
                    text="The investigation resumed successfully.",
                    input_tokens=32,
                    output_tokens=8,
                )

        sf = create_session_factory(create_db_engine("sqlite:///:memory:"))
        with tempfile.TemporaryDirectory() as td:
            app1 = create_app(build_container(artifact_root=td), session_factory=sf)
            run = app1.state.repo_runs.create_run(
                run_id="run_stale", workspace_id="ws1", conversation_id="conv_ws1", mode="quick", status="running", question="q"
            )
            self.assertEqual(run["status"], "running")

            container = build_container(artifact_root=td)
            container.providers.register(RestartGateway())
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-only-key"
            app2 = create_app(container, session_factory=sf)
            with patch.dict(os.environ, {"RUN_EXECUTION_MODE": "inline", "AGENT_RUNTIME": "deepagents"}):
                with TestClient(app2) as client:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        stale = app2.state.repo_runs.get_run("run_stale")
                        if stale["status"] not in {"queued", "running"}:
                            break
                        time.sleep(0.01)
                    self.assertEqual(stale["status"], "completed")
                    events = app2.state.repo_events.list_after("run_stale", 0)
                    self.assertEqual(events[-1].type.value, "run.finished")

                    app2.state.repo_runs.create_run(
                        run_id="run_other", workspace_id="ws1", conversation_id="conv_ws1", mode="quick", status="completed", question="q2"
                    )
                    for pin_id, question, run_id in (("pin_a", "first question", "run_stale"), ("pin_b", "second question", "run_other")):
                        app2.state.repo_pins.create_pin(pin_id=pin_id, workspace_id="ws1", title=question, question_text=question, run_id=run_id)
                    first = client.post("/api/v1/workspaces/ws1/pins/pin_a/refresh")
                    second = client.post("/api/v1/workspaces/ws1/pins/pin_b/refresh")
                    self.assertEqual(first.status_code, 202)
                    self.assertEqual(second.status_code, 202, second.text)
                    self.assertNotEqual(first.json()["refresh_run_id"], second.json()["refresh_run_id"])


class TestSafety(unittest.TestCase):
    def test_unknown_citations_are_removed(self) -> None:
        text = "Revenue was 10 [ev_aaaaaaaaaaaa, ev_bbbbbbbbbbbb] and fees 2 [ev_cccccccccccc]."
        cleaned, dropped = _strip_unknown_citations(text, {"ev_aaaaaaaaaaaa"})
        self.assertEqual(cleaned, "Revenue was 10 [ev_aaaaaaaaaaaa] and fees 2 .")
        self.assertEqual(sorted(dropped), ["ev_bbbbbbbbbbbb", "ev_cccccccccccc"])

    def test_source_config_redaction(self) -> None:
        safe = _redacted_source_config(
            {"raw_base64": "AAAA", "format": "csv", "url": "https://user:pw@bucket.s3.amazonaws.com/a.csv?X-Amz-Signature=s"}
        )
        self.assertNotIn("raw_base64", safe)
        self.assertEqual(safe["url"], "https://bucket.s3.amazonaws.com/a.csv")
        self.assertEqual(safe["format"], "csv")


if __name__ == "__main__":
    unittest.main()
