import threading
from collections import OrderedDict
from dataclasses import replace
from typing import Any

import pyarrow as pa
import pyarrow.ipc as pa_ipc

from app.application.ports.query import QueryCompiler, QueryResult
from app.application.ports.storage import ArtifactStore
from app.domain.errors import NotFoundError, QueryValidationError
from app.domain.models import ColumnSchema, TableSchema
from app.domain.limits import DEFAULT_GLOBAL_LIMITS
from app.domain.timezone import DEFAULT_BUSINESS_TIMEZONE
from app.domain.query_ir import QueryIR

from app.adapters.query.duckdb_executor import EXECUTOR_VERSION, DuckDbQueryExecutor
from app.adapters.query.result_cache import SingleFlightCache

SHARED_RESULT_CACHE: SingleFlightCache[QueryResult] = SingleFlightCache()


def table_schemas(snapshot: dict[str, Any]) -> dict[str, TableSchema]:
    schemas: dict[str, TableSchema] = {}
    for detail in snapshot.get("table_details") or []:
        columns = tuple(
            ColumnSchema(
                name=str(c["name"]),
                arrow_type=str(c.get("type", "")),
                nullable=bool(c.get("nullable", True)),
                is_leading_zero_id=bool(c.get("leading_zero_id", False)),
                currency_code=c.get("currency"),
            )
            for c in detail.get("columns", [])
        )
        schemas[str(detail["name"])] = TableSchema(
            table_name=str(detail["name"]),
            columns=columns,
            row_count=int(detail.get("row_count", 0)),
            normalized_bytes=0,
        )
    return schemas


class SnapshotQueryEngine:
    def __init__(
        self,
        artifacts: ArtifactStore,
        compiler: QueryCompiler,
        executor: DuckDbQueryExecutor | None = None,
        max_cached_snapshots: int = 4,
        result_cache: SingleFlightCache[QueryResult] | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._compiler = compiler
        self._executor = executor or DuckDbQueryExecutor()
        self._results = SHARED_RESULT_CACHE if result_cache is None else result_cache
        self._cache: OrderedDict[str, dict[str, pa.Table]] = OrderedDict()
        self._max_cached = max_cached_snapshots
        self._lock = threading.Lock()

    def _load_tables(
        self, snapshot: dict[str, Any], requested_tables: tuple[str, ...] | None = None
    ) -> dict[str, pa.Table]:
        snapshot_id = str(snapshot["snapshot_id"])
        table_artifacts: dict[str, str] = snapshot.get("table_artifacts") or {}
        if not table_artifacts:
            raise NotFoundError(
                "This snapshot has no queryable tables",
                remediation="Re-extract the dataset so its tables are published for querying.",
            )
        names = tuple(table_artifacts) if requested_tables is None else requested_tables
        unknown = [name for name in names if name not in table_artifacts]
        if unknown:
            raise QueryValidationError(f"Unknown table(s): {unknown!r}")

        with self._lock:
            tables = self._cache.setdefault(snapshot_id, {})
            missing = [name for name in names if name not in tables]
            self._cache.move_to_end(snapshot_id)

        loaded: dict[str, pa.Table] = {}
        for name in missing:
            raw = self._artifacts.read_bytes(table_artifacts[name])
            loaded[name] = pa_ipc.open_stream(pa.BufferReader(raw)).read_all()

        with self._lock:
            tables = self._cache.setdefault(snapshot_id, {})
            tables.update(loaded)
            while len(self._cache) > self._max_cached:
                self._cache.popitem(last=False)
            return {name: tables[name] for name in names}

    def load_table(self, snapshot: dict[str, Any], table_name: str) -> pa.Table:
        schemas = table_schemas(snapshot)
        if table_name not in schemas:
            raise QueryValidationError(f"Unknown table: {table_name!r}")
        return self._load_tables(snapshot, (table_name,))[table_name]

    def preview(self, snapshot: dict[str, Any], table_name: str, limit: int) -> dict[str, Any]:
        schemas = table_schemas(snapshot)
        if table_name not in schemas:
            raise QueryValidationError(f"Unknown table: {table_name!r}")
        table_artifact = (snapshot.get("table_artifacts") or {}).get(table_name)
        if table_artifact is None:
            raise NotFoundError(
                "This snapshot has no queryable tables",
                remediation="Re-extract the dataset so its tables are published for querying.",
            )

        snapshot_id = str(snapshot["snapshot_id"])
        with self._lock:
            cached = self._cache.get(snapshot_id, {}).get(table_name)
            if snapshot_id in self._cache:
                self._cache.move_to_end(snapshot_id)

        if cached is not None:
            preview = cached.slice(0, limit)
            return {"columns": cached.column_names, "rows": preview.to_pylist(), "row_count": preview.num_rows}

        rows: list[dict[str, Any]] = []
        with self._artifacts.open_read_stream(table_artifact) as artifact_stream:
            reader = pa_ipc.open_stream(artifact_stream)
            columns = reader.schema.names
            for batch in reader:
                remaining = limit - len(rows)
                if remaining <= 0:
                    break
                rows.extend(batch.slice(0, remaining).to_pylist())
                if len(rows) >= limit:
                    break
        return {"columns": columns, "rows": rows, "row_count": len(rows)}

    def run(
        self,
        snapshot: dict[str, Any],
        ir: QueryIR,
        semantic_version_id: str,
        max_rows: int,
        *,
        timeout_seconds: int = DEFAULT_GLOBAL_LIMITS.query_timeout_seconds,
        business_timezone: str = DEFAULT_BUSINESS_TIMEZONE,
    ) -> tuple[QueryResult, str]:
        schemas = table_schemas(snapshot)
        if not schemas:
            raise QueryValidationError("This snapshot has no table schema to query")
        compiled = self._compiler.compile(ir, schemas, max_rows)
        snapshot_id = str(snapshot["snapshot_id"])
        key = (
            snapshot_id,
            semantic_version_id,
            business_timezone,
            compiled.compiler_version,
            EXECUTOR_VERSION,
            compiled.sql,
            tuple(repr(p) for p in compiled.parameters),
        )

        def execute() -> QueryResult:
            return self._executor.execute_tables(
                compiled,
                self._load_tables(snapshot),
                snapshot_id=snapshot_id,
                semantic_version_id=semantic_version_id,
                timeout_seconds=timeout_seconds,
                business_timezone=business_timezone,
            )

        result, hit = self._results.get_or_compute(key, execute)
        if hit:
            result = replace(result, query_ir_hash=compiled.query_ir_hash, execution_ms=0, cache_hit=True)
        return result, compiled.sql

    def forget_snapshot(self, snapshot_id: str) -> int:
        with self._lock:
            self._cache.pop(snapshot_id, None)
        return self._results.invalidate(lambda key: isinstance(key, tuple) and key[0] == snapshot_id)
