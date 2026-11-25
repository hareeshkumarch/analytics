import hashlib
from pathlib import Path
from typing import Any, Callable, Iterable

from app.adapters.parsers.registry import ParsedTable, ParserRegistry
from app.application.ports.clock import Clock
from app.application.ports.connectors import (
    DiscoveredTable,
    ExtractionBatch,
    ExtractionRequest,
    ProbeResult,
)
from app.domain.errors import ConnectorError, LimitExceededError, ValidationError
from app.domain.limits import DEFAULT_DATA_LIMITS, DataLimits
from app.domain.models import (
    ConnectorFamily,
    ConsistencyLevel,
    ExtractionCapabilities,
    SnapshotManifest,
    TableSchema,
)


class UploadedFileConnector:
    family = ConnectorFamily.UPLOADED_FILE

    def __init__(self, parser_registry: ParserRegistry, clock: Clock) -> None:
        self._parsers = parser_registry
        self._clock = clock

    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            transactional_snapshot=False,
            resumable_cursor=False,
            supports_incremental=False,
        )

    def _read_inputs(self, config: dict[str, Any]) -> list[tuple[bytes, str, str]]:
        files = config.get("files")
        if files is None:
            return [self._read_config_bytes(config)]
        fmt = str(config.get("format", "")).lower()
        return [(bytes(raw), fmt, str(name)) for name, raw in files]

    def _parse_all(self, config: dict[str, Any], limits: DataLimits) -> tuple[list[ParsedTable], str]:
        parsed: list[ParsedTable] = []
        fmt = ""
        for raw_bytes, fmt, table_name in self._read_inputs(config):
            parsed.extend(self._parsers.get(fmt).parse(raw_bytes, table_name, limits, config.get("options")))
        return parsed, fmt

    def _read_config_bytes(self, config: dict[str, Any]) -> tuple[bytes, str, str]:
        fmt = str(config.get("format", "")).lower()
        table_name = str(config.get("table_name") or "uploaded_table")
        if "raw_bytes" in config and isinstance(config["raw_bytes"], (bytes, bytearray)):
            return bytes(config["raw_bytes"]), fmt, table_name
        file_path = config.get("file_path")
        if not file_path:
            raise ValidationError("Uploaded file connector requires 'file_path' or 'raw_bytes'")
        path = Path(str(file_path))
        if not path.exists() or not path.is_file():
            raise ConnectorError(f"Uploaded file not found: {path.name}")
        return path.read_bytes(), fmt, table_name

    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult:
        inputs = self._read_inputs(config)
        fmt = inputs[0][1] if inputs else ""
        self._parsers.get(fmt)
        size = sum(len(raw) for raw, _, _ in inputs)
        if size > DEFAULT_DATA_LIMITS.max_uploaded_file_bytes:
            raise LimitExceededError("Uploaded file exceeds 40,000,000 bytes limit")
        return ProbeResult(
            ok=True,
            family=self.family,
            message=f"Validated {fmt.upper()} file ({size} bytes)",
            capabilities=self.capabilities(),
            details={"format": fmt, "byte_size": str(size)},
        )

    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]:
        parsed_tables, fmt = self._parse_all(config, DEFAULT_DATA_LIMITS)
        return tuple(
            DiscoveredTable(
                name=pt.table_name,
                columns=pt.columns,
                estimated_rows=pt.row_count,
                metadata={"format": fmt},
            )
            for pt in parsed_tables
        )

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]:
        if is_cancelled():
            raise ConnectorError("Extraction cancelled before start")
        started_at = self._clock.now()
        parsed_tables, fmt = self._parse_all(config, request.limits)

        selected = set(request.selected_tables) if request.selected_tables else None
        if selected is not None and not any(pt.table_name in selected for pt in parsed_tables):
            raise ValidationError(
                f"None of the selected tables were found: {', '.join(sorted(selected))}",
                remediation="Re-run discovery and select from the tables it lists.",
            )
        batches: list[ExtractionBatch] = []
        table_schemas: list[TableSchema] = []
        total_rows = 0
        total_bytes = 0
        hasher = hashlib.sha256()

        for pt in parsed_tables:
            if is_cancelled():
                raise ConnectorError("Extraction cancelled during processing")
            if selected is not None and pt.table_name not in selected:
                continue
            batches.append(
                ExtractionBatch(
                    table_name=pt.table_name,
                    arrow_ipc_bytes=pt.ipc_bytes,
                    row_count=pt.row_count,
                    normalized_bytes=pt.normalized_bytes,
                )
            )
            table_schemas.append(
                TableSchema(
                    table_name=pt.table_name,
                    columns=pt.columns,
                    row_count=pt.row_count,
                    normalized_bytes=pt.normalized_bytes,
                )
            )
            total_rows += pt.row_count
            total_bytes += pt.normalized_bytes
            hasher.update(pt.table_name.encode("utf-8"))
            for col in pt.columns:
                hasher.update(f"{col.name}:{col.arrow_type}".encode("utf-8"))

        if total_bytes > request.limits.max_normalized_source_bytes:
            raise LimitExceededError(
                f"Normalized size {total_bytes} exceeds limit {request.limits.max_normalized_source_bytes}"
            )

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
            source_config_summary={"family": self.family.value, "format": fmt},
        )
        return batches, manifest
