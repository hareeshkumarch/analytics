from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.application.ports.storage import ArtifactStore
from app.application.services.artifact_lifecycle import ArtifactIndex, sweep_artifacts


class RestoreState(Protocol):
    def dataset_files(self) -> list[tuple[str, dict[str, str], tuple[str, ...]]]: ...
    def set_unavailable_tables(self, snapshot_id: str, tables: list[str]) -> None: ...
    def export_ids(self) -> list[str]: ...
    def active_runs_without_job(self) -> list[str]: ...
    def enqueue_runs(self, run_ids: list[str]) -> None: ...


@dataclass(slots=True)
class ReconcileReport:
    datasets_missing_files: dict[str, list[str]] = field(default_factory=dict)
    datasets_recovered: list[str] = field(default_factory=list)
    exports_removed: list[str] = field(default_factory=list)
    runs_requeued: list[str] = field(default_factory=list)
    orphans_removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.datasets_missing_files or self.datasets_recovered or self.exports_removed or self.runs_requeued or self.orphans_removed)

    def summary(self) -> str:
        if not self.changed:
            return "Nothing to reconcile: the database and the artifact files agree."
        lines = []
        for snapshot_id, tables in sorted(self.datasets_missing_files.items()):
            lines.append(f"Dataset {snapshot_id} is missing files for {', '.join(tables)}; it is marked unavailable until it is re-imported.")
        lines += [f"Dataset {snapshot_id} has all its files again and is available." for snapshot_id in self.datasets_recovered]
        lines += [f"Removed export {artifact_id}: its file is missing." for artifact_id in self.exports_removed]
        lines += [f"Re-queued run {run_id}." for run_id in self.runs_requeued]
        if self.orphans_removed:
            lines.append(f"Removed {self.orphans_removed} files that nothing references.")
        return "\n".join(lines)


def reconcile_after_restore(
    state: RestoreState, index: ArtifactIndex, store: ArtifactStore, *, now: datetime, requeue_runs: bool
) -> ReconcileReport:
    report = ReconcileReport()
    for snapshot_id, tables, flagged in state.dataset_files():
        missing = sorted(table for table, artifact_id in tables.items() if not store.exists(artifact_id))
        if missing and missing != sorted(flagged):
            state.set_unavailable_tables(snapshot_id, missing)
            report.datasets_missing_files[snapshot_id] = missing
        elif not missing and flagged:
            state.set_unavailable_tables(snapshot_id, [])
            report.datasets_recovered.append(snapshot_id)
    report.exports_removed = [artifact_id for artifact_id in state.export_ids() if not store.exists(artifact_id)]
    index.delete_exports(report.exports_removed)
    if requeue_runs:
        report.runs_requeued = state.active_runs_without_job()
        state.enqueue_runs(report.runs_requeued)
    report.orphans_removed = sweep_artifacts(store, index, now).orphans
    return report
