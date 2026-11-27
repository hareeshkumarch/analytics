import hashlib
import json
from typing import Any, Callable, Iterable

import pyarrow as pa

from app.domain.limits import DEFAULT_DATA_LIMITS
from app.adapters.parsers.registry import serialize_arrow_ipc_checked
from app.application.ports.clock import Clock
from app.application.ports.connectors import (
    DiscoveredTable,
    ExtractionBatch,
    ExtractionRequest,
    ProbeResult,
)
from app.domain.errors import ConnectorError, LimitExceededError, ValidationError
from app.domain.models import (
    ColumnSchema,
    ConnectorFamily,
    ConsistencyLevel,
    ExtractionCapabilities,
    SnapshotManifest,
    TableSchema,
)


def _arrow_type(field: Any) -> pa.DataType:
    kind = str(field.field_type).upper()
    if kind in ("RECORD", "STRUCT"):
        arrow_type = pa.struct([pa.field(child.name, _arrow_type(child)) for child in field.fields])
    else:
        arrow_type = {
            "STRING": pa.string(),
            "GEOGRAPHY": pa.string(),
            "JSON": pa.string(),
            "BYTES": pa.binary(),
            "INTEGER": pa.int64(),
            "INT64": pa.int64(),
            "FLOAT": pa.float64(),
            "FLOAT64": pa.float64(),
            "NUMERIC": pa.decimal128(38, 9),
            "DECIMAL": pa.decimal128(38, 9),
            "BIGNUMERIC": pa.decimal256(76, 38),
            "BIGDECIMAL": pa.decimal256(76, 38),
            "BOOLEAN": pa.bool_(),
            "BOOL": pa.bool_(),
            "TIMESTAMP": pa.timestamp("us", tz="UTC"),
            "DATETIME": pa.timestamp("us"),
            "DATE": pa.date32(),
            "TIME": pa.time64("us"),
        }.get(kind, pa.string())
    return pa.list_(arrow_type) if str(field.mode).upper() == "REPEATED" else arrow_type


def _row_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _row_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_row_value(item) for item in value]
    if not isinstance(value, (str, bytes, bytearray)) and hasattr(value, "items"):
        return {str(key): _row_value(item) for key, item in value.items()}
    return value


