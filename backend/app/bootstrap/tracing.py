import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

_provider: TracerProvider | None = None


def tracing_enabled() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))


def configure_tracing(service_name: str, exporter: SpanExporter | None = None) -> bool:
    global _provider
    if _provider is not None:
        return True
    if exporter is None and not tracing_enabled():
        return False
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", service_name)}))
    provider.add_span_processor(BatchSpanProcessor(exporter or OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _provider = provider
    return True


def instrument_app(app: FastAPI) -> None:
    if configure_tracing("ledgerline-api"):
        FastAPIInstrumentor.instrument_app(app, excluded_urls="api/v1/health")
