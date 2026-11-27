from decimal import Decimal
import os
from dataclasses import dataclass, replace

from app.domain.models import RunMode


@dataclass(frozen=True, slots=True)
class DataLimits:
    max_uploaded_file_bytes: int = 40_000_000
    max_normalized_source_bytes: int = 40_000_000
    max_workspace_retained_raw_bytes: int = 200_000_000
    max_published_snapshot_bytes: int = 512 * 1024 * 1024
    max_rows_per_dataset: int = 1_000_000
    max_columns_per_dataset: int = 200
    max_workbook_sheets: int = 20
    max_chars_per_cell: int = 1_000_000
    max_expanded_xlsx_bytes: int = 200_000_000
    max_expanded_file_bytes: int = 200_000_000
    ingestion_deadline_seconds: int = 300
    max_preview_rows: int = 1_000
    max_export_bytes: int = 10 * 1024 * 1024
    arrow_batch_max_rows: int = 4_096

    rest_max_completed_pages: int = 50
    rest_max_http_attempts: int = 100
    rest_max_decoded_bytes_per_response: int = 10_000_000
    rest_max_decoded_bytes_per_job: int = 80_000_000
    rest_connect_timeout_seconds: int = 5
    rest_response_timeout_seconds: int = 15
    rest_max_attempts_per_request: int = 3

    connector_connect_timeout_seconds: int = 5
    connector_read_timeout_seconds: int = 20
    connector_max_attempts: int = 4
    connector_backoff_cap_seconds: float = 30.0
    database_read_timeout_seconds: int = 30
    warehouse_max_tables: int = 1_000
    warehouse_page_size: int = 1_000

    def to_safe_dict(self) -> dict[str, int]:
        return {
            "max_uploaded_file_bytes": self.max_uploaded_file_bytes,
            "max_normalized_source_bytes": self.max_normalized_source_bytes,
            "max_workspace_retained_raw_bytes": self.max_workspace_retained_raw_bytes,
            "max_published_snapshot_bytes": self.max_published_snapshot_bytes,
            "max_rows_per_dataset": self.max_rows_per_dataset,
            "max_columns_per_dataset": self.max_columns_per_dataset,
            "max_workbook_sheets": self.max_workbook_sheets,
            "max_chars_per_cell": self.max_chars_per_cell,
            "max_expanded_xlsx_bytes": self.max_expanded_xlsx_bytes,
            "max_expanded_file_bytes": self.max_expanded_file_bytes,
            "ingestion_deadline_seconds": self.ingestion_deadline_seconds,
            "max_preview_rows": self.max_preview_rows,
            "max_export_bytes": self.max_export_bytes,
        }


REPLAN_TASKS = 2

MAX_TOOL_ROUNDS = 6
# A specialist is told to write its answer while this much time is still left, instead of being cut off mid-step.
WIND_DOWN_SECONDS = 45
# Model calls a specialist must leave unspent so the final answer can still be composed.
FINAL_ANSWER_ATTEMPT_RESERVE = 1
# Rate limits and provider outages: wait and try the same model again when no other model can take over.
RETRY_BACKOFF_BASE_SECONDS = 1.0
RETRY_BACKOFF_MAX_SECONDS = 15.0
# A grouping is treated as row-level data when it has at least this many values and fewer than two rows per value.
ROW_LEVEL_MIN_GROUPS = 10
CHARS_PER_TOKEN = 3
TOKEN_HEADROOM_FACTOR = 2
STEP_MAX_OUTPUT_TOKENS = 12_000
STRUCTURED_MAX_OUTPUT_TOKENS = 4_096
MODEL_CALL_TIMEOUT_SECONDS = 120
MIN_CALL_TIMEOUT_SECONDS = 10

LOG_EXCERPT_CHARS = 300
PUBLIC_ERROR_CHARS = 500
ERROR_DETAIL_CHARS = 200
CONNECTOR_ERROR_CHARS = 400
PREVIEW_CHARS = 200
LONG_TEXT_CHARS = 1_000
IDENTIFIER_CHARS = 64
DISPLAY_NAME_CHARS = 120
STORED_TEXT_CHARS = 2_000
VERSION_STRING_CHARS = 80
MAX_CONNECTOR_FIELDS = 60
MAX_CONNECTOR_VALUE_CHARS = 4_000


@dataclass(frozen=True, slots=True)
class RunBudgetLimits:
    mode: str
    active_seconds: int
    total_model_attempts: int
    analytical_calls: int
    accepted_dag_tasks: int
    concurrent_specialists: int
    lifetime_specialist_instances: int
    max_replans: int
    max_tool_invocations: int
    total_tokens: int = 500_000
    finalization_reserve_tokens: int = 24_000
    warning_fraction: float = 0.8

    def __post_init__(self) -> None:
        if self.planner_task_limit < 1:
            raise ValueError(
                f"{self.mode} limits leave no planner capacity after reserving {self.max_replans} replans"
            )

    @property
    def planner_task_limit(self) -> int:
        return (
            min(self.accepted_dag_tasks, self.lifetime_specialist_instances)
            - REPLAN_TASKS * self.max_replans
        )


QUICK_BUDGET_LIMITS = RunBudgetLimits(
    mode="quick",
    active_seconds=120,
    total_model_attempts=6,
    analytical_calls=4,
    accepted_dag_tasks=4,
    concurrent_specialists=1,
    lifetime_specialist_instances=4,
    max_replans=0,
    max_tool_invocations=16,
    total_tokens=200_000,
    finalization_reserve_tokens=24_000,
)

DEEP_BUDGET_LIMITS = RunBudgetLimits(
    mode="deep",
    active_seconds=600,
    total_model_attempts=24,
    analytical_calls=16,
    accepted_dag_tasks=12,
    concurrent_specialists=2,
    lifetime_specialist_instances=8,
    max_replans=2,
    max_tool_invocations=64,
    total_tokens=500_000,
    finalization_reserve_tokens=24_000,
)


def limits_for(mode: RunMode | str) -> RunBudgetLimits:
    return QUICK_BUDGET_LIMITS if RunMode(mode) == RunMode.QUICK else DEEP_BUDGET_LIMITS


@dataclass(frozen=True, slots=True)
class GlobalLimits:
    max_active_root_runs: int = 1
    max_queued_runs: int = 10
    query_timeout_seconds: int = 15
    max_attempts_per_logical_step: int = 3
    max_schema_repairs_per_step: int = 1
    max_proposed_tool_calls_per_response: int = 8
    max_model_tool_result_bytes: int = 64 * 1024
    idempotency_key_retention_hours: int = 24
    max_question_chars: int = 4_000
    max_clarification_chars: int = 2_000
    max_pin_title_chars: int = 255
    workspace_daily_runs: int | None = None
    workspace_daily_cost_usd: Decimal | None = None


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return value


def global_limits_from_env() -> GlobalLimits:
    base = GlobalLimits()
    return replace(
        base,
        max_active_root_runs=_positive_int_env("MAX_ACTIVE_RUNS", base.max_active_root_runs),
        max_queued_runs=_positive_int_env("MAX_QUEUED_RUNS", base.max_queued_runs),
    )


DEFAULT_DATA_LIMITS = DataLimits()
DEFAULT_GLOBAL_LIMITS = global_limits_from_env()
