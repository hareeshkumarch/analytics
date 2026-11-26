import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pyarrow as pa

from app.adapters.connectors.bigquery_connector import BigQueryConnector
from app.application.ports.clock import SystemClock
from app.application.ports.connectors import ExtractionRequest
from app.domain.limits import DEFAULT_DATA_LIMITS

TABLES = {
    "p.sales.orders": [{"order_id": "1", "amount": 10}, {"order_id": "2", "amount": 20}],
    "p.sales.refunds": [{"order_id": "2", "amount": 5}],
}


def _field(name: str, kind: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, field_type=kind, mode="NULLABLE", fields=())


class FakeClient:
    def get_table(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, schema=[_field("order_id", "STRING"), _field("amount", "INTEGER")])

    def list_rows(self, table: SimpleNamespace, **_: object) -> SimpleNamespace:
        return SimpleNamespace(pages=[TABLES[table.name]])


class TestBigQueryMultiTable(unittest.TestCase):
    def test_every_selected_table_is_extracted_and_in_the_manifest(self) -> None:
        connector = BigQueryConnector(SystemClock())
        request = ExtractionRequest("ws1", "src1", "job1", ("sales.orders", "sales.refunds"), {}, DEFAULT_DATA_LIMITS, 1)
        with patch.object(BigQueryConnector, "_client", return_value=FakeClient()):
            batches, manifest = connector.extract({"project_id": "p", "dataset_id": "sales"}, {}, request, lambda: False)
        batches = list(batches)
        self.assertEqual([b.table_name for b in batches], ["sales.orders", "sales.refunds"])
        self.assertEqual([t.table_name for t in manifest.tables], ["sales.orders", "sales.refunds"])
        self.assertEqual(manifest.total_rows, 3)
        rows = [pa.ipc.open_stream(b.arrow_ipc_bytes).read_all().num_rows for b in batches]
        self.assertEqual(rows, [2, 1])


if __name__ == "__main__":
    unittest.main()
