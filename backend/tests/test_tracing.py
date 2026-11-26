import json
import logging
import tempfile
import unittest

from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse
from app.bootstrap import tracing
from app.bootstrap.container import build_container
from app.bootstrap.logging_setup import ContextFilter, JsonFormatter
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile

EXPORTER = InMemorySpanExporter()


def attach_exporter(exporter: InMemorySpanExporter) -> bool:
    if tracing._provider is None:
        return tracing.configure_tracing("ledgerline-test", exporter=exporter)
    tracing._provider.add_span_processor(SimpleSpanProcessor(exporter))
    return True


class _Gateway:
    provider_id = ProviderId.OPENAI

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="Done.", input_tokens=7, output_tokens=3)


class TestTracing(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not attach_exporter(EXPORTER):
            raise unittest.SkipTest("tracing could not be configured")

    def _spans(self):
        provider = trace.get_tracer_provider()
        provider.force_flush()
        return EXPORTER.get_finished_spans()

    def test_requests_and_model_calls_are_traced(self) -> None:
        EXPORTER.clear()
        with tempfile.TemporaryDirectory() as td:
            container = build_container(artifact_root=td)
            container.providers.register(_Gateway())
            container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            client = TestClient(create_app(container))
            run_id = client.post("/api/v1/workspaces/ws1/runs", json={"question": "Fees?", "execute_inline": True}).json()["run_id"]
            client.get("/api/v1/health")
        spans = self._spans()
        names = [s.name for s in spans]
        self.assertTrue(any(n.startswith("POST /api/v1/workspaces/{workspace_id}/runs") for n in names), names)
        self.assertFalse(any("/health" in n for n in names))
        model_calls = [s for s in spans if s.name == "model.call"]
        self.assertTrue(model_calls)
        self.assertEqual(model_calls[0].attributes["llm.provider"], "openai")
        self.assertEqual(model_calls[0].attributes["run.id"], run_id)
        self.assertEqual(model_calls[0].attributes["llm.input_tokens"], 7)

    def test_log_lines_carry_the_trace_id(self) -> None:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("work") as span:
            record = logging.LogRecord("ledgerline", logging.INFO, __file__, 1, "inside", (), None)
            ContextFilter().filter(record)
            expected = format(span.get_span_context().trace_id, "032x")
        self.assertEqual(json.loads(JsonFormatter().format(record))["trace_id"], expected)


if __name__ == "__main__":
    unittest.main()
