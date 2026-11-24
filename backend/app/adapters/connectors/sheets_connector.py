import hashlib
import json
import random
import time
from typing import Any, Callable, Iterable


from app.adapters.parsers.inference import infer_table
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
    ConnectorFamily,
    ConsistencyLevel,
    ExtractionCapabilities,
    SnapshotManifest,
    TableSchema,
)

SHEETS_READONLY_SCOPE = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class GoogleSheetsConnector:
    family = ConnectorFamily.GOOGLE_SHEETS

    def __init__(self, clock: Clock, service_factory: Callable[[dict[str, str]], Any] | None = None) -> None:
        self._clock = clock
        self._service_factory = service_factory or self._default_service_factory

    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            transactional_snapshot=False,
            resumable_cursor=False,
            supports_incremental=False,
        )

    def _default_service_factory(self, credentials: dict[str, str]) -> Any:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        sa_json = credentials.get("service_account_json")
        if not sa_json:
            raise ValidationError(
                "Missing Google service account credentials.",
                remediation="Configure a service account JSON and share the spreadsheet with its client_email as Viewer.",
            )
        info = json.loads(sa_json)
        creds = Credentials.from_service_account_info(info, scopes=SHEETS_READONLY_SCOPE)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    def _with_quota_retry(self, fn: Callable[[], Any], max_attempts: int = 4) -> Any:
        for attempt in range(max_attempts):
            try:
                return fn()
            except Exception as exc:
                msg = str(exc).lower()
                is_quota = "429" in msg or "quota" in msg or "rate limit" in msg
                if not is_quota or attempt == max_attempts - 1:
                    raise ConnectorError(f"Google Sheets API error: {exc}", retryable=is_quota) from exc
                delay = min(16.0, (2 ** attempt) + random.uniform(0.0, 0.5))
                time.sleep(delay)
        raise ConnectorError("Google Sheets retry exhausted", retryable=True)

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        spreadsheet_id = str(config.get("spreadsheet_id", "")).strip()
        if not spreadsheet_id:
            raise ValidationError("spreadsheet_id is required")
        service = self._service_factory(credentials)
        meta = self._with_quota_retry(
            lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        )
        props = meta.get("properties", {})
        return ProbeResult(
            ok=True,
            family=self.family,
            message=f"Accessible read-only spreadsheet: {props.get('title', spreadsheet_id)}",
            capabilities=self.capabilities(),
            details={
                "locale": str(props.get("locale", "unknown")),
                "timeZone": str(props.get("timeZone", "UTC")),
            },
        )

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        spreadsheet_id = str(config.get("spreadsheet_id", "")).strip()
        service = self._service_factory(credentials)
        meta = self._with_quota_retry(
            lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        )
        sheets = meta.get("sheets", [])
        if len(sheets) > 20:
            raise LimitExceededError("Spreadsheet exceeds max_workbook_sheets (20)")
        return tuple(
            DiscoveredTable(
                name=s["properties"]["title"],
                columns=(),
                estimated_rows=s["properties"].get("gridProperties", {}).get("rowCount"),
                metadata={"sheetId": str(s["properties"].get("sheetId", ""))},
            )
            for s in sheets
        )

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]:
        if is_cancelled():
            raise ConnectorError("Google Sheets extraction cancelled before start")
        started_at = self._clock.now()
        spreadsheet_id = str(config.get("spreadsheet_id", "")).strip()
        ranges = list(config.get("ranges") or request.selected_tables)
        if not ranges:
            raise ValidationError("At least one sheet range must be selected")

        service = self._service_factory(credentials)
        meta = self._with_quota_retry(
            lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        )
        props = meta.get("properties", {})
        locale = str(props.get("locale", "en_US"))
        tz = str(props.get("timeZone", "UTC"))

        resp = self._with_quota_retry(
            lambda: service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=spreadsheet_id,
                ranges=ranges,
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING",
            )
            .execute()
        )

        batches: list[ExtractionBatch] = []
        table_schemas: list[TableSchema] = []
        total_rows = 0
        total_bytes = 0
        hasher = hashlib.sha256()

        for vr in resp.get("valueRanges", []):
            if is_cancelled():
                raise ConnectorError("Google Sheets extraction cancelled")
            rng_name = str(vr.get("range", "sheet")).split("!")[0].strip("'")
            values = vr.get("values", [])
            if not values:
                continue
            headers = [str(h) if h is not None else f"column_{i + 1}" for i, h in enumerate(values[0])]
            data_rows = values[1:]
            col_lists: list[list[str | None]] = [[] for _ in headers]
            for row in data_rows:
                for i in range(len(headers)):
                    v = row[i] if i < len(row) else None
                    col_lists[i].append(None if v is None else str(v))
            arrow_table, col_schemas = infer_table(headers, col_lists)
            ipc_bytes = serialize_arrow_ipc_checked(arrow_table, request.limits, total_bytes)
            batches.append(
                ExtractionBatch(
                    table_name=rng_name,
                    arrow_ipc_bytes=ipc_bytes,
                    row_count=arrow_table.num_rows,
                    normalized_bytes=len(ipc_bytes),
                )
            )
            table_schemas.append(
                TableSchema(
                    table_name=rng_name,
                    columns=tuple(col_schemas),
                    row_count=arrow_table.num_rows,
                    normalized_bytes=len(ipc_bytes),
                )
            )
            total_rows += arrow_table.num_rows
            total_bytes += len(ipc_bytes)
            hasher.update(rng_name.encode("utf-8"))

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
            consistency_level=ConsistencyLevel.BEST_EFFORT_WINDOW,
            extraction_started_at=started_at,
            extraction_completed_at=completed_at,
            is_complete=True,
            is_sampled=False,
            artifact_id=f"art_{request.job_id}",
            sha256=hasher.hexdigest(),
            source_config_summary={
                "family": self.family.value,
                "locale": locale,
                "timezone": tz,
            },
        )
        return batches, manifest
