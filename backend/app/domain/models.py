from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class ConnectorFamily(str, Enum):
    UPLOADED_FILE = "uploaded_file"
    UPLOADED_SQLITE = "uploaded_sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    GOOGLE_SHEETS = "google_sheets"
    REST_JSON = "rest_json"
    AIRTABLE = "airtable"
    BIGQUERY = "bigquery"
    REMOTE_FILE = "remote_file"
    UPLOADED_DUCKDB = "uploaded_duckdb"


class TaskKind(str, Enum):
    DATA_SPECIALIST = "data_specialist"
    RECONCILIATION_SPECIALIST = "reconciliation_specialist"
    EVIDENCE_REVIEWER = "evidence_reviewer"


class ConsistencyLevel(str, Enum):
    SNAPSHOT_ISOLATION = "snapshot_isolation"
    REPEATABLE_READ = "repeatable_read"
    STATIC_EXPORT = "static_export"
    BEST_EFFORT_WINDOW = "best_effort_window"


class RunMode(str, Enum):
    QUICK = "quick"
    DEEP = "deep"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunOutcome(str, Enum):
    ANSWERED = "answered"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    NEEDS_REVIEW = "needs_review"
    INSUFFICIENT_DATA = "insufficient_data"
    CLARIFICATION_EXPIRED = "clarification_expired"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ColumnSchema:
    name: str
    arrow_type: str
    nullable: bool = True
    is_leading_zero_id: bool = False
    currency_code: str | None = None
    decimal_precision: int | None = None
    decimal_scale: int | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class TableSchema:
    table_name: str
    columns: tuple[ColumnSchema, ...]
    row_count: int
    normalized_bytes: int
    primary_key_candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionCapabilities:
    transactional_snapshot: bool
    resumable_cursor: bool
    supports_incremental: bool = False


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    snapshot_id: str
    workspace_id: str
    source_id: str
    version: int
    schema_fingerprint: str
    tables: tuple[TableSchema, ...]
    total_rows: int
    total_normalized_bytes: int
    consistency_level: ConsistencyLevel
    extraction_started_at: datetime
    extraction_completed_at: datetime
    is_complete: bool
    is_sampled: bool
    artifact_id: str
    sha256: str
    source_config_summary: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    column_name: str
    null_count: int
    distinct_count: int
    duplicate_count: int
    min_value: str | None
    max_value: str | None
    is_sampled: bool = False


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    display_name: str
    expression: str
    grain: str
    unit: str
    currency: str | None = None
    null_policy: str = "exclude"
    rounding_scale: int = 2
    required_filters: tuple[str, ...] = ()
    timezone: str = "UTC"
    approved: bool = False


@dataclass(frozen=True, slots=True)
class SemanticVersion:
    semantic_version_id: str
    workspace_id: str
    snapshot_id: str
    version: int
    metrics: tuple[MetricDefinition, ...]
    approved: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TaskNode:
    task_id: str
    kind: str
    objective: str
    dependencies: tuple[str, ...]
    snapshot_id: str
    semantic_version_id: str
    required_inputs: tuple[str, ...]
    result_schema: str
    priority: int = 1
    attempt_limit: int = 3
    acceptance_criteria: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskBudget:
    tokens: int
    tool_calls: int


@dataclass(frozen=True, slots=True)
class TaskRecord:
    run_id: str
    task_id: str
    plan_revision: int
    kind: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...]
    status: str
    attempt: int
    input_fingerprint: str
    version: int
    evidence_ids: tuple[str, ...] = ()
    result: dict[str, Any] | None = None
    error: str | None = None
    budget: TaskBudget | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlanDAG:
    revision: int
    goal: str
    completion_criteria: tuple[str, ...]
    tasks: tuple[TaskNode, ...]

    def validate_dag(self) -> None:
        task_ids = {t.task_id for t in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise ValueError("Duplicate task_id in PlanDAG")
        adj: dict[str, tuple[str, ...]] = {t.task_id: t.dependencies for t in self.tasks}
        for tid, deps in adj.items():
            for dep in deps:
                if dep not in task_ids:
                    raise ValueError(f"Task {tid} depends on missing task {dep}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise ValueError(f"Cycle detected in PlanDAG involving {node}")
            visiting.add(node)
            for dep in adj[node]:
                dfs(dep)
            visiting.remove(node)
            visited.add(node)

        for tid in task_ids:
            dfs(tid)


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    label: str
    statement: str
    value: Decimal | str | None
    unit: str | None
    currency: str | None
    evidence_ids: tuple[str, ...]
    cell_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChartSpec:
    chart_id: str
    chart_type: str
    title: str
    x_field: str
    y_fields: tuple[str, ...]
    evidence_id: str
    rows: tuple[tuple[str | float | None, ...], ...]
    truncated: bool = False
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class CriterionResult:
    criterion: str
    status: str
    evidence_ids: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Answer:
    answer_id: str
    run_id: str
    summary_markdown: str
    claims: tuple[Claim, ...]
    charts: tuple[ChartSpec, ...]
    limitations: tuple[str, ...]
    snapshot_id: str
    semantic_version_id: str
    created_at: datetime
    outcome: str = "success"
    criteria: tuple[CriterionResult, ...] = ()
    route: str = "standard"
    # Questions the user could ask next, suggested after the answer is written.
    follow_ups: tuple[str, ...] = ()
