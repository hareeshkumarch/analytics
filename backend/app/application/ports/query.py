from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.models import TableSchema
from app.domain.query_ir import QueryIR


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    sql: str
    parameters: tuple[Any, ...]
    query_ir_hash: str
    compiler_version: str
    operation: str
    template_name: str
    row_limit: int


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool
    query_ir_hash: str
    result_hash: str
    execution_ms: int
    snapshot_id: str
    semantic_version_id: str
    artifact_id: str | None = None
    units: dict[str, str] = field(default_factory=dict)


class QueryCompiler(Protocol):
    def compile(
        self,
        ir: QueryIR,
        schemas: dict[str, TableSchema],
        max_rows: int,
    ) -> CompiledQuery: ...


class SnapshotQueryPort(Protocol):
    def run(
        self,
        snapshot: dict[str, Any],
        ir: QueryIR,
        semantic_version_id: str,
        max_rows: int,
        *,
        timeout_seconds: int = 15,
        business_timezone: str = "UTC",
    ) -> tuple[QueryResult, str]: ...


class QueryExecutor(Protocol):
    def execute(
        self,
        compiled: CompiledQuery,
        snapshot_path: str,
        snapshot_id: str,
        semantic_version_id: str,
        timeout_seconds: int = 15,
    ) -> QueryResult: ...
