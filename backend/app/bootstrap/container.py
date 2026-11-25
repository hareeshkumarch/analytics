from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path

import yaml

from app.adapters.connectors.file_connector import UploadedFileConnector
from app.adapters.connectors.airtable_connector import AirtableConnector
from app.adapters.connectors.bigquery_connector import BigQueryConnector
from app.adapters.connectors.duckdb_file_connector import UploadedDuckDbConnector
from app.adapters.connectors.mysql_connector import MySqlConnector
from app.adapters.connectors.postgres_connector import PostgresConnector
from app.adapters.connectors.registry import ConnectorRegistry
from app.adapters.connectors.remote_file_connector import RemoteFileConnector
from app.adapters.connectors.rest_connector import RestJsonConnector
from app.adapters.connectors.sheets_connector import GoogleSheetsConnector
from app.adapters.connectors.sqlite_connector import UploadedSqliteConnector
from app.adapters.parsers.arrow_parser import ArrowIpcFileParser
from app.adapters.parsers.csv_parser import CsvFileParser, TsvFileParser
from app.adapters.parsers.json_parser import JsonFileParser
from app.adapters.parsers.parquet_parser import ParquetFileParser
from app.adapters.parsers.registry import ParserRegistry
from app.adapters.parsers.xlsx_parser import XlsxFileParser
from app.adapters.providers.anthropic_adapter import AnthropicMessagesAdapter
from app.adapters.providers.gemini_adapter import GeminiInteractionsAdapter
from app.adapters.providers.groq_adapter import GroqChatAdapter
from app.adapters.providers.kimi_adapter import KimiChatAdapter
from app.adapters.providers.nim_adapter import NvidiaNimAdapter
from app.adapters.providers.openai_adapter import OpenAiResponsesAdapter
from app.adapters.providers.registry import ProviderRegistry
from app.adapters.providers.xai_adapter import XaiResponsesAdapter
from app.adapters.storage.local_fs import LocalArtifactStore, LocalUploadStore
from app.application.ports.clock import SystemClock
from app.application.ports.providers import BudgetLedger
from app.application.services.budget_service import AtomicBudgetLedger
from app.application.services.export_service import ExportService
from app.application.services.routing_service import CircuitBreakerManager, RoutingService
from app.domain.limits import DEFAULT_DATA_LIMITS
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


@dataclass
class AppContainer:
    clock: SystemClock
    artifacts: LocalArtifactStore
    uploads: LocalUploadStore
    parsers: ParserRegistry
    connectors: ConnectorRegistry
    providers: ProviderRegistry
    provider_profiles: dict[ProviderId, ProviderProfile]
    model_profiles: dict[str, ModelProfile]
    default_routing_policy: RoutingPolicy
    circuits: CircuitBreakerManager
    budget_ledger: BudgetLedger
    routing_service: RoutingService
    export_service: ExportService


def load_yaml_configs(config_dir: Path) -> tuple[dict[ProviderId, ProviderProfile], dict[str, ModelProfile], RoutingPolicy]:
    prov_data = yaml.safe_load((config_dir / "providers.yaml").read_text(encoding="utf-8"))
    model_data = yaml.safe_load((config_dir / "models.yaml").read_text(encoding="utf-8"))
    route_data = yaml.safe_load((config_dir / "routing.yaml").read_text(encoding="utf-8"))["routing"]

    prov_profiles: dict[ProviderId, ProviderProfile] = {}
    for p in prov_data["providers"]:
        pid = ProviderId(p["provider_id"])
        prov_profiles[pid] = ProviderProfile(
            provider_id=pid,
            api_profile=ApiProfile(p["api_profile"]),
            endpoint_profile_id=p["endpoint_profile_id"],
            base_url=p["base_url"],
            default_timeout_seconds=int(p.get("default_timeout_seconds", 30)),
            max_concurrent_requests=int(p.get("max_concurrent_requests", 4)),
            allow_schema_egress=bool(p.get("allow_schema_egress", True)),
            allow_raw_record_egress=bool(p.get("allow_raw_record_egress", False)),
        )

    model_profiles: dict[str, ModelProfile] = {}
    for m in model_data["models"]:
        caps = m["capabilities"]
        model_profiles[m["model_id"]] = ModelProfile(
            model_id=m["model_id"],
            provider_id=ProviderId(m["provider_id"]),
            api_profile=ApiProfile(m["api_profile"]),
            roles=tuple(AgentRole(r) for r in m["roles"]),
            context_window=int(m["context_window"]),
            max_output_tokens=int(m["max_output_tokens"]),
            capabilities=ModelCapabilities(
                structured_output=bool(caps["structured_output"]),
                tool_calling=bool(caps["tool_calling"]),
                streaming=bool(caps["streaming"]),
                structured_with_tools=bool(caps["structured_with_tools"]),
                reasoning_tokens=bool(caps["reasoning_tokens"]),
                sampling_params=bool(caps.get("sampling_params", True)),
                refusal_fallback=bool(caps.get("refusal_fallback", False)),
            ),
            input_price_per_1m=Decimal(str(m["input_price_per_1m"])) if m.get("input_price_per_1m") else None,
            output_price_per_1m=Decimal(str(m["output_price_per_1m"])) if m.get("output_price_per_1m") else None,
            verification_date=str(m["verification_date"]),
            enabled=bool(m.get("enabled", True)),
        )

    role_routes = {
        AgentRole(role): tuple(models)
        for role, models in route_data.get("role_routes", {}).items()
    }
    policy = RoutingPolicy(
        policy_version=int(route_data["policy_version"]),
        preset=RoutingPreset(route_data["preset"]),
        fallback_enabled=bool(route_data["fallback_enabled"]),
        role_routes=role_routes,
        allowed_providers=tuple(ProviderId(p) for p in route_data.get("allowed_providers", [])),
        hard_cost_ceiling_usd=(
            Decimal(str(route_data["hard_cost_ceiling_usd"]))
            if route_data.get("hard_cost_ceiling_usd") is not None
            else None
        ),
    )
    return prov_profiles, model_profiles, policy


