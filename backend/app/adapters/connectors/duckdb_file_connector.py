"""Import base tables from an uploaded DuckDB database file.

The file is copied to a read-only quarantine directory and opened read-only with external
access, extension install/autoload and configuration changes disabled, so views or macros in
an untrusted file cannot read local files or the network. Only base tables are imported;
views are ignored. Column types the query engine cannot use directly (lists, structs, maps,
enums, UUID, INTERVAL, BLOB...) are cast to text.
"""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from app.adapters.parsers.registry import serialize_arrow_ipc_checked
from app.application.ports.clock import Clock
from app.application.ports.connectors import (
    DiscoveredTable,
    ExtractionBatch,
    ExtractionRequest,
    ProbeResult,
)
from app.domain.errors import ConnectorError, LimitExceededError, ValidationError
from app.domain.limits import DEFAULT_DATA_LIMITS
from app.domain.models import (
    ColumnSchema,
    ConnectorFamily,
    ConsistencyLevel,
    ExtractionCapabilities,
    SnapshotManifest,
    TableSchema,
)

_NATIVE_TYPE_PREFIXES = (
    "BOOLEAN",
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
    "VARCHAR",
    "DATE",
    "TIMESTAMP",
)
_SAFE_CONFIG = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "lock_configuration": True,
}
_REMEDIATION = "Save the database with DuckDB 0.10 or newer, close other programs using it, or export the tables as Parquet."


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _is_native(data_type: str) -> bool:
    upper = data_type.upper()
    if any(token in upper for token in ("[", "STRUCT", "MAP(", "UNION", "ENUM")):
        return False
    return upper.startswith(_NATIVE_TYPE_PREFIXES)


