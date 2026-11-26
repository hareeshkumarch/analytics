import hashlib
import logging
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from app.application.ports.storage import StoredArtifact
from app.domain.errors import NotFoundError, SecurityPolicyError, ValidationError

logger = logging.getLogger(__name__)
COPY_CHUNK_BYTES = 1024 * 1024


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except PermissionError:
        logger.warning("Could not restrict permissions on %s", path)


class LocalArtifactStore:
    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir).resolve()
        self._staging_dir = self._root / "staging"
        self._published_dir = self._root / "published"
        for directory in (self._root, self._staging_dir, self._published_dir):
            _private_dir(directory)

    def _safe_id(self, identifier: str) -> str:
        if not identifier or "/" in identifier or "\\" in identifier or ".." in identifier:
            raise SecurityPolicyError(f"Invalid opaque identifier: {identifier!r}")
        return identifier

    def _published_path(self, artifact_id: str) -> Path:
        path = (self._published_dir / self._safe_id(artifact_id)).resolve()
        if path.parent != self._published_dir:
            raise SecurityPolicyError("Artifact path escapes the published directory")
        return path

    def stage_bytes(self, job_id: str, name: str, data: bytes, content_type: str) -> str:
        safe_job = self._safe_id(job_id)
        safe_name = self._safe_id(name)
        job_dir = self._staging_dir / safe_job
        job_dir.mkdir(parents=True, exist_ok=True)
        target = (job_dir / safe_name).resolve()
        if not str(target).startswith(str(job_dir.resolve())):
            raise SecurityPolicyError("Path traversal blocked in stage_bytes")
        tmp_path = job_dir / f".tmp_{safe_name}_{uuid.uuid4().hex}"
        tmp_path.write_bytes(data)
        os.replace(tmp_path, target)
        return f"{safe_job}/{safe_name}"

    def publish_staged(self, job_id: str, staged_ref: str, artifact_id: str) -> StoredArtifact:
        safe_job = self._safe_id(job_id)
        safe_artifact = self._safe_id(artifact_id)
        parts = staged_ref.split("/", 1)
        if len(parts) != 2 or parts[0] != safe_job:
            raise SecurityPolicyError("Staged reference does not belong to job")
        job_dir = (self._staging_dir / safe_job).resolve()
        staged_path = (job_dir / self._safe_id(parts[1])).resolve()
        if staged_path.parent != job_dir:
            raise SecurityPolicyError("Staged path escapes the job directory")
        if not staged_path.exists():
            raise NotFoundError(f"Staged artifact not found: {staged_ref}")

        dest_path = self._published_path(safe_artifact)
        tmp_dest = self._published_dir / f".tmp_{safe_artifact}_{uuid.uuid4().hex}"
        digest = hashlib.sha256()
        size = 0
        try:
            with staged_path.open("rb") as src, tmp_dest.open("wb") as dst:
                while chunk := src.read(COPY_CHUNK_BYTES):
                    digest.update(chunk)
                    dst.write(chunk)
                    size += len(chunk)
            os.replace(tmp_dest, dest_path)
        finally:
            tmp_dest.unlink(missing_ok=True)

        return StoredArtifact(
            artifact_id=safe_artifact,
            sha256=digest.hexdigest(),
            byte_size=size,
            content_type="application/octet-stream",
        )

    def read_bytes(self, artifact_id: str) -> bytes:
        path = self._published_path(artifact_id)
        if not path.exists():
            raise NotFoundError(f"Artifact {artifact_id} not found")
        return path.read_bytes()

    @contextmanager
    def open_read_stream(self, artifact_id: str) -> Iterator[BinaryIO]:
        path = self._published_path(artifact_id)
        if not path.exists():
            raise NotFoundError(f"Artifact {artifact_id} not found")
        with path.open("rb") as stream:
            yield stream

    def exists(self, artifact_id: str) -> bool:
        try:
            return self._published_path(artifact_id).exists()
        except SecurityPolicyError:
            return False

    def delete(self, artifact_id: str) -> None:
        (self._published_dir / self._safe_id(artifact_id)).unlink(missing_ok=True)

    def delete_older_than(self, prefix: str, max_age_seconds: float) -> int:
        cutoff = time.time() - max_age_seconds
        removed = 0
        for path in self._published_dir.glob(f"{self._safe_id(prefix)}*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def cleanup_job_staging(self, job_id: str) -> None:
        safe_job = self._safe_id(job_id)
        job_dir = (self._staging_dir / safe_job).resolve()
        if job_dir.exists() and str(job_dir).startswith(str(self._staging_dir.resolve())):
            try:
                shutil.rmtree(job_dir)
            except OSError:
                logger.warning("Could not remove staging directory %s", job_dir, exc_info=True)


UPLOAD_MAX_AGE_SECONDS = 24 * 3600


class LocalUploadStore:
    def __init__(self, root_dir: str | Path, max_age_seconds: int = UPLOAD_MAX_AGE_SECONDS) -> None:
        self._root = Path(root_dir).resolve() / "uploads"
        _private_dir(self._root)
        self._max_age_seconds = max_age_seconds

    def _path(self, workspace_id: str, upload_id: str) -> Path:
        if not upload_id.startswith("upl_") or not upload_id[4:].isalnum():
            raise ValidationError("upload_id is not valid")
        namespace = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:16]
        return self._root / namespace / upload_id

    def _sweep(self) -> None:
        cutoff = time.time() - self._max_age_seconds
        for path in self._root.glob("*/upl_*"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def save(self, workspace_id: str, data: bytes) -> str:
        self._sweep()
        upload_id = f"upl_{uuid.uuid4().hex}"
        path = self._path(workspace_id, upload_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return upload_id

    def read(self, workspace_id: str, upload_id: str) -> bytes:
        path = self._path(workspace_id, upload_id)
        if not path.is_file():
            raise NotFoundError("This upload has expired or was already imported. Choose the file again.")
        return path.read_bytes()

    def delete(self, workspace_id: str, upload_id: str) -> None:
        self._path(workspace_id, upload_id).unlink(missing_ok=True)
