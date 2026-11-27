import unittest

import httpx

from app.adapters.connectors.rest_connector import RestJsonConnector
from app.application.ports.clock import SystemClock
from tests.test_new_connectors import LOCAL_POLICY


class TestRestDiscovery(unittest.TestCase):
    def test_a_field_that_first_appears_late_is_discovered(self) -> None:
        records = [{"id": i, "amount": i * 10} for i in range(60)] + [{"id": 60, "amount": 600, "refund_reason": "late field"}]
        conn = RestJsonConnector(SystemClock(), policy=LOCAL_POLICY, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=records)))
        tables = conn.discover({"url": "http://localhost:8080/charges", "collection_path": "$", "safe_get_documented": True}, {})
        self.assertEqual([c.name for c in tables[0].columns], ["id", "amount", "refund_reason"])
        self.assertEqual(tables[0].estimated_rows, 61)


if __name__ == "__main__":
    unittest.main()
