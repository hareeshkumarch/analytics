from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.models import RunMode
from app.domain.providers import ProviderId, RoutingPreset


class ApiResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProviderStatusOut(ApiResponse):
    provider_id: ProviderId
    api_profile: str
    endpoint_profile_id: str
    configured: bool
    key_status: Literal["ready", "needs_key", "missing"]


class ModelStatusOut(ApiResponse):
    model_id: str
    provider_id: str
    roles: list[str]
    enabled: bool
    verification_date: str


class ProvidersOut(ApiResponse):
    workspace_id: str
    providers: list[ProviderStatusOut]
    models: list[ModelStatusOut]


class RoutingPolicyOut(ApiResponse):
    workspace_id: str
    policy_version: int
    preset: RoutingPreset
    fallback_enabled: bool
    role_routes: dict[str, list[str]]
    hard_cost_ceiling_usd: str | None


class WorkspaceSettingsOut(ApiResponse):
    workspace_id: str
    business_timezone: str


class CreateRunOut(ApiResponse):
    run_id: str
    status: str
    status_url: str
    events_url: str


class ChartOut(ApiResponse):
    chart_id: str
    chart_type: str
    title: str
    x_field: str
    y_fields: list[str]
    evidence_id: str
    rows: list[list[str | float | None]]
    truncated: bool = False


class CriterionOut(ApiResponse):
    criterion: str
    status: Literal["verified", "failed", "blocked", "not_applicable"]
    evidence_ids: list[str] = []
    detail: str = ""


class RunAnswerOut(ApiResponse):
    answer_id: str | None = None
    summary_markdown: str | None = None
    charts: list[ChartOut] | None = None
    outcome: str | None = None
    criteria: list[CriterionOut] | None = None


class RunOut(ApiResponse):
    run_id: str
    workspace_id: str
    status: str
    outcome: str | None = None
    mode: RunMode | None = None
    question: str | None = None
    conversation_id: str | None = None
    snapshot_id: str | None = None
    semantic_version_id: str | None = None
    answer: RunAnswerOut | None = None
    error: str | None = None
    created_at: str | None = None
    clarification_prompt: str | None = None
    clarification_revision: int | None = None


class RunPageOut(ApiResponse):
    runs: list[RunOut]
    next_before: str | None


class ColumnDetailOut(ApiResponse):
    name: str
    type: str
    nullable: bool | None = None
    leading_zero_id: bool | None = None
    currency: str | None = None


class TableDetailOut(ApiResponse):
    name: str
    row_count: int
    normalized_bytes: int | None = None
    columns: list[ColumnDetailOut]


class ReimportOut(ApiResponse):
    connector_id: str | None
    values: dict[str, str | bool | int | float]
    selected_tables: list[str]


class SnapshotOut(ApiResponse):
    snapshot_id: str
    source_id: str
    version: int
    total_rows: int
    tables: list[str]
    semantic_version_id: str | None = None
    approved: bool | None = None
    created_at: str | None = None
    source_family: str | None = None
    total_normalized_bytes: int | None = None
    consistency_level: str | None = None
    sha256: str | None = None
    table_details: list[TableDetailOut] | None = None
    reimport: ReimportOut | None = None


class SnapshotListOut(ApiResponse):
    snapshots: list[SnapshotOut]


class PinOut(ApiResponse):
    pin_id: str
    title: str
    question_text: str
    run_id: str
    version: int
    created_at: str
    auto_refresh: bool = False
    latest_run_id: str | None = None


class PinListOut(ApiResponse):
    pins: list[PinOut]


class RunUsageOut(ApiResponse):
    run_id: str
    used_attempts: int
    total_tokens: int
    total_cost_usd: str
    incomplete: bool
    mode: RunMode | None = None
    status: str | None = None
    created_at: str | None = None
    question: str | None = None
    error: str | None = None


class ModelUsageOut(ApiResponse):
    provider_id: str
    model_id: str
    calls: int
    unsettled_calls: int
    total_tokens: int
    total_cost_usd: str
    cost_unknown: bool
    avg_latency_ms: int | None


class ToolUsageOut(ApiResponse):
    tool: str
    calls: int
    failed_calls: int
    invalid_calls: int
    duplicate_calls: int
    timeouts: int
    avg_duration_ms: int
    calls_per_task: float


class UsageOut(ApiResponse):
    workspace_id: str
    run_count: int
    runs: list[RunUsageOut]
    models: list[ModelUsageOut]
    tools: list[ToolUsageOut] = []


class ToolExecutionOut(ApiResponse):
    execution_id: str
    run_id: str
    task_id: str
    tool: str
    tool_version: str
    args_fingerprint: str
    status: str
    error_code: str | None
    duration_ms: int
    evidence_id: str | None
    created_at: str


class ToolExecutionListOut(ApiResponse):
    run_id: str
    executions: list[ToolExecutionOut]
