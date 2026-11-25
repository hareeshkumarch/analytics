import hashlib
from typing import Any, Callable, Iterable

import pyarrow as pa

from app.adapters.connectors.destination_policy import DestinationPolicy, validate_and_resolve_host
from app.adapters.connectors.tls import ca_file, ca_pem_from
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


def _quote_pg_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


VERIFYING_SSLMODES = frozenset({"verify-ca", "verify-full"})

class PostgresConnector:
    family = ConnectorFamily.POSTGRESQL

    def __init__(self, clock: Clock, policy: DestinationPolicy | None = None) -> None:
        self._clock = clock
        self._policy = policy or DestinationPolicy(
            allowed_ports=(5432,),
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
        import psycopg

        host = str(config.get("host", ""))
        port = int(config.get("port", 5432))
        dbname = str(config.get("database", ""))
        sslmode = str(config.get("sslmode", "prefer"))
        validate_and_resolve_host(host, port, self._policy)

        user = credentials.get("username", "")
        password = credentials.get("password", "")
        if not user or not dbname:
            raise ValidationError("PostgreSQL connector requires host, database, and read-only credentials")

        settings: dict[str, Any] = {
            "host": host,
            "port": port,
            "dbname": dbname,
            "user": user,
            "password": password,
            "sslmode": sslmode,
            "connect_timeout": 5,
        }
        pem = ca_pem_from(credentials)
        if sslmode in VERIFYING_SSLMODES and pem is not None:
            with ca_file(pem) as path:
                settings["sslrootcert"] = path
                return psycopg.connect(**settings)
        if sslmode == "verify-full":
            settings["sslrootcert"] = "system"
        return psycopg.connect(**settings)

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        with self._connect(config, credentials) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                ver = cur.fetchone()[0]
        return ProbeResult(
            ok=True,
            family=self.family,
            message="Connected to PostgreSQL read-only source",
            capabilities=self.capabilities(),
            details={"server_version": str(ver)[:80]},
        )

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        schema_name = str(config.get("schema", "public"))
        with self._connect(config, credentials) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name, column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    ORDER BY table_name, ordinal_position
                    """,
                    (schema_name,),
                )
                rows = cur.fetchall()
        grouped: dict[str, list[ColumnSchema]] = {}
        for tname, cname, dtype, is_null in rows:
            grouped.setdefault(tname, []).append(
                ColumnSchema(name=cname, arrow_type=str(dtype), nullable=(is_null == "YES"))
            )
        return tuple(
            DiscoveredTable(name=f"{schema_name}.{tname}", columns=tuple(cols))
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
            raise ConnectorError("PostgreSQL extraction cancelled before start")
        started_at = self._clock.now()
        schema_name = str(config.get("schema", "public"))

        with self._connect(config, credentials) as conn:
            import psycopg

            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT table_name, column_name
                        FROM information_schema.columns
                        WHERE table_schema = %s
                        ORDER BY table_name, ordinal_position
                        """,
                        (schema_name,),
                    )
                    catalog: dict[str, list[str]] = {}
                    for tname, cname in cur.fetchall():
                        catalog.setdefault(tname, []).append(cname)

                    batches: list[ExtractionBatch] = []
                    table_schemas: list[TableSchema] = []
                    total_rows = 0
                    total_bytes = 0
                    hasher = hashlib.sha256()

                    for full_name in request.selected_tables:
                        if is_cancelled():
                            raise ConnectorError("PostgreSQL extraction cancelled")
                        tname = full_name.split(".", 1)[-1]
                        if tname not in catalog:
                            raise ValidationError(f"Selected table {full_name!r} not found in catalog")
                        allowed_cols = catalog[tname]
                        req_cols = request.selected_columns.get(full_name) or tuple(allowed_cols)
                        for c in req_cols:
                            if c not in allowed_cols:
                                raise ValidationError(f"Selected column {c!r} not in {full_name!r}")

                        col_sql = ", ".join(_quote_pg_ident(c) for c in req_cols)
                        tbl_sql = f"{_quote_pg_ident(schema_name)}.{_quote_pg_ident(tname)}"
                        cur.execute(f"SELECT {col_sql} FROM {tbl_sql}")
                        rows = cur.fetchmany(request.limits.max_rows_per_dataset + 1)
                        if len(rows) > request.limits.max_rows_per_dataset:
                            raise LimitExceededError(f"Table {full_name} exceeds max_rows_per_dataset")

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
                                table_name=full_name,
                                arrow_ipc_bytes=ipc_bytes,
                                row_count=arrow_table.num_rows,
                                normalized_bytes=len(ipc_bytes),
                            )
                        )
                        table_schemas.append(
                            TableSchema(
                                table_name=full_name,
                                columns=col_schemas,
                                row_count=arrow_table.num_rows,
                                normalized_bytes=len(ipc_bytes),
                            )
                        )
                        total_rows += arrow_table.num_rows
                        total_bytes += len(ipc_bytes)
                        hasher.update(full_name.encode("utf-8"))

                conn.commit()
            except Exception as exc:
                if "serialization" in str(exc).lower() or "deadlock" in str(exc).lower():
                    raise TransactionLostError(f"PostgreSQL REPEATABLE READ transaction lost: {exc}") from exc
                raise

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
            source_config_summary={"family": self.family.value, "schema": schema_name},
        )
        return batches, manifest
