from typing import Any, Protocol

from app.domain.events import RunEvent
from app.domain.models import Answer, RunStatus, SemanticVersion, SnapshotManifest


class EventLog(Protocol):
    def append(self, event: RunEvent) -> RunEvent: ...
    def list_after(self, run_id: str, after_sequence: int) -> tuple[RunEvent, ...]: ...


class RunRepository(Protocol):
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def update_status(
        self,
        run_id: str,
        status: RunStatus,
        outcome: str | None = None,
        event: RunEvent | None = None,
    ) -> None: ...
    def save_answer(self, answer: Answer) -> None: ...


class SourceRepository(Protocol):
    def get_latest_snapshot(self, workspace_id: str, source_id: str) -> SnapshotManifest | None: ...
    def publish_snapshot(self, manifest: SnapshotManifest) -> None: ...


class SemanticRepository(Protocol):
    def get_active_semantic(self, workspace_id: str, snapshot_id: str) -> SemanticVersion | None: ...
    def save_semantic(self, semantic: SemanticVersion) -> None: ...