class BigQueryConnector:
    """Read-only BigQuery table importer authenticated with a service account."""

    family = ConnectorFamily.BIGQUERY

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            transactional_snapshot=False,
            resumable_cursor=False,
            supports_incremental=False,
        )

    def _client(self, config: dict[str, Any], credentials: dict[str, str]) -> Any:
        project_id = str(config.get("project_id", "")).strip()
        service_account_json = str(credentials.get("service_account_json", "")).strip()
        if not project_id:
            raise ValidationError("BigQuery project ID is required.")
        if not service_account_json:
            raise ValidationError(
                "BigQuery service account JSON is required.",
                remediation="Use a service account with BigQuery Data Viewer access to the datasets being imported.",
            )
        try:
            from google.cloud import bigquery
            from google.oauth2.service_account import Credentials

            info = json.loads(service_account_json)
            auth = Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/bigquery.readonly"]
            )
            return bigquery.Client(project=project_id, credentials=auth, location=config.get("location") or None)
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("BigQuery service account JSON or client setup is invalid.") from exc

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        client = self._client(config, credentials)
        project_id = str(config["project_id"]).strip()
        dataset_id = str(config.get("dataset_id", "")).strip()
        if not dataset_id:
            raise ValidationError("BigQuery dataset ID is required.")
        try:
            dataset = client.get_dataset(f"{project_id}.{dataset_id}")
        except Exception as exc:
            raise ConnectorError(
                "BigQuery rejected access to the selected dataset.",
                remediation="Check that BigQuery API is enabled and the service account has BigQuery Data Viewer on this dataset.",
            ) from exc
        return ProbeResult(
            ok=True,
            family=self.family,
            message=f"Connected to BigQuery dataset {project_id}.{dataset_id}",
            capabilities=self.capabilities(),
            details={"project_id": project_id, "dataset_id": dataset_id, "location": str(dataset.location or "")},
        )

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        client = self._client(config, credentials)
        project_id = str(config["project_id"]).strip()
        try:
            dataset_id = str(config.get("dataset_id", "")).strip()
            if not dataset_id:
                raise ValidationError("BigQuery dataset ID is required.")
            dataset = client.get_dataset(f"{project_id}.{dataset_id}")
            max_tables = DEFAULT_DATA_LIMITS.warehouse_max_tables
            tables = list(client.list_tables(dataset, max_results=max_tables + 1))
            if len(tables) > max_tables:
                raise LimitExceededError(
                    f"BigQuery dataset {dataset_id!r} has more than {max_tables:,} tables."
                )
            discovered: list[DiscoveredTable] = []
            for table in tables:
                full_name = f"{dataset.dataset_id}.{table.table_id}"
                metadata = client.get_table(table)
                columns = tuple(
                    ColumnSchema(name=field.name, arrow_type=field.field_type, nullable=True)
                    for field in metadata.schema
                )
                discovered.append(
                    DiscoveredTable(
                        name=full_name,
                        columns=columns,
                        estimated_rows=metadata.num_rows,
                    )
                )
            return tuple(discovered)
        except (LimitExceededError, ValidationError):
            raise
        except Exception as exc:
            raise ConnectorError(
                "Could not list BigQuery tables.",
                remediation="Grant the service account BigQuery Data Viewer on the selected dataset.",
            ) from exc

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]:
        if is_cancelled():
            raise ConnectorError("BigQuery extraction cancelled before start")
        if not request.selected_tables:
            raise ValidationError("Select at least one BigQuery table to import.")
        started_at = self._clock.now()
        client = self._client(config, credentials)
        project_id = str(config["project_id"]).strip()
        selected_dataset = str(config.get("dataset_id", "")).strip()
        if not selected_dataset:
            raise ValidationError("BigQuery dataset ID is required.")
        batches: list[ExtractionBatch] = []
        table_schemas: list[TableSchema] = []
        total_rows = 0
        total_bytes = 0
        hasher = hashlib.sha256()

        for full_name in request.selected_tables:
            if is_cancelled():
                raise ConnectorError("BigQuery extraction cancelled")
            parts = full_name.split(".")
            if len(parts) != 2 or not all(parts):
                raise ValidationError(f"Invalid BigQuery table selection {full_name!r}.")
            dataset_id, table_id = parts
            if dataset_id != selected_dataset:
                raise ValidationError(
                    f"Selected table {full_name!r} is outside the configured dataset."
                )
            try:
                table = client.get_table(f"{project_id}.{dataset_id}.{table_id}")
                schema_by_name = {field.name: field for field in table.schema}
                requested_columns = request.selected_columns.get(full_name) or tuple(schema_by_name)
                unknown = set(requested_columns) - set(schema_by_name)
                if unknown:
                    raise ValidationError(
                        f"Selected BigQuery column {sorted(unknown)[0]!r} was not found in {full_name!r}."
                    )
                selected_schema = [schema_by_name[name] for name in requested_columns]
                if len(selected_schema) > request.limits.max_columns_per_dataset:
                    raise LimitExceededError(
                        f"BigQuery table {full_name!r} exceeds the column limit."
                    )
                rows = client.list_rows(
                    table,
                    selected_fields=selected_schema,
                    page_size=DEFAULT_DATA_LIMITS.warehouse_page_size,
                    max_results=request.limits.max_rows_per_dataset + 1,
                )
                arrow_schema = pa.schema(
                    [
                        pa.field(field.name, _arrow_type(field), nullable=str(field.mode).upper() != "REQUIRED")
                        for field in selected_schema
                    ]
                )
                page_tables: list[pa.Table] = []
                row_count = 0
                table_bytes = 0
                for page in rows.pages:
                    if is_cancelled():
                        raise ConnectorError("BigQuery extraction cancelled")
                    page_rows = [
                        {str(key): _row_value(value) for key, value in row.items()}
                        for row in page
                    ]
                    row_count += len(page_rows)
                    if row_count > request.limits.max_rows_per_dataset:
                        raise LimitExceededError(f"BigQuery table {full_name!r} exceeds the row limit.")
                    page_table = pa.Table.from_pylist(page_rows, schema=arrow_schema)
                    table_bytes += page_table.nbytes
                    if table_bytes + total_bytes > request.limits.max_normalized_source_bytes:
                        raise LimitExceededError(
                            f"BigQuery table {full_name!r} exceeds the normalized snapshot size limit."
                        )
                    page_tables.append(page_table)
                arrow_table = (
                    pa.concat_tables(page_tables)
                    if page_tables
                    else pa.Table.from_batches([], schema=arrow_schema)
                )
            except (ConnectorError, LimitExceededError, ValidationError):
                raise
            except Exception as exc:
                raise ConnectorError(
                    f"Could not read BigQuery table {full_name!r}.",
                    remediation="Check dataset permissions, table location, and that the table still exists.",
                ) from exc
            ipc_bytes = serialize_arrow_ipc_checked(arrow_table, request.limits, total_bytes)
            columns = tuple(
                ColumnSchema(name=field.name, arrow_type=str(field.type), nullable=field.nullable)
                for field in arrow_table.schema
            )
            batches.append(
                ExtractionBatch(full_name, ipc_bytes, arrow_table.num_rows, len(ipc_bytes))
            )
            table_schemas.append(
                TableSchema(full_name, columns, arrow_table.num_rows, len(ipc_bytes))
            )
            total_rows += arrow_table.num_rows
            total_bytes += len(ipc_bytes)
            hasher.update(full_name.encode("utf-8"))
            hasher.update(ipc_bytes)

        completed_at = self._clock.now()
        fingerprint = hasher.hexdigest()
        manifest = SnapshotManifest(
            snapshot_id=f"snap_{request.job_id}",
            workspace_id=request.workspace_id,
            source_id=request.source_id,
            version=request.version,
            schema_fingerprint=fingerprint,
            tables=tuple(table_schemas),
            total_rows=total_rows,
            total_normalized_bytes=total_bytes,
            consistency_level=ConsistencyLevel.BEST_EFFORT_WINDOW,
            extraction_started_at=started_at,
            extraction_completed_at=completed_at,
            is_complete=True,
            is_sampled=False,
            artifact_id=f"art_{request.job_id}",
            sha256=fingerprint,
            source_config_summary={
                "family": self.family.value,
                "project_id": project_id,
                "dataset_id": selected_dataset,
            },
        )
        return batches, manifest
