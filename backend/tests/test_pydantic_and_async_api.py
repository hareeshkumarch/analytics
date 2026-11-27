import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.main import create_app
from app.api.schemas import QueryIREnvelope
from app.bootstrap.container import build_container


class TestPydanticAndAsyncApi(unittest.TestCase):
    def test_pydantic_extra_fields_rejected_with_error_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            app = create_app(c)
            client = TestClient(app)

            r = client.post(
                "/api/v1/workspaces/ws1/runs",
                json={"question": "valid", "mode": "quick", "unexpected_hack_field": "malicious"},
            )
            self.assertEqual(r.status_code, 422)
            body = r.json()
            self.assertEqual(body["code"], "validation_error")
            self.assertIn("body.unexpected_hack_field", body["field_errors"])

    def test_discriminated_query_ir_pydantic_envelope(self) -> None:
        env = QueryIREnvelope.model_validate(
            {
                "query": {
                    "operation": "aggregate",
                    "table_id": "orders",
                    "metrics": [{"alias": "total", "func": "sum", "field_id": "amount_inr"}],
                    "limit": 100,
                }
            }
        )
        dom = env.to_domain()
        self.assertEqual(dom.operation, "aggregate")
        self.assertEqual(dom.table_id, "orders")

    def test_global_queue_limit_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            app = create_app(c)
            client = TestClient(app)

            for i in range(10):
                res = client.post(
                    "/api/v1/workspaces/ws1/runs",
                    json={"question": f"q{i}", "mode": "quick", "background_async": False},
                )
                self.assertEqual(res.status_code, 202)

            res_overflow = client.post(
                "/api/v1/workspaces/ws1/runs",
                json={"question": "q11", "mode": "quick", "background_async": False},
            )
            self.assertEqual(res_overflow.status_code, 429)


if __name__ == "__main__":
    unittest.main()
