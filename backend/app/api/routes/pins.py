import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import (
    CreatePinRequest,
    UpdatePinRequest,
    CreateRunRequest,
)
from app.domain.models import RunMode, RunStatus

from app.api.context import ApiContext

logger = logging.getLogger(__name__)


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    _db_unavailable = ctx.db_unavailable
    _snapshot_record = ctx.snapshot_record
    _newest_approved_in_source = ctx.newest_approved_in_source

    @router.get("/api/v1/workspaces/{workspace_id}/pins")
    def list_pins(workspace_id: str) -> dict[str, Any]:
        try:
            return {"pins": app.state.repo_pins.list_pins(workspace_id)}
        except SQLAlchemyError as exc:
            logger.exception("Failed to query pins from DB")
            raise _db_unavailable("load pinned answers") from exc

    @router.post("/api/v1/workspaces/{workspace_id}/pins", status_code=201)
    def create_pin(workspace_id: str, payload: CreatePinRequest) -> dict[str, Any]:
        run_rec = app.state.repo_runs.get_run(payload.run_id)
        if run_rec:
            app.state.runs[payload.run_id] = run_rec
        else:
            run_rec = app.state.runs.get(payload.run_id)
        if not run_rec or run_rec.get("workspace_id") != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found in this workspace")
        if run_rec.get("status") != RunStatus.COMPLETED.value:
            raise HTTPException(status_code=409, detail="Only completed runs can be pinned")
        try:
            existing_pins = app.state.repo_pins.list_pins(workspace_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to check existing pins in DB")
            raise _db_unavailable("check existing pins") from exc
        existing = next((pin for pin in existing_pins if pin.get("run_id") == payload.run_id), None)
        if existing:
            return existing
        pin_id = f"pin_{uuid.uuid4().hex[:10]}"
        try:
            res = app.state.repo_pins.create_pin(
                pin_id=pin_id,
                workspace_id=workspace_id,
                title=payload.title,
                question_text=payload.question_text,
                run_id=payload.run_id,
            )
        except SQLAlchemyError as exc:
            logger.exception("Failed to create pin in DB")
            raise _db_unavailable("pin the answer") from exc
        return res

    @router.delete("/api/v1/workspaces/{workspace_id}/pins/{pin_id}")
    def delete_pin(workspace_id: str, pin_id: str) -> dict[str, Any]:
        try:
            removed = app.state.repo_pins.delete_pin(workspace_id, pin_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to delete pin in DB")
            raise _db_unavailable("remove the pin") from exc
        if not removed:
            raise HTTPException(status_code=404, detail="Pin not found")
        return {"pin_id": pin_id, "deleted": True}

    @router.patch("/api/v1/workspaces/{workspace_id}/pins/{pin_id}")
    def update_pin(workspace_id: str, pin_id: str, payload: UpdatePinRequest) -> dict[str, Any]:
        try:
            if not app.state.repo_pins.update_pin_state(workspace_id, pin_id, {"auto_refresh": payload.auto_refresh}):
                raise HTTPException(status_code=404, detail="Pin not found")
            return next(p for p in app.state.repo_pins.list_pins(workspace_id) if p["pin_id"] == pin_id)
        except SQLAlchemyError as exc:
            logger.exception("Failed to update pin")
            raise _db_unavailable("update the pin") from exc

    @router.post("/api/v1/workspaces/{workspace_id}/pins/{pin_id}/refresh", status_code=202)
    async def refresh_pin(workspace_id: str, pin_id: str) -> dict[str, Any]:
        pins = await asyncio.to_thread(app.state.repo_pins.list_pins, workspace_id)
        pin = next((p for p in pins if p["pin_id"] == pin_id), None)
        if not pin:
            raise HTTPException(status_code=404, detail="Pin not found")
        original_run = await asyncio.to_thread(app.state.repo_runs.get_run, pin["run_id"])
        try:
            mode = RunMode(str((original_run or {}).get("mode", RunMode.QUICK.value)))
        except ValueError:
            mode = RunMode.QUICK
        pinned_snapshot = await asyncio.to_thread(_snapshot_record, (original_run or {}).get("snapshot_id"))
        newest = (
            await asyncio.to_thread(_newest_approved_in_source, workspace_id, pinned_snapshot["source_id"])
            if pinned_snapshot
            else None
        )
        refreshed = await ctx.create_run(
            workspace_id,
            CreateRunRequest(
                question=pin["question_text"],
                mode=mode,
                background_async=True,
                snapshot_id=newest["snapshot_id"] if newest else None,
            ),
            # Called directly, so the Header() default object would otherwise act as a shared idempotency key.
            idempotency_key=None,
        )
        run_data = json.loads(refreshed.body)
        await asyncio.to_thread(
            app.state.repo_pins.update_pin_state, workspace_id, pin_id, {"latest_run_id": run_data["run_id"]}
        )
        return {"pin_id": pin_id, "refresh_run_id": run_data["run_id"], "status": run_data["status"]}

    async def _auto_refresh_pins(workspace_id: str, snapshot_id: str) -> list[str]:
        approved = await asyncio.to_thread(_snapshot_record, snapshot_id)
        if not approved:
            return []
        refreshed: list[str] = []
        for pin in await asyncio.to_thread(app.state.repo_pins.list_pins, workspace_id):
            if not pin.get("auto_refresh"):
                continue
            try:
                current = await asyncio.to_thread(app.state.repo_runs.get_run, pin.get("latest_run_id") or pin["run_id"])
                bound = await asyncio.to_thread(_snapshot_record, (current or {}).get("snapshot_id"))
                if not bound or bound["source_id"] != approved["source_id"] or int(bound["version"]) >= int(approved["version"]):
                    continue
                await refresh_pin(workspace_id, pin["pin_id"])
                refreshed.append(pin["pin_id"])
            except Exception:  # noqa: BLE001
                logger.exception("Automatic refresh of pin %s failed", pin["pin_id"])
        return refreshed

    ctx.auto_refresh_pins = _auto_refresh_pins
    return router
