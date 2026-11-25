import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.connectors.destination_policy import DestinationPolicy, validate_http_url, validate_ip_address
from app.adapters.connectors.sqlite_connector import UploadedSqliteConnector
from app.adapters.parsers.csv_parser import CsvFileParser
from app.adapters.parsers.json_parser import JsonFileParser
from app.adapters.parsers.parquet_parser import ParquetFileParser
from app.adapters.parsers.xlsx_parser import XlsxFileParser
from app.adapters.storage.local_fs import LocalArtifactStore
from app.application.ports.clock import SystemClock
from app.application.ports.connectors import ExtractionRequest
from app.domain.errors import SecurityPolicyError, ValidationError
from app.domain.limits import DEFAULT_DATA_LIMITS


class TestParsersAndConnectors(unittest.TestCase):
    def test_csv_preserves_leading_zero_ids(self) -> None:
        raw = b"order_id,amount\n00101,1000.00\n00102,2000.00\n"
        parsed = CsvFileParser().parse(raw, "orders", DEFAULT_DATA_LIMITS)
        self.assertEqual(len(parsed), 1)
        tbl = parsed[0].arrow_table
        self.assertEqual(tbl["order_id"].to_pylist(), ["00101", "00102"])
        self.assertTrue(parsed[0].columns[0].is_leading_zero_id)

    def test_xlsx_missing_formula_cache_is_none_not_zero(self) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["item", "calc"])
        ws.append(["A", "=1+1"])
        buf = io.BytesIO()
        wb.save(buf)
        parsed = XlsxFileParser().parse(buf.getvalue(), "sheet1", DEFAULT_DATA_LIMITS)
        self.assertEqual(parsed[0].arrow_table["calc"].to_pylist(), [None])

    def test_json_nested_array_requires_explicit_mapping(self) -> None:
        raw = json.dumps([{"id": 1, "items": [{"sku": "X"}]}]).encode("utf-8")
        with self.assertRaises(ValidationError):
            JsonFileParser().parse(raw, "orders", DEFAULT_DATA_LIMITS)
        parsed = JsonFileParser().parse(
            raw, "orders", DEFAULT_DATA_LIMITS, options={"array_mappings": {"items": "child_table"}}
        )
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[1].table_name, "orders__items")

    def test_parquet_preserves_decimal_metadata(self) -> None:
        from decimal import Decimal
        tbl = pa.table({"amount": pa.array([Decimal("123.45")], type=pa.decimal128(10, 2))})
        buf = io.BytesIO()
        pq.write_table(tbl, buf)
        parsed = ParquetFileParser().parse(buf.getvalue(), "tx", DEFAULT_DATA_LIMITS)
        self.assertEqual(parsed[0].columns[0].decimal_precision, 10)
        self.assertEqual(parsed[0].columns[0].decimal_scale, 2)

    def test_sqlite_rejects_views_and_extracts_ordinary_tables(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "clean.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE orders (id TEXT, amount INTEGER)")
            conn.execute("INSERT INTO orders VALUES ('001', 100)")
            conn.commit()
            conn.close()

            connector = UploadedSqliteConnector(SystemClock())
            probe = connector.probe({"file_path": str(db_path)}, {})
            self.assertTrue(probe.ok)
            req = ExtractionRequest("ws1", "src1", "job1", ("orders",), {}, DEFAULT_DATA_LIMITS, 1)
            batches, manifest = connector.extract({"file_path": str(db_path)}, {}, req, lambda: False)
            self.assertEqual(manifest.total_rows, 1)
            self.assertEqual(len(list(batches)), 1)

            conn2 = sqlite3.connect(db_path)
            conn2.execute("CREATE VIEW v_orders AS SELECT * FROM orders")
            conn2.commit()
            conn2.close()
            with self.assertRaises(SecurityPolicyError):
                connector.probe({"file_path": str(db_path)}, {})

    def test_ssrf_metadata_never_overridden_by_local_test_flag(self) -> None:
        with self.assertRaises(SecurityPolicyError):
            validate_ip_address("169.254.169.254", allow_private_local=True)
        with self.assertRaises(SecurityPolicyError):
            validate_ip_address("::ffff:169.254.169.254", allow_private_local=True)
        with self.assertRaises(SecurityPolicyError):
            validate_ip_address("fe80::1", allow_private_local=True)

        policy = DestinationPolicy(allow_private_local_for_tests=False, require_https=True)
        with self.assertRaises(SecurityPolicyError):
            validate_http_url("http://127.0.0.1:8080/api", policy)

    def test_artifact_store_staging_publication_and_workspace_limits(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LocalArtifactStore(td)
            staged = store.stage_bytes("job1", "data.arrow", b"hello-arrow", "application/octet-stream")
            pub = store.publish_staged("job1", staged, "art_1")
            self.assertEqual(pub.byte_size, 11)
            self.assertEqual(store.read_bytes("art_1"), b"hello-arrow")
            store.cleanup_job_staging("job1")


if __name__ == "__main__":
    unittest.main()
