import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import (
    CreatePinRequest,
    RefreshPinsRequest,
    UpdatePinRequest,
    CreateRunRequest,
)
from app.domain.models import RunMode, RunStatus

from app.api.context import ApiContext
from app.api.responses import PinListOut, PinOut, PinRefreshBatchOut
from app.api.support import RUN_LIST_ANSWER_KEYS
from app.application.services.verification import headline_value

logger = logging.getLogger(__name__)

SCHEDULE_INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}

ACTIVE_STATUSES = {RunStatus.QUEUED.value, RunStatus.RUNNING.value, RunStatus.AWAITING_INPUT.value}


def newer_approved_version(snapshots: list[dict[str, Any]], snapshot: dict[str, Any] | None) -> int | None:
    """Highest approved version of the same source that is newer than `snapshot`, if there is one."""
    if not snapshot:
        return None
    versions = [
        int(s["version"])
        for s in snapshots
        if s["source_id"] == snapshot["source_id"] and s.get("approved") and int(s["version"]) > int(snapshot["version"])
    ]
    return max(versions, default=None)


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    _db_unavailable = ctx.db_unavailable
    _snapshot_record = ctx.snapshot_record
    _newest_approved_in_source = ctx.newest_approved_in_source

    def _alert_state(pin: dict[str, Any], shown: dict[str, Any] | None) -> dict[str, Any]:
        """The answer's headline figure, and whether it crosses the pin's threshold."""
        figure = headline_value(str(((shown or {}).get("answer") or {}).get("summary_markdown") or ""))
        alert = pin.get("alert") if isinstance(pin.get("alert"), dict) else None
        triggered: bool | None = None
        if alert and figure is not None:
            threshold = Decimal(str(alert["value"]))
            triggered = figure > threshold if alert["direction"] == "above" else figure < threshold
        return {"alert": alert, "headline_value": float(figure) if figure is not None else None, "alert_triggered": triggered}

    def _describe_pins(workspace_id: str, pins: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add what a dashboard card shows: the answer on display, its dataset, and whether newer data exists."""
        run_ids = {p["run_id"] for p in pins} | {p["latest_run_id"] for p in pins if p.get("latest_run_id")}
        runs = app.state.repo_runs.get_runs_by_ids(sorted(run_ids)) if run_ids else {}
        snapshots = app.state.repo_snapshots.list_snapshots(workspace_id) if pins else []
        by_id = {s["snapshot_id"]: s for s in snapshots}
        described = []
        for pin in pins:
            latest = runs.get(pin.get("latest_run_id") or "")
            # The newest finished re-run is what the card shows; until one finishes, the pinned answer stays.
            shown = latest if latest and latest["status"] == RunStatus.COMPLETED.value else runs.get(pin["run_id"])
            snapshot = by_id.get((shown or {}).get("snapshot_id") or "")
            described.append(
                {
                    **pin,
                    "shown_run": (
                        {**shown, "answer": {k: shown["answer"][k] for k in RUN_LIST_ANSWER_KEYS if k in shown["answer"]}}
                        if shown
                        else None
                    ),
                    "latest_run_status": latest["status"] if latest else None,
                    "snapshot": snapshot,
                    "newer_version": newer_approved_version(snapshots, snapshot),
                    **_alert_state(pin, shown),
                }
            )
        return described

    @router.get("/api/v1/workspaces/{workspace_id}/pins", response_model=PinListOut, response_model_exclude_unset=True)
    def list_pins(workspace_id: str) -> dict[str, Any]:
        try:
            return {"pins": _describe_pins(workspace_id, app.state.repo_pins.list_pins(workspace_id))}
        except SQLAlchemyError as exc:
            logger.exception("Failed to query pins from DB")
            raise _db_unavailable("load pinned answers") from exc

    @router.post("/api/v1/workspaces/{workspace_id}/pins", status_code=201, response_model=PinOut, response_model_exclude_unset=True)
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

    @router.patch("/api/v1/workspaces/{workspace_id}/pins/{pin_id}", response_model=PinOut, response_model_exclude_unset=True)
    def update_pin(workspace_id: str, pin_id: str, payload: UpdatePinRequest) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if payload.auto_refresh is not None:
            changes["auto_refresh"] = payload.auto_refresh
        if payload.schedule is not None:
            changes["schedule"] = payload.schedule
            # The first scheduled run is one full interval away; "off" clears the slot.
            changes["next_run_at"] = (
                (datetime.now(timezone.utc) + SCHEDULE_INTERVALS[payload.schedule]).isoformat() if payload.schedule in SCHEDULE_INTERVALS else None
            )
        if payload.alert is not None:
            changes["alert"] = {"direction": payload.alert.direction, "value": payload.alert.value}
        elif payload.clear_alert:
            changes["alert"] = None
        try:
            if not app.state.repo_pins.update_pin_state(workspace_id, pin_id, changes):
                raise HTTPException(status_code=404, detail="Pin not found")
            pins = [p for p in app.state.repo_pins.list_pins(workspace_id) if p["pin_id"] == pin_id]
            return _describe_pins(workspace_id, pins)[0]
        except SQLAlchemyError as exc:
            logger.exception("Failed to update pin")
            raise _db_unavailable("update the pin") from exc

    async def _start_refresh(workspace_id: str, pin: dict[str, Any]) -> dict[str, Any]:
        pin_id = pin["pin_id"]
        latest_id = pin.get("latest_run_id")
        if latest_id:
            latest = await asyncio.to_thread(app.state.repo_runs.get_run, latest_id)
            if latest and latest.get("status") in ACTIVE_STATUSES:
                # A re-run is already in flight: hand that one back instead of paying for a second.
                return {"pin_id": pin_id, "refresh_run_id": latest_id, "status": latest["status"], "already_running": True}
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
        return {"pin_id": pin_id, "refresh_run_id": run_data["run_id"], "status": run_data["status"], "already_running": False}

    @router.post("/api/v1/workspaces/{workspace_id}/pins/refresh", status_code=202, response_model=PinRefreshBatchOut)
    async def refresh_pins(workspace_id: str, payload: RefreshPinsRequest) -> dict[str, Any]:
        try:
            pins = await asyncio.to_thread(app.state.repo_pins.list_pins, workspace_id)
            described = await asyncio.to_thread(_describe_pins, workspace_id, pins)
        except SQLAlchemyError as exc:
            logger.exception("Failed to load pins for a batch refresh")
            raise _db_unavailable("re-run pinned answers") from exc
        wanted = set(payload.pin_ids) if payload.pin_ids is not None else None
        targets = [p for p in described if (p["pin_id"] in wanted if wanted is not None else p["newer_version"] is not None)]
        refreshed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for pin in targets:
            try:
                refreshed.append(await _start_refresh(workspace_id, pin))
            except HTTPException as exc:
                # One pin over a limit must not stop the rest.
                failed.append({"pin_id": pin["pin_id"], "detail": str(exc.detail)})
        return {"refreshed": refreshed, "failed": failed}

    @router.post("/api/v1/workspaces/{workspace_id}/pins/{pin_id}/refresh", status_code=202)
    async def refresh_pin(workspace_id: str, pin_id: str) -> dict[str, Any]:
        pins = await asyncio.to_thread(app.state.repo_pins.list_pins, workspace_id)
        pin = next((p for p in pins if p["pin_id"] == pin_id), None)
        if not pin:
            raise HTTPException(status_code=404, detail="Pin not found")
        return await _start_refresh(workspace_id, pin)

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

    async def _run_due_pins() -> list[str]:
        """Re-run every scheduled pin whose time has come. Safe to call from several processes at once."""
        now = datetime.now(timezone.utc)
        started: list[str] = []
        for due in await asyncio.to_thread(app.state.repo_pins.due_pins, now):
            following = (now + SCHEDULE_INTERVALS[due["schedule"]]).isoformat()
            # Claim the slot first: only the process that moves next_run_at forward starts the run.
            if not await asyncio.to_thread(app.state.repo_pins.claim_due_pin, due["pin_id"], due["next_run_at"], following):
                continue
            try:
                await refresh_pin(due["workspace_id"], due["pin_id"])
                started.append(due["pin_id"])
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled re-run of pin %s failed", due["pin_id"])
        return started

    ctx.run_due_pins = _run_due_pins
    ctx.auto_refresh_pins = _auto_refresh_pins
    return router