def build_container(
    *,
    artifact_root: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> AppContainer:
    clock = SystemClock()
    root = Path(artifact_root) if artifact_root else Path(os.environ.get("ARTIFACT_ROOT", "/tmp/agentic_analytics_artifacts"))
    artifacts = LocalArtifactStore(root)

    parsers = ParserRegistry()
    parsers.register("csv", CsvFileParser())
    parsers.register("xlsx", XlsxFileParser())
    parsers.register("json", JsonFileParser())
    parsers.register("ndjson", JsonFileParser())
    parsers.register("parquet", ParquetFileParser())
    parsers.register("tsv", TsvFileParser())
    parsers.register("arrow", ArrowIpcFileParser())

    connectors = ConnectorRegistry()
    file_conn = UploadedFileConnector(parsers, clock)
    connectors.register(file_conn.family, file_conn, file_conn)
    sqlite_conn = UploadedSqliteConnector(clock)
    connectors.register(sqlite_conn.family, sqlite_conn, sqlite_conn)
    pg_conn = PostgresConnector(clock)
    connectors.register(pg_conn.family, pg_conn, pg_conn)
    mysql_conn = MySqlConnector(clock)
    connectors.register(mysql_conn.family, mysql_conn, mysql_conn)
    sheets_conn = GoogleSheetsConnector(clock)
    connectors.register(sheets_conn.family, sheets_conn, sheets_conn)
    rest_conn = RestJsonConnector(clock)
    connectors.register(rest_conn.family, rest_conn, rest_conn)
    airtable_conn = AirtableConnector(clock)
    connectors.register(airtable_conn.family, airtable_conn, airtable_conn)
    bigquery_conn = BigQueryConnector(clock)
    connectors.register(bigquery_conn.family, bigquery_conn, bigquery_conn)
    remote_file_conn = RemoteFileConnector(parsers, clock)
    connectors.register(remote_file_conn.family, remote_file_conn, remote_file_conn)
    duckdb_conn = UploadedDuckDbConnector(clock)
    connectors.register(duckdb_conn.family, duckdb_conn, duckdb_conn)

    providers = ProviderRegistry()
    for gw in (
        OpenAiResponsesAdapter(),
        AnthropicMessagesAdapter(),
        GeminiInteractionsAdapter(),
        GroqChatAdapter(),
        XaiResponsesAdapter(),
        KimiChatAdapter(),
        NvidiaNimAdapter(),
    ):
        providers.register(gw)

    cfg_dir = Path(config_dir or Path(__file__).resolve().parents[2] / "config")
    prov_profiles, model_profiles, policy = load_yaml_configs(cfg_dir)

    api_keys: dict[ProviderId, str] = {
        ProviderId.OPENAI: os.environ.get("OPENAI_API_KEY", ""),
        ProviderId.ANTHROPIC: os.environ.get("ANTHROPIC_API_KEY", ""),
        ProviderId.GEMINI: os.environ.get("GEMINI_API_KEY", ""),
        ProviderId.GROQ: os.environ.get("GROQ_API_KEY", ""),
        ProviderId.XAI: os.environ.get("XAI_API_KEY", ""),
        ProviderId.KIMI: os.environ.get("MOONSHOT_API_KEY", ""),
        ProviderId.NVIDIA_NIM: os.environ.get("NVIDIA_NIM_API_KEY", ""),
    }

    circuits = CircuitBreakerManager(clock)
    ledger = AtomicBudgetLedger()
    routing = RoutingService(
        providers=providers,
        provider_profiles=prov_profiles,
        model_profiles=model_profiles,
        circuit_breakers=circuits,
        budget_ledger=ledger,
        api_keys=api_keys,
    )
    export_service = ExportService(DEFAULT_DATA_LIMITS)

    return AppContainer(
        clock=clock,
        artifacts=artifacts,
        uploads=LocalUploadStore(root),
        parsers=parsers,
        connectors=connectors,
        providers=providers,
        provider_profiles=prov_profiles,
        model_profiles=model_profiles,
        default_routing_policy=policy,
        circuits=circuits,
        budget_ledger=ledger,
        routing_service=routing,
        export_service=export_service,
    )
