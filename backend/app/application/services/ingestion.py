import hashlib
from typing import Any, Callable

from app.adapters.connectors.registry import ConnectorRegistry
from app.application.ports.connectors import ExtractionRequest
from app.application.ports.repositories import SourceRepository
from app.application.ports.storage import ArtifactStore
from app.domain.limits import DataLimits, DEFAULT_DATA_LIMITS
from app.domain.models import ConnectorFamily, SnapshotManifest


def table_key(table_name: str) -> str:
    return "t_" + hashlib.sha256(table_name.encode("utf-8")).hexdigest()[:16]


def table_artifact_id(artifact_id: str, table_name: str) -> str:
    return f"{artifact_id}__{table_key(table_name)}"


class IngestionService:
    def __init__(
        self,
        connectors: ConnectorRegistry,
        artifacts: ArtifactStore,
        sources: SourceRepository,
        limits: DataLimits = DEFAULT_DATA_LIMITS,
    ) -> None:
        self._connectors = connectors
        self._artifacts = artifacts
        self._sources = sources
        self._limits = limits

    def ingest_source(
        self,
        *,
        family: ConnectorFamily,
        config: dict[str, Any],
        credentials: dict[str, str],
        workspace_id: str,
        source_id: str,
        job_id: str,
        selected_tables: tuple[str, ...],
        selected_columns: dict[str, tuple[str, ...]],
        version: int,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> SnapshotManifest:
        extractor = self._connectors.get_extractor(family)
        req = ExtractionRequest(
            workspace_id=workspace_id,
            source_id=source_id,
            job_id=job_id,
            selected_tables=selected_tables,
            selected_columns=selected_columns,
            limits=self._limits,
            version=version,
        )
        reserved = False
        try:
            batches, manifest = extractor.extract(config, credentials, req, is_cancelled)
            self._artifacts.reserve_workspace_bytes(workspace_id, manifest.total_normalized_bytes)
            reserved = True

            for batch in batches:
                staged_ref = self._artifacts.stage_bytes(
                    job_id,
                    f"{table_key(batch.table_name)}.arrow",
                    batch.arrow_ipc_bytes,
                    "application/vnd.apache.arrow.stream",
                )
                self._artifacts.publish_staged(
                    job_id,
                    staged_ref,
                    table_artifact_id(manifest.artifact_id, batch.table_name),
                )

            self._sources.publish_snapshot(manifest)
            return manifest
        except Exception:
            if reserved:
                self._artifacts.release_workspace_bytes(workspace_id, manifest.total_normalized_bytes)
            raise
        finally:
            self._artifacts.cleanup_job_staging(job_id)