class UploadedDuckDbConnector:
    family = ConnectorFamily.UPLOADED_DUCKDB

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            transactional_snapshot=True,
            resumable_cursor=False,
            supports_incremental=False,
        )

    @staticmethod
    def _path(config: dict[str, Any]) -> Path:
        path = Path(str(config.get("file_path", "")))
        if not path.exists() or not path.is_file():
            raise ConnectorError("DuckDB file not found")
        if path.stat().st_size > DEFAULT_DATA_LIMITS.max_uploaded_file_bytes:
            raise LimitExceededError("DuckDB file exceeds the 40,000,000 bytes limit")
        return path

    def _open(self, src: Path) -> tuple[Any, Path]:
        import duckdb

        tmp_dir = Path(tempfile.mkdtemp(prefix="duckdb_quarantine_"))
        quarantined = tmp_dir / "upload.duckdb"
        shutil.copy2(src, quarantined)
        os.chmod(quarantined, 0o444)
        try:
            con = duckdb.connect(str(quarantined), read_only=True, config=dict(_SAFE_CONFIG))
        except duckdb.Error as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise ConnectorError(f"Could not open the DuckDB file: {str(exc)[:200]}", remediation=_REMEDIATION) from exc
        return con, tmp_dir

    @staticmethod
    def _tables(con: Any) -> list[tuple[str, str, str]]:
        rows = con.execute(
            "SELECT schema_name, table_name FROM duckdb_tables() "
            "WHERE NOT internal AND NOT temporary AND database_name = current_database() "
            "ORDER BY schema_name, table_name"
        ).fetchall()
        out: list[tuple[str, str, str]] = []
        for schema_name, table_name in rows:
            display = table_name if schema_name == "main" else f"{schema_name}__{table_name}"
            out.append((display, schema_name, table_name))
        return out

    @staticmethod
    def _view_count(con: Any) -> int:
        try:
            row = con.execute(
                "SELECT count(*) FROM duckdb_views() "
                "WHERE NOT internal AND NOT temporary AND database_name = current_database()"
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:  # noqa: BLE001 - informational only
            return 0

    @staticmethod
    def _columns(con: Any, schema_name: str, table_name: str) -> list[tuple[str, str, bool]]:
        rows = con.execute(
            "SELECT column_name, data_type, is_nullable FROM duckdb_columns() "
            "WHERE database_name = current_database() AND schema_name = ? AND table_name = ? "
            "ORDER BY column_index",
            [schema_name, table_name],
        ).fetchall()
        return [(str(name), str(dtype), bool(nullable)) for name, dtype, nullable in rows]

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        con, tmp_dir = self._open(self._path(config))
        try:
            tables = self._tables(con)
            views = self._view_count(con)
            note = f"; {views} view{'s' if views != 1 else ''} ignored" if views else ""
            return ProbeResult(
                ok=True,
                family=self.family,
                message=f"Opened DuckDB file with {len(tables)} table{'s' if len(tables) != 1 else ''}{note}",
                capabilities=self.capabilities(),
                details={"table_count": str(len(tables)), "view_count": str(views)},
            )
        finally:
            con.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        con, tmp_dir = self._open(self._path(config))
        try:
            discovered: list[DiscoveredTable] = []
            for display, schema_name, table_name in self._tables(con):
                cols = tuple(
                    ColumnSchema(name=name, arrow_type=dtype if _is_native(dtype) else "string", nullable=nullable)
                    for name, dtype, nullable in self._columns(con, schema_name, table_name)
                )
                count = con.execute(f"SELECT count(*) FROM {_quote(schema_name)}.{_quote(table_name)}").fetchone()
                discovered.append(DiscoveredTable(name=display, columns=cols, estimated_rows=int(count[0]) if count else None))
            return tuple(discovered)
        finally:
            con.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]:
        if is_cancelled():
            raise ConnectorError("DuckDB extraction cancelled before start")
        started_at = self._clock.now()
        con, tmp_dir = self._open(self._path(config))
        try:
            all_tables = self._tables(con)
            selected = set(request.selected_tables) if request.selected_tables else {t[0] for t in all_tables}
            if not any(display in selected for display, _, _ in all_tables):
                raise ValidationError(
                    f"None of the selected tables were found: {', '.join(sorted(selected))}",
                    remediation="Re-run discovery and select from the tables it lists.",
                )
            batches: list[ExtractionBatch] = []
            table_schemas: list[TableSchema] = []
            total_rows = 0
            total_bytes = 0
            hasher = hashlib.sha256()
            max_rows = request.limits.max_rows_per_dataset

            for display, schema_name, table_name in all_tables:
                if display not in selected:
                    continue
                if is_cancelled():
                    raise ConnectorError("DuckDB extraction cancelled")
                columns = self._columns(con, schema_name, table_name)
                if not columns:
                    continue
                select_list = ", ".join(
                    _quote(name) if _is_native(dtype) else f"CAST({_quote(name)} AS VARCHAR) AS {_quote(name)}"
                    for name, dtype, _ in columns
                )
                arrow_table = con.execute(
                    f"SELECT {select_list} FROM {_quote(schema_name)}.{_quote(table_name)} LIMIT {max_rows + 1}"
                ).fetch_arrow_table()
                if arrow_table.num_rows > max_rows:
                    raise LimitExceededError(f"Table {display} exceeds max_rows_per_dataset ({max_rows})")
                ipc_bytes = serialize_arrow_ipc_checked(arrow_table, request.limits, total_bytes)
                col_schemas = tuple(
                    ColumnSchema(name=f.name, arrow_type=str(f.type), nullable=f.nullable)
                    for f in arrow_table.schema
                )
                batches.append(
                    ExtractionBatch(
                        table_name=display,
                        arrow_ipc_bytes=ipc_bytes,
                        row_count=arrow_table.num_rows,
                        normalized_bytes=len(ipc_bytes),
                    )
                )
                table_schemas.append(
                    TableSchema(
                        table_name=display,
                        columns=col_schemas,
                        row_count=arrow_table.num_rows,
                        normalized_bytes=len(ipc_bytes),
                    )
                )
                total_rows += arrow_table.num_rows
                total_bytes += len(ipc_bytes)
                hasher.update(display.encode("utf-8"))
                for col in col_schemas:
                    hasher.update(f"{col.name}:{col.arrow_type}".encode("utf-8"))

            manifest = SnapshotManifest(
                snapshot_id=f"snap_{request.job_id}",
                workspace_id=request.workspace_id,
                source_id=request.source_id,
                version=request.version,
                schema_fingerprint=hasher.hexdigest(),
                tables=tuple(table_schemas),
                total_rows=total_rows,
                total_normalized_bytes=total_bytes,
                consistency_level=ConsistencyLevel.STATIC_EXPORT,
                extraction_started_at=started_at,
                extraction_completed_at=self._clock.now(),
                is_complete=True,
                is_sampled=False,
                artifact_id=f"art_{request.job_id}",
                sha256=hasher.hexdigest(),
                source_config_summary={"family": self.family.value},
            )
            return batches, manifest
        except Exception as exc:
            import duckdb

            if isinstance(exc, duckdb.Error):
                raise ConnectorError(f"DuckDB read failed: {str(exc)[:200]}", remediation=_REMEDIATION) from exc
            raise
        finally:
            con.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)
