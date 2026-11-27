import hashlib
import ssl
from typing import Any, Callable, Iterable

import pyarrow as pa

from app.domain.limits import DEFAULT_DATA_LIMITS
from app.adapters.connectors.destination_policy import DestinationPolicy, validate_and_resolve_host
from app.adapters.connectors.tls import ca_pem_from, tls_mode_for
from app.adapters.parsers.registry import serialize_arrow_ipc_checked
from app.application.ports.clock import Clock
from app.application.ports.connectors import (
    DiscoveredTable,
    ExtractionBatch,
    ExtractionRequest,
    ProbeResult,
)
from app.domain.errors import ConnectorError, LimitExceededError, TransactionLostError, ValidationError
from app.domain.models import (
    ColumnSchema,
    ConnectorFamily,
    ConsistencyLevel,
    ExtractionCapabilities,
    SnapshotManifest,
    TableSchema,
)


def _quote_mysql_ident(ident: str) -> str:
    return "`" + ident.replace("`", "``") + "`"


MYSQL_TLS_MODES = ("disable", "require", "verify-full")


def _tls_context(mode: str, ca_pem: str | None = None) -> ssl.SSLContext | None:
    """TLS for managed MySQL/MariaDB (Azure, Cloud SQL, RDS); disable is only accepted for local hosts."""
    if mode in ("", "disable"):
        return None
    if mode not in MYSQL_TLS_MODES:
        raise ValidationError(f"Unsupported tls_mode {mode!r}", remediation=f"Use one of: {', '.join(MYSQL_TLS_MODES)}.")
    ctx = ssl.create_default_context(cadata=ca_pem) if ca_pem and mode == "verify-full" else ssl.create_default_context()
    if mode == "require":
        # Encrypt without verifying the certificate (RDS uses its own CA, not in the system store).
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class MySqlConnector:
    family = ConnectorFamily.MYSQL

    def __init__(self, clock: Clock, policy: DestinationPolicy | None = None) -> None:
        self._clock = clock
        self._policy = policy or DestinationPolicy(
            allowed_ports=(3306,),
            allow_private_local_for_tests=True,
            require_https=False,
        )

    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            transactional_snapshot=True,
            resumable_cursor=False,
            supports_incremental=False,
        )

    def _connect(self, config: dict[str, Any], credentials: dict[str, str]) -> Any:
        import pymysql

        host = str(config.get("host", ""))
        port = int(config.get("port", 3306))
        database = str(config.get("database", ""))
        host_ip = validate_and_resolve_host(host, port, self._policy)
        tls_mode = tls_mode_for(
            host_ip, str(config.get("tls_mode", "") or ""), plaintext_modes=("disable",), local_default="disable"
        )

        user = credentials.get("username", "")
        password = credentials.get("password", "")
        if not user or not database:
            raise ValidationError("MySQL connector requires host, database, and read-only credentials")

        try:
            tls = _tls_context(tls_mode, ca_pem_from(credentials))
        except ssl.SSLError as exc:
            raise ValidationError("The CA certificate could not be read.", remediation="Paste the full PEM certificate.") from exc
        return pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=DEFAULT_DATA_LIMITS.connector_connect_timeout_seconds,
            read_timeout=DEFAULT_DATA_LIMITS.database_read_timeout_seconds,
            autocommit=False,
            **({"ssl": tls} if tls is not None else {}),
        )

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        conn = self._connect(config, credentials)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                ver = cur.fetchone()[0]
            return ProbeResult(
                ok=True,
                family=self.family,
                message="Connected to MySQL read-only source",
                capabilities=self.capabilities(),
                details={"server_version": str(ver)[:80]},
            )
        finally:
            conn.close()

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        database = str(config.get("database", ""))
        conn = self._connect(config, credentials)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.TABLE_NAME, t.ENGINE, c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE
                    FROM information_schema.TABLES t
                    JOIN information_schema.COLUMNS c
                      ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
                    WHERE t.TABLE_SCHEMA = %s AND t.TABLE_TYPE = 'BASE TABLE'
                    ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION
                    """,
                    (database,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        grouped: dict[str, list[ColumnSchema]] = {}
        engines: dict[str, str] = {}
        for tname, engine, cname, dtype, is_null in rows:
            engines[tname] = str(engine or "")
            grouped.setdefault(tname, []).append(
                ColumnSchema(name=cname, arrow_type=str(dtype), nullable=(is_null == "YES"))
            )
        return tuple(
            DiscoveredTable(
                name=tname,
                columns=tuple(cols),
                metadata={"engine": engines.get(tname, "")},
            )
            for tname, cols in grouped.items()
        )

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]:
        if is_cancelled():
            raise ConnectorError("MySQL extraction cancelled before start")
        started_at = self._clock.now()
        database = str(config.get("database", ""))
        conn = self._connect(config, credentials)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT TABLE_NAME, ENGINE
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
                    """,
                    (database,),
                )
                table_engines = {r[0]: str(r[1] or "") for r in cur.fetchall()}
                for tname in request.selected_tables:
                    if tname not in table_engines:
                        raise ValidationError(f"MySQL table {tname!r} not found")
                    if table_engines[tname].upper() != "INNODB":
                        raise ValidationError(
                            f"Table {tname!r} uses engine {table_engines[tname]!r}; only InnoDB supports consistent snapshots."
                        )

                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")

                batches: list[ExtractionBatch] = []
                table_schemas: list[TableSchema] = []
                total_rows = 0
                total_bytes = 0
                hasher = hashlib.sha256()

                for tname in request.selected_tables:
                    if is_cancelled():
                        raise ConnectorError("MySQL extraction cancelled")
                    cur.execute(
                        """
                        SELECT COLUMN_NAME
                        FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                        ORDER BY ORDINAL_POSITION
                        """,
                        (database, tname),
                    )
                    allowed_cols = [r[0] for r in cur.fetchall()]
                    req_cols = request.selected_columns.get(tname) or tuple(allowed_cols)
                    for c in req_cols:
                        if c not in allowed_cols:
                            raise ValidationError(f"Column {c!r} not in {tname!r}")

                    col_sql = ", ".join(_quote_mysql_ident(c) for c in req_cols)
                    cur.execute(f"SELECT {col_sql} FROM {_quote_mysql_ident(tname)}")
                    rows = cur.fetchmany(request.limits.max_rows_per_dataset + 1)
                    if len(rows) > request.limits.max_rows_per_dataset:
                        raise LimitExceededError(f"Table {tname} exceeds max_rows_per_dataset")

                    arrow_table = pa.table(
                        {c: pa.array([r[i] for r in rows]) for i, c in enumerate(req_cols)}
                    )
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

                conn.commit()
        except Exception as exc:
            if "lost connection" in str(exc).lower() or "deadlock" in str(exc).lower():
                raise TransactionLostError(f"MySQL consistent snapshot transaction lost: {exc}") from exc
            raise
        finally:
            conn.close()

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
            consistency_level=ConsistencyLevel.REPEATABLE_READ,
            extraction_started_at=started_at,
            extraction_completed_at=completed_at,
            is_complete=True,
            is_sampled=False,
            artifact_id=f"art_{request.job_id}",
            sha256=hasher.hexdigest(),
            source_config_summary={"family": self.family.value, "database": database},
        )
        return batches, manifest
