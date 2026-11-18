import threading
from collections import OrderedDict
from typing import Any

import pyarrow as pa
import pyarrow.ipc as pa_ipc

from app.application.ports.query import QueryCompiler, QueryResult
from app.application.ports.storage import ArtifactStore
from app.domain.errors import NotFoundError, QueryValidationError
from app.domain.models import ColumnSchema, TableSchema
from app.domain.query_ir import QueryIR

from app.adapters.query.duckdb_executor import DuckDbQueryExecutor


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
    ) -> None:
        self._artifacts = artifacts
        self._compiler = compiler
        self._executor = executor or DuckDbQueryExecutor()
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
    ) -> tuple[QueryResult, str]:
        schemas = table_schemas(snapshot)
        if not schemas:
            raise QueryValidationError("This snapshot has no table schema to query")
        compiled = self._compiler.compile(ir, schemas, max_rows)
        tables = self._load_tables(snapshot)
        result = self._executor.execute_tables(
            compiled,
            tables,
            snapshot_id=str(snapshot["snapshot_id"]),
            semantic_version_id=semantic_version_id,
        )
        return result, compiled.sql
