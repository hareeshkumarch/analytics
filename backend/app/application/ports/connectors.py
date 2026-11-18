from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from app.domain.limits import DataLimits
from app.domain.models import (
    ColumnSchema,
    ConnectorFamily,
    ExtractionCapabilities,
    SnapshotManifest,
)


@dataclass(frozen=True, slots=True)
class DiscoveredTable:
    name: str
    columns: tuple[ColumnSchema, ...]
    estimated_rows: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    family: ConnectorFamily
    message: str
    capabilities: ExtractionCapabilities
    details: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractionBatch:
    table_name: str
    arrow_ipc_bytes: bytes
    row_count: int
    normalized_bytes: int


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    workspace_id: str
    source_id: str
    job_id: str
    selected_tables: tuple[str, ...]
    selected_columns: dict[str, tuple[str, ...]]
    limits: DataLimits
    version: int
    cursor: str | None = None


class ConnectorDiscovery(Protocol):
    family: ConnectorFamily

    def capabilities(self) -> ExtractionCapabilities: ...
    def probe(self, config: dict[str, Any], credentials: dict[str, str]) -> ProbeResult: ...
    def discover(
        self, config: dict[str, Any], credentials: dict[str, str]
    ) -> tuple[DiscoveredTable, ...]: ...


class SnapshotExtractor(Protocol):
    family: ConnectorFamily

    def extract(
        self,
        config: dict[str, Any],
        credentials: dict[str, str],
        request: ExtractionRequest,
        is_cancelled: Callable[[], bool],
    ) -> tuple[Iterable[ExtractionBatch], SnapshotManifest]: ...
