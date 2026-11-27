import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import (
    CreateDatasetNoteRequest,
    ApproveSemanticsRequest,
)
from app.application.services.profiling import profile_arrow_table
from app.bootstrap.container import AppContainer
from app.domain.errors import (
    NotFoundError,
    VersionConflictError,
)
from app.domain.limits import (
    DEFAULT_DATA_LIMITS,
)

from app.api.context import ApiContext
from app.api.responses import DatasetNoteListOut, DatasetNoteOut, SnapshotListOut

logger = logging.getLogger(__name__)

# Notes are handed to the agents with the schema, so a dataset keeps only a short, curated list.
MAX_DATASET_NOTES = 20


def _table_or_first(snap: dict[str, Any], table: str | None) -> str:
    if table:
        return table
    tables = snap.get("tables") or []
    if not tables:
        raise HTTPException(status_code=404, detail="This dataset has no tables")
    return str(tables[0])


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    _db_unavailable = ctx.db_unavailable
    _get_query_engine = ctx.get_query_engine
    _get_workspace_snapshot = ctx.get_workspace_snapshot

    @router.get("/api/v1/workspaces/{workspace_id}/snapshots", response_model=SnapshotListOut, response_model_exclude_unset=True)
    def list_snapshots(workspace_id: str) -> dict[str, Any]:
        if hasattr(app.state, "repo_snapshots"):
            db_snaps = app.state.repo_snapshots.list_snapshots(workspace_id)
            if db_snaps:
                return {"snapshots": db_snaps}
        items = []
        for snap in app.state.snapshots.values():
            if snap["workspace_id"] != workspace_id:
                continue
            sem = app.state.semantics.get(snap["snapshot_id"]) or {}
            items.append(
                {
                    **snap,
                    "semantic_version_id": sem.get("semantic_version_id"),
                    "approved": bool(sem.get("approved")),
                }
            )
        # Same contract as the database path: newest first.
        items.sort(key=lambda s: str(s.get("created_at") or ""), reverse=True)
        return {"snapshots": items}

    @router.get("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}/profile")
    def profile_snapshot(workspace_id: str, snapshot_id: str, table: str | None = None) -> dict[str, Any]:
        snap = _get_workspace_snapshot(workspace_id, snapshot_id)
        table = _table_or_first(snap, table)
        arrow_table = _get_query_engine().load_table(snap, table)
        profiles = profile_arrow_table(arrow_table, is_sampled=bool(snap.get("is_sampled", False)))
        return {
            "snapshot_id": snapshot_id,
            "table": table,
            "columns": [
                {
                    "column_name": profile.column_name,
                    "null_count": profile.null_count,
                    "distinct_count": profile.distinct_count,
                    "duplicate_count": profile.duplicate_count,
                    "min_value": profile.min_value,
                    "max_value": profile.max_value,
                    "is_sampled": profile.is_sampled,
                }
                for profile in profiles
            ],
        }

    @router.get("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}/preview")
    def preview_snapshot(
        workspace_id: str,
        snapshot_id: str,
        table: str | None = None,
        limit: int = Query(default=100, ge=1, le=DEFAULT_DATA_LIMITS.max_preview_rows),
    ) -> dict[str, Any]:
        snap = _get_workspace_snapshot(workspace_id, snapshot_id)
        table = _table_or_first(snap, table)
        preview = _get_query_engine().preview(snap, table, limit)
        return {"snapshot_id": snapshot_id, "table": table, "limit": limit, **preview}

    @router.get("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}/diagnostics")
    def snapshot_diagnostics(workspace_id: str, snapshot_id: str) -> dict[str, Any]:
        snap = _get_workspace_snapshot(workspace_id, snapshot_id)
        diagnostics = snap.get("diagnostics")
        return {
            "snapshot_id": snapshot_id,
            "available": bool(diagnostics),
            "diagnostics": diagnostics,
            "consistency_level": snap.get("consistency_level"),
            "total_rows": snap.get("total_rows", 0),
            "total_normalized_bytes": snap.get("total_normalized_bytes", 0),
            "tables": [
                {
                    "name": table.get("name"),
                    "row_count": table.get("row_count", 0),
                    "normalized_bytes": table.get("normalized_bytes", 0),
                }
                for table in (snap.get("table_details") or [])
            ],
        }

    @router.get("/api/v1/workspaces/{workspace_id}/semantics/{snapshot_id}")
    def get_semantics(workspace_id: str, snapshot_id: str) -> dict[str, Any]:
        _get_workspace_snapshot(workspace_id, snapshot_id)
        if hasattr(app.state, "repo_snapshots"):
            sem = app.state.repo_snapshots.get_semantics(snapshot_id)
            if sem:
                return sem
        sem = app.state.semantics.get(snapshot_id)
        if not sem:
            raise HTTPException(status_code=404, detail="Semantic version not found")
        return sem

    @router.post("/api/v1/workspaces/{workspace_id}/semantics/{snapshot_id}/approve")
    async def approve_semantics(workspace_id: str, snapshot_id: str, payload: ApproveSemanticsRequest) -> dict[str, Any]:
        await asyncio.to_thread(_get_workspace_snapshot, workspace_id, snapshot_id)
        res = await asyncio.to_thread(_approve_semantics, snapshot_id, payload.expected_version)
        return {**res, "refreshed_pin_ids": await ctx.auto_refresh_pins(workspace_id, snapshot_id)}

    def _approve_semantics(snapshot_id: str, expected_version: int) -> dict[str, Any]:
        try:
            res: dict[str, Any] = app.state.repo_snapshots.approve_semantics(snapshot_id, expected_version)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="Semantic version not found") from exc
        except VersionConflictError as exc:
            raise HTTPException(status_code=409, detail="Semantic version conflict") from exc
        except SQLAlchemyError as exc:
            logger.exception("Failed to approve semantics")
            raise _db_unavailable("approve the definitions") from exc
        app.state.semantics[snapshot_id] = res
        return res

    @router.get("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}/notes", response_model=DatasetNoteListOut)
    def list_dataset_notes(workspace_id: str, snapshot_id: str) -> dict[str, Any]:
        snapshot = _get_workspace_snapshot(workspace_id, snapshot_id)
        try:
            notes = app.state.repo_snapshots.list_dataset_notes(workspace_id, str(snapshot["source_id"]))
        except SQLAlchemyError as exc:
            logger.exception("Failed to load dataset notes")
            raise _db_unavailable("load the dataset notes") from exc
        return {"notes": notes, "limit": MAX_DATASET_NOTES}

    @router.post("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}/notes", status_code=201, response_model=DatasetNoteOut)
    def add_dataset_note(workspace_id: str, snapshot_id: str, payload: CreateDatasetNoteRequest) -> dict[str, Any]:
        snapshot = _get_workspace_snapshot(workspace_id, snapshot_id)
        text = " ".join(payload.text.split())
        if len(text) < 3:
            raise HTTPException(status_code=422, detail="A note needs a few words")
        try:
            note: dict[str, Any] | None = app.state.repo_snapshots.add_dataset_note(
                workspace_id, str(snapshot["source_id"]), f"note_{uuid.uuid4().hex[:12]}", text, MAX_DATASET_NOTES
            )
        except SQLAlchemyError as exc:
            logger.exception("Failed to save a dataset note")
            raise _db_unavailable("save the note") from exc
        if note is None:
            raise HTTPException(status_code=409, detail=f"A dataset can hold {MAX_DATASET_NOTES} notes; delete one first.")
        return note

    @router.delete("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}/notes/{note_id}")
    def delete_dataset_note(workspace_id: str, snapshot_id: str, note_id: str) -> dict[str, Any]:
        snapshot = _get_workspace_snapshot(workspace_id, snapshot_id)
        try:
            removed = app.state.repo_snapshots.delete_dataset_note(workspace_id, str(snapshot["source_id"]), note_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to delete a dataset note")
            raise _db_unavailable("delete the note") from exc
        if not removed:
            raise HTTPException(status_code=404, detail="Note not found")
        return {"note_id": note_id, "deleted": True}

    @router.delete("/api/v1/workspaces/{workspace_id}/snapshots/{snapshot_id}")
    def delete_snapshot(workspace_id: str, snapshot_id: str, force: bool = False) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            existing = app.state.repo_snapshots.get_snapshot(snapshot_id)
            if not existing or existing["workspace_id"] != workspace_id:
                raise HTTPException(status_code=404, detail="Dataset not found")
            pinned = app.state.repo_snapshots.pinned_runs_for_snapshot(workspace_id, snapshot_id)
            if pinned and not force:
                raise HTTPException(
                    status_code=409,
                    detail=f"{len(pinned)} pinned answer(s) use this dataset; delete with force=true to remove it anyway.",
                )
            removed = app.state.repo_snapshots.delete_snapshot(workspace_id, snapshot_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to delete snapshot %s", snapshot_id)
            raise _db_unavailable("delete the dataset") from exc
        if removed is None:
            raise HTTPException(status_code=404, detail="Dataset not found")
        for artifact_id in (removed.get("table_artifacts") or {}).values():
            c.artifacts.delete(str(artifact_id))
        app.state.repo_quota.release_workspace_bytes(workspace_id, int(removed.get("total_normalized_bytes") or 0))
        app.state.snapshots.pop(snapshot_id, None)
        app.state.semantics.pop(snapshot_id, None)
        _get_query_engine().forget_snapshot(snapshot_id)
        return {"snapshot_id": snapshot_id, "deleted": True}

    ctx.approve_semantics = approve_semantics
    return router
