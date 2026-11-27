import hashlib
import json
import random
import time
from typing import Any, Callable, Iterable
from urllib.parse import quote

import httpx
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


class AirtableConnector:
    """Read-only Airtable importer using a base-scoped personal access token."""

    family = ConnectorFamily.AIRTABLE
    _API_ROOT = "https://api.airtable.com/v0"

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            transactional_snapshot=False,
            resumable_cursor=False,
            supports_incremental=False,
        )

    def _base_and_token(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[str, str]:
        base_id = str(config.get("base_id", "")).strip()
        token = str(credentials.get("personal_access_token", "")).strip()
        if not base_id or not base_id.startswith("app"):
            raise ValidationError("Airtable requires a base ID beginning with 'app'.")
        if not token:
            raise ValidationError(
                "Airtable personal access token is required.",
                remediation="Create a PAT with data.records:read and schema.bases:read, scoped to this base.",
            )
        return base_id, token

    def _get(self, url: str, token: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        limits = DEFAULT_DATA_LIMITS
        connect = float(limits.connector_connect_timeout_seconds)
        timeout = httpx.Timeout(connect=connect, read=float(limits.connector_read_timeout_seconds), write=connect, pool=connect)
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            for attempt in range(limits.connector_max_attempts):
                try:
                    response = client.get(url, params=params, headers=headers)
                except httpx.HTTPError as exc:
                    raise ConnectorError("Could not reach Airtable API.", retryable=True) from exc
                if response.status_code == 429 or response.status_code in (500, 502, 503, 504):
                    if attempt == limits.connector_max_attempts - 1:
                        raise ConnectorError(
                            f"Airtable API is temporarily unavailable (HTTP {response.status_code}).",
                            retryable=True,
                        )
                    retry_after = response.headers.get("Retry-After", "")
                    delay = float(retry_after) if retry_after.isdigit() else 2**attempt
                    time.sleep(min(limits.connector_backoff_cap_seconds, delay) + random.uniform(0.0, 0.2))
                    continue
                if response.status_code >= 400:
                    remediation = None
                    if response.status_code in (401, 403):
                        remediation = "Check the PAT, its read scopes, and that it is limited to this base."
                    raise ConnectorError(
                        f"Airtable API rejected the request (HTTP {response.status_code}).",
                        remediation=remediation,
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ConnectorError("Airtable returned invalid JSON.") from exc
                if not isinstance(payload, dict):
                    raise ConnectorError("Airtable returned an unexpected response.")
                return payload
        raise ConnectorError("Airtable request failed after retries.", retryable=True)

    def _schema(self, base_id: str, token: str) -> list[dict[str, Any]]:
        url = f"{self._API_ROOT}/meta/bases/{quote(base_id, safe='')}/tables"
        payload = self._get(url, token)
        tables = payload.get("tables")
        if not isinstance(tables, list):
            raise ConnectorError("Airtable returned an invalid base schema.")
        return [table for table in tables if isinstance(table, dict)]

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        base_id, token = self._base_and_token(config, credentials)
        tables = self._schema(base_id, token)
        return ProbeResult(
            ok=True,
            family=self.family,
            message=f"Connected to Airtable base ({len(tables)} tables visible)",
            capabilities=self.capabilities(),
            details={"tables": str(len(tables))},
        )

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        base_id, token = self._base_and_token(config, credentials)
        tables = self._schema(base_id, token)
        discovered: list[DiscoveredTable] = []
        for table in tables:
            table_id = str(table.get("id", ""))
            name = str(table.get("name", ""))
            if not table_id or not name:
                continue
            columns = tuple(
                ColumnSchema(
                    name=str(field.get("name", "")),
                    arrow_type=str(field.get("type", "unknown")),
                    nullable=True,
                )
                for field in table.get("fields", [])
                if isinstance(field, dict) and field.get("name")
            )
            discovered.append(
                DiscoveredTable(
                    name=name,
                    columns=columns,
                    metadata={"table_id": table_id},
                )
            )
        return tuple(discovered)

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]:
        if is_cancelled():
            raise ConnectorError("Airtable extraction cancelled before start")
        started_at = self._clock.now()
        base_id, token = self._base_and_token(config, credentials)
        schema = self._schema(base_id, token)
        table_by_name = {
            str(table.get("name")): table
            for table in schema
            if table.get("name") and table.get("id")
        }
        if not request.selected_tables:
            raise ValidationError("Select at least one Airtable table to import.")

        batches: list[ExtractionBatch] = []
        table_schemas: list[TableSchema] = []
        total_rows = 0
        total_bytes = 0
        hasher = hashlib.sha256()
        total_decoded = 0

        for name in request.selected_tables:
            if is_cancelled():
                raise ConnectorError("Airtable extraction cancelled")
            metadata = table_by_name.get(name)
            if metadata is None:
                raise ValidationError(f"Selected Airtable table {name!r} is not in this base.")
            table_id = str(metadata["id"])
            fields = [
                str(field["name"])
                for field in metadata.get("fields", [])
                if isinstance(field, dict) and field.get("name")
            ]
            requested_fields = request.selected_columns.get(name) or tuple(fields)
            unknown = set(requested_fields) - set(fields)
            if unknown:
                raise ValidationError(f"Selected Airtable field {sorted(unknown)[0]!r} was not found.")

            url = f"{self._API_ROOT}/{quote(base_id, safe='')}/{quote(table_id, safe='')}"
            records: list[dict[str, Any]] = []
            seen_offsets: set[str] = set()
            offset: str | None = None
            page_count = 0
            while True:
                if is_cancelled():
                    raise ConnectorError("Airtable extraction cancelled")
                if page_count >= request.limits.rest_max_completed_pages:
                    raise LimitExceededError(
                        "Airtable reached the 50-page import limit before the table was complete.",
                        remediation="Use a filtered Airtable view or import fewer records per source.",
                    )
                page = self._get(url, token, {"pageSize": "100", **({"offset": offset} if offset else {})})
                raw_page = json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                total_decoded += len(raw_page)
                if total_decoded > request.limits.rest_max_decoded_bytes_per_job:
                    raise LimitExceededError("Airtable import exceeds the 80,000,000 byte response limit.")
                page_records = page.get("records", [])
                if not isinstance(page_records, list):
                    raise ConnectorError("Airtable returned invalid records data.")
                for record in page_records:
                    if not isinstance(record, dict):
                        continue
                    if len(records) >= request.limits.max_rows_per_dataset:
                        raise LimitExceededError(f"Airtable table {name!r} exceeds the row limit.")
                    values = record.get("fields", {})
                    if isinstance(values, dict):
                        records.append({field: values.get(field) for field in requested_fields})
                page_count += 1
                next_offset = page.get("offset")
                if not next_offset:
                    break
                offset = str(next_offset)
                if offset in seen_offsets:
                    raise ConnectorError("Airtable repeated a pagination cursor.")
                seen_offsets.add(offset)

            columns: dict[str, pa.Array] = {}
            for field in requested_fields:
                values = [record.get(field) for record in records]
                normalized = [
                    json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(value, (dict, list))
                    else value
                    for value in values
                ]
                try:
                    columns[field] = pa.array(normalized)
                except (pa.ArrowException, TypeError, ValueError):
                    columns[field] = pa.array(
                        [None if value is None else str(value) for value in normalized],
                        type=pa.string(),
                    )
            arrow_table = (
                pa.table(columns)
                if columns
                else pa.table({"_empty": pa.array([None] * len(records), type=pa.null())})
            )
            ipc_bytes = serialize_arrow_ipc_checked(arrow_table, request.limits, total_bytes)
            col_schemas = tuple(
                ColumnSchema(name=column.name, arrow_type=str(column.type), nullable=True)
                for column in arrow_table.schema
            )
            batches.append(
                ExtractionBatch(name, ipc_bytes, arrow_table.num_rows, len(ipc_bytes))
            )
            table_schemas.append(
                TableSchema(name, col_schemas, arrow_table.num_rows, len(ipc_bytes))
            )
            total_rows += arrow_table.num_rows
            total_bytes += len(ipc_bytes)
            hasher.update(name.encode("utf-8"))
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
            source_config_summary={"family": self.family.value, "base_id": base_id},
        )
        return batches, manifest
