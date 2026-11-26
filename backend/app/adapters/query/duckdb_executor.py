import hashlib
import json
import threading
import time
from typing import Any

from app.application.ports.query import CompiledQuery, QueryResult
from app.domain.errors import QueryTimeoutError
from app.domain.limits import DEFAULT_DATA_LIMITS

DEFAULT_MEMORY_LIMIT = "1GB"
DEFAULT_TIMEOUT_SECONDS = 15


class DuckDbQueryExecutor:
    def __init__(self, memory_limit: str = DEFAULT_MEMORY_LIMIT, max_rows: int | None = None) -> None:
        self._memory_limit = memory_limit
        self._max_rows = max_rows or DEFAULT_DATA_LIMITS.max_preview_rows

    def execute(
        self,
        compiled: CompiledQuery,
        snapshot_path: str,
        snapshot_id: str,
        semantic_version_id: str,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> QueryResult:
        import duckdb

        conn = duckdb.connect(snapshot_path, read_only=True)
        return self._run(conn, compiled, snapshot_id, semantic_version_id, timeout_seconds)

    def execute_tables(
        self,
        compiled: CompiledQuery,
        tables: dict[str, Any],
        snapshot_id: str,
        semantic_version_id: str,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> QueryResult:
        import duckdb

        conn = duckdb.connect(":memory:")
        for name, table in tables.items():
            conn.register(name, table)
        return self._run(conn, compiled, snapshot_id, semantic_version_id, timeout_seconds)

    def _run(
        self,
        conn: Any,
        compiled: CompiledQuery,
        snapshot_id: str,
        semantic_version_id: str,
        timeout_seconds: int,
    ) -> QueryResult:
        import duckdb

        start = time.monotonic()
        deadline = threading.Timer(timeout_seconds, conn.interrupt)
        deadline.daemon = True
        deadline.start()
        try:
            conn.execute("SET enable_external_access = false")
            conn.execute("SET autoinstall_known_extensions = false")
            conn.execute("SET autoload_known_extensions = false")
            conn.execute(f"SET memory_limit = '{self._memory_limit}'")
            try:
                conn.execute("SET lock_configuration = true")
            except Exception:
                pass

            try:
                cur = conn.execute(compiled.sql, list(compiled.parameters))
                columns = tuple(desc[0] for desc in (cur.description or []))
                row_cap = min(self._max_rows, compiled.row_limit)
                fetched = cur.fetchmany(row_cap + 1)
            except duckdb.InterruptException as exc:
                raise QueryTimeoutError("Query exceeded execution deadline") from exc
            truncated = len(fetched) > row_cap
            rows = tuple(tuple(r) for r in fetched[:row_cap])

            elapsed_ms = int((time.monotonic() - start) * 1000)
            if elapsed_ms > timeout_seconds * 1000:
                raise QueryTimeoutError("Query exceeded execution deadline")

            result_hash = hashlib.sha256(
                json.dumps({"columns": columns, "rows": rows}, default=str).encode("utf-8")
            ).hexdigest()

            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                query_ir_hash=compiled.query_ir_hash,
                result_hash=result_hash,
                execution_ms=elapsed_ms,
                snapshot_id=snapshot_id,
                semantic_version_id=semantic_version_id,
            )
        finally:
            deadline.cancel()
            conn.close()
