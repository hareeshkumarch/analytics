import hashlib
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

import pyarrow as pa

from app.adapters.parsers.registry import serialize_arrow_ipc_checked
from app.application.ports.clock import Clock
from app.application.ports.connectors import (
    DiscoveredTable,
    ExtractionBatch,
    ExtractionRequest,
    ProbeResult,
)
from app.domain.errors import ConnectorError, LimitExceededError, SecurityPolicyError, ValidationError
from app.domain.limits import DEFAULT_DATA_LIMITS
from app.domain.models import (
    ColumnSchema,
    ConnectorFamily,
    ConsistencyLevel,
    ExtractionCapabilities,
    SnapshotManifest,
    TableSchema,
)


def _quote_sqlite_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


class UploadedSqliteConnector:
    family = ConnectorFamily.UPLOADED_SQLITE

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            transactional_snapshot=True,
            resumable_cursor=False,
            supports_incremental=False,
        )

    def _validate_consistent_export(self, db_path: Path) -> None:
        if not db_path.exists() or not db_path.is_file():
            raise ConnectorError(f"SQLite file not found: {db_path.name}")
        if db_path.stat().st_size > DEFAULT_DATA_LIMITS.max_uploaded_file_bytes:
            raise LimitExceededError("SQLite file exceeds 40,000,000 bytes limit")
        wal_path = Path(f"{db_path}-wal")
        journal_path = Path(f"{db_path}-journal")
        if wal_path.exists() and wal_path.stat().st_size > 0:
            raise ValidationError(
                "Live WAL file detected alongside SQLite database; export a consistent standalone backup (VACUUM INTO or sqlite3 .backup).",
                remediation="Run sqlite3 .backup or VACUUM INTO before uploading.",
            )
        if journal_path.exists() and journal_path.stat().st_size > 0:
            raise ValidationError(
                "Active rollback journal detected alongside SQLite database; upload a consistent standalone export."
            )

    def _open_quarantined_readonly(self, src_path: Path) -> tuple[sqlite3.Connection, Path]:
        tmp_dir = Path(tempfile.mkdtemp(prefix="sqlite_quarantine_"))
        quarantined = tmp_dir / "snapshot.sqlite"
        shutil.copy2(src_path, quarantined)
        os.chmod(quarantined, 0o444)

        conn = sqlite3.connect(f"file:{quarantined}?mode=ro&immutable=1", uri=True)
        try:
            conn.enable_load_extension(False)
        except AttributeError:
            pass
        conn.execute("PRAGMA trusted_schema = OFF")

        def authorizer(action: int, arg1: str | None, arg2: str | None, dbname: str | None, source: str | None) -> int:
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and arg2 not in ("load_extension",):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in ("table_info", "trusted_schema"):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer)
        return conn, tmp_dir

    def _discover_ordinary_tables(self, conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            "SELECT name, type, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        ordinary: list[str] = []
        for name, obj_type, ddl in rows:
            if obj_type in ("view", "trigger"):
                raise SecurityPolicyError(
                    f"SQLite {obj_type} {name!r} is not permitted; only ordinary tables are supported."
                )
            if obj_type == "table":
                if ddl and "VIRTUAL TABLE" in ddl.upper():
                    raise SecurityPolicyError(f"SQLite virtual table {name!r} is rejected.")
                ordinary.append(name)
        return ordinary

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        db_path = Path(str(config.get("file_path", "")))
        self._validate_consistent_export(db_path)
        conn, tmp_dir = self._open_quarantined_readonly(db_path)
        try:
            tables = self._discover_ordinary_tables(conn)
            return ProbeResult(
                ok=True,
                family=self.family,
                message=f"Validated consistent SQLite export with {len(tables)} ordinary tables",
                capabilities=self.capabilities(),
                details={"table_count": str(len(tables))},
            )
        finally:
            conn.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        db_path = Path(str(config.get("file_path", "")))
        self._validate_consistent_export(db_path)
        conn, tmp_dir = self._open_quarantined_readonly(db_path)
        try:
            table_names = self._discover_ordinary_tables(conn)
            discovered: list[DiscoveredTable] = []
            for tname in table_names:
                info_rows = conn.execute(f"PRAGMA table_info({_quote_sqlite_ident(tname)})").fetchall()
                cols = tuple(
                    ColumnSchema(name=r[1], arrow_type=str(r[2] or "TEXT"), nullable=not bool(r[3]))
                    for r in info_rows
                )
                cnt = conn.execute(f"SELECT COUNT(*) FROM {_quote_sqlite_ident(tname)}").fetchone()[0]
                discovered.append(DiscoveredTable(name=tname, columns=cols, estimated_rows=int(cnt)))
            return tuple(discovered)
        finally:
            conn.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]:
        if is_cancelled():
            raise ConnectorError("SQLite extraction cancelled before start")
        started_at = self._clock.now()
        db_path = Path(str(config.get("file_path", "")))
        self._validate_consistent_export(db_path)
        conn, tmp_dir = self._open_quarantined_readonly(db_path)
        try:
            ordinary_tables = self._discover_ordinary_tables(conn)
            selected = set(request.selected_tables) if request.selected_tables else set(ordinary_tables)
            batches: list[ExtractionBatch] = []
            table_schemas: list[TableSchema] = []
            total_rows = 0
            total_bytes = 0
            hasher = hashlib.sha256()

            for tname in ordinary_tables:
                if tname not in selected:
                    continue
                if is_cancelled():
                    raise ConnectorError("SQLite extraction cancelled")
                quoted = _quote_sqlite_ident(tname)
                cur = conn.execute(f"SELECT * FROM {quoted}")
                col_names = [d[0] for d in cur.description]
                rows = cur.fetchmany(request.limits.max_rows_per_dataset + 1)
                if len(rows) > request.limits.max_rows_per_dataset:
                    raise LimitExceededError(f"Table {tname} exceeds max_rows_per_dataset")
                col_dict = {
                    col: pa.array([row[i] for row in rows])
                    for i, col in enumerate(col_names)
                }
                arrow_table = pa.table(col_dict)
                ipc_bytes = serialize_arrow_ipc_checked(arrow_table, request.limits, total_bytes)
                col_schemas = tuple(
                    ColumnSchema(name=f.name, arrow_type=str(f.type), nullable=f.nullable)
                    for f in arrow_table.schema
                )
                batches.append(
                    ExtractionBatch(
                        table_name=tname,
                        arrow_ipc_bytes=ipc_bytes,
                        row_count=arrow_table.num_rows,
                        normalized_bytes=len(ipc_bytes),
                    )
                )
                table_schemas.append(
                    TableSchema(
                        table_name=tname,
                        columns=col_schemas,
                        row_count=arrow_table.num_rows,
                        normalized_bytes=len(ipc_bytes),
                    )
                )
                total_rows += arrow_table.num_rows
                total_bytes += len(ipc_bytes)
                hasher.update(tname.encode("utf-8"))

            completed_at = self._clock.now()
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
                extraction_completed_at=completed_at,
                is_complete=True,
                is_sampled=False,
                artifact_id=f"art_{request.job_id}",
                sha256=hasher.hexdigest(),
                source_config_summary={"family": self.family.value},
            )
            return batches, manifest
        finally:
            conn.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)
