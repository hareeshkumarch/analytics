import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.application.ports.storage import ArtifactStore

logger = logging.getLogger(__name__)

ORPHAN_GRACE_SECONDS = 3600
SWEEP_INTERVAL_SECONDS = 3600


class ArtifactIndex(Protocol):
    def expired_exports(self, now: datetime) -> list[str]: ...
    def delete_exports(self, artifact_ids: list[str]) -> None: ...
    def referenced_artifact_ids(self) -> set[str]: ...


@dataclass(frozen=True, slots=True)
class SweepResult:
    expired_exports: int
    orphans: int


def sweep_artifacts(artifacts: ArtifactStore, index: ArtifactIndex, now: datetime, orphan_grace_seconds: float = ORPHAN_GRACE_SECONDS) -> SweepResult:
    expired = index.expired_exports(now)
    for artifact_id in expired:
        artifacts.delete(artifact_id)
    index.delete_exports(expired)
    referenced = index.referenced_artifact_ids()
    orphans = [a for a in artifacts.list_published(older_than_seconds=orphan_grace_seconds) if a not in referenced]
    for artifact_id in orphans:
        artifacts.delete(artifact_id)
        logger.warning("Removed orphaned artifact %s", artifact_id)
    return SweepResult(expired_exports=len(expired), orphans=len(orphans))
