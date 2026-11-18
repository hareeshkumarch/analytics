import hashlib
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from app.application.ports.storage import StoredArtifact
from app.domain.errors import LimitExceededError, NotFoundError, SecurityPolicyError
from app.domain.limits import DataLimits, DEFAULT_DATA_LIMITS


class LocalArtifactStore:
    def __init__(self, root_dir: str | Path, limits: DataLimits = DEFAULT_DATA_LIMITS) -> None:
        self._root = Path(root_dir).resolve()
        self._staging_dir = self._root / "staging"
        self._published_dir = self._root / "published"
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        self._published_dir.mkdir(parents=True, exist_ok=True)
        self._limits = limits
        self._lock = threading.Lock()
        self._workspace_bytes: dict[str, int] = {}

    def _safe_id(self, identifier: str) -> str:
        if not identifier or "/" in identifier or "\\" in identifier or ".." in identifier:
            raise SecurityPolicyError(f"Invalid opaque identifier: {identifier!r}")
        return identifier

    def reserve_workspace_bytes(self, workspace_id: str, byte_count: int) -> None:
        if byte_count < 0:
            raise ValueError("byte_count must be non-negative")
        with self._lock:
            current = self._workspace_bytes.get(workspace_id, 0)
            if current + byte_count > self._limits.max_workspace_retained_raw_bytes:
                raise LimitExceededError(
                    f"Workspace raw data retention limit exceeded ({self._limits.max_workspace_retained_raw_bytes} bytes)",
                    remediation="Remove unused datasets or snapshots before uploading more data.",
                )
            self._workspace_bytes[workspace_id] = current + byte_count

    def release_workspace_bytes(self, workspace_id: str, byte_count: int) -> None:
        with self._lock:
            current = self._workspace_bytes.get(workspace_id, 0)
            self._workspace_bytes[workspace_id] = max(0, current - max(0, byte_count))

    def stage_bytes(self, job_id: str, name: str, data: bytes, content_type: str) -> str:
        safe_job = self._safe_id(job_id)
        safe_name = self._safe_id(name)
        job_dir = self._staging_dir / safe_job
        job_dir.mkdir(parents=True, exist_ok=True)
        target = (job_dir / safe_name).resolve()
        if not str(target).startswith(str(job_dir.resolve())):
            raise SecurityPolicyError("Path traversal blocked in stage_bytes")
        tmp_path = job_dir / f".tmp_{safe_name}_{os.getpid()}"
        tmp_path.write_bytes(data)
        os.replace(tmp_path, target)
        return f"{safe_job}/{safe_name}"

    def publish_staged(self, job_id: str, staged_ref: str, artifact_id: str) -> StoredArtifact:
        safe_job = self._safe_id(job_id)
        safe_artifact = self._safe_id(artifact_id)
        parts = staged_ref.split("/", 1)
        if len(parts) != 2 or parts[0] != safe_job:
            raise SecurityPolicyError("Staged reference does not belong to job")
        staged_path = (self._staging_dir / safe_job / self._safe_id(parts[1])).resolve()
        if not staged_path.exists():
            raise NotFoundError(f"Staged artifact not found: {staged_ref}")

        data = staged_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        dest_path = (self._published_dir / safe_artifact).resolve()
        tmp_dest = self._published_dir / f".tmp_{safe_artifact}_{os.getpid()}"
        tmp_dest.write_bytes(data)
        os.replace(tmp_dest, dest_path)

        return StoredArtifact(
            artifact_id=safe_artifact,
            sha256=digest,
            byte_size=len(data),
            content_type="application/octet-stream",
        )

    def read_bytes(self, artifact_id: str) -> bytes:
        safe_id = self._safe_id(artifact_id)
        path = (self._published_dir / safe_id).resolve()
        if not path.exists():
            raise NotFoundError(f"Artifact {safe_id} not found")
        return path.read_bytes()

    @contextmanager
    def open_read_stream(self, artifact_id: str) -> Iterator[BinaryIO]:
        safe_id = self._safe_id(artifact_id)
        path = (self._published_dir / safe_id).resolve()
        if not path.exists():
            raise NotFoundError(f"Artifact {safe_id} not found")
        with path.open("rb") as stream:
            yield stream

    def exists(self, artifact_id: str) -> bool:
        try:
            safe_id = self._safe_id(artifact_id)
        except SecurityPolicyError:
            return False
        return (self._published_dir / safe_id).exists()

    def get_published_path(self, artifact_id: str) -> Path:
        safe_id = self._safe_id(artifact_id)
        path = (self._published_dir / safe_id).resolve()
        if not path.exists():
            raise NotFoundError(f"Artifact {safe_id} not found")
        return path

    def cleanup_job_staging(self, job_id: str) -> None:
        safe_job = self._safe_id(job_id)
        job_dir = (self._staging_dir / safe_job).resolve()
        if job_dir.exists() and str(job_dir).startswith(str(self._staging_dir.resolve())):
            shutil.rmtree(job_dir, ignore_errors=True)
