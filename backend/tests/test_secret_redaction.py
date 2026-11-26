import io
import json
import logging
import tempfile
import unittest

import httpx
from fastapi.testclient import TestClient
from opentelemetry import trace

from app.adapters.providers.openai_adapter import OpenAiResponsesAdapter
from app.api.main import create_app
from app.bootstrap.container import build_container
from app.bootstrap.logging_setup import ContextFilter, JsonFormatter, RedactingFormatter, redact_secrets
from app.domain.providers import ProviderId
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.test_tracing import attach_exporter

API_KEY = "sk-proj-LEAKCHECK0123456789abcdef"
DB_URL = "postgresql://analyst:Hunter2Secret@db.internal:5432/sales"


class TestSecretsStayOutOfLogsAndTraces(unittest.TestCase):
    def test_redaction_patterns(self) -> None:
        for secret, text in (
            (API_KEY, f"Incorrect API key provided: {API_KEY}."),
            ("eyJhbGciOiJIUzI1NiJ9.payload", "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload"),
            ("Hunter2Secret", f"could not connect to {DB_URL}"),
            ("AIzaSyA1234567890abcdefghijk", "key=AIzaSyA1234567890abcdefghijk"),
            ("gsk_abcdefghijklmnop", "groq key gsk_abcdefghijklmnop rejected"),
            ("MyJsonPass99", '{"password": "MyJsonPass99", "api_key": "MyJsonKey99"}'),
            ("MyJsonKey99", json.dumps({"msg": '{"api_key": "MyJsonKey99"}'})),
        ):
            self.assertNotIn(secret, redact_secrets(text))
        self.assertEqual(redact_secrets("North total 12,345.67 for 2024"), "North total 12,345.67 for 2024")

    def test_provider_error_echoing_the_key_is_redacted_everywhere(self) -> None:
        exporter = InMemorySpanExporter()
        if not attach_exporter(exporter):
            self.skipTest("tracing could not be configured")
        body = {"error": {"message": f"Incorrect API key provided: {API_KEY}. Connection {DB_URL}"}}
        stream = io.StringIO()
        handlers = []
        for formatter in (RedactingFormatter("%(levelname)s %(name)s %(message)s"), JsonFormatter()):
            handler = logging.StreamHandler(stream)
            handler.addFilter(ContextFilter())
            handler.setFormatter(formatter)
            logging.getLogger().addHandler(handler)
            handlers.append(handler)
        try:
            with tempfile.TemporaryDirectory() as td:
                container = build_container(artifact_root=td)
                container.providers.register(OpenAiResponsesAdapter(transport=httpx.MockTransport(lambda req: httpx.Response(401, json=body))))
                container.routing_service._api_keys[ProviderId.OPENAI] = API_KEY
                client = TestClient(create_app(container))
                run_id = client.post("/api/v1/workspaces/ws1/runs", json={"question": "Fees?", "execute_inline": True}).json()["run_id"]
                run = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}").text
                events = client.get(f"/api/v1/workspaces/ws1/runs/{run_id}/events?after=0").text
        finally:
            for handler in handlers:
                logging.getLogger().removeHandler(handler)
        trace.get_tracer_provider().force_flush()
        spans = json.dumps(
            [{"name": s.name, "attributes": dict(s.attributes or {}), "events": [dict(e.attributes or {}) for e in s.events]} for s in exporter.get_finished_spans()],
            default=str,
        )
        logs = stream.getvalue()
        self.assertIn("returned HTTP 401", logs)
        self.assertIn("[REDACTED]", logs)
        for secret in (API_KEY, "Hunter2Secret"):
            for name, text in (("logs", logs), ("spans", spans), ("run", run), ("events", events)):
                self.assertNotIn(secret, text, name)


if __name__ == "__main__":
    unittest.main()
