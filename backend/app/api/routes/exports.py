import asyncio
from functools import partial
import hashlib
import os
from datetime import datetime, timezone
from decimal import Decimal
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from app.api.schemas import (
    CreateExportRequest,
)
from app.application.services.charts import chart_from_dict
from app.application.services.text_utils import EVIDENCE_ID_RE
from app.bootstrap.container import AppContainer
from app.domain.models import Answer

from app.api.support import EXPORT_ARTIFACT_PREFIX
from app.api.context import ApiContext

logger = logging.getLogger(__name__)


def _export_retention_seconds() -> float:
    return float(os.environ.get("EXPORT_RETENTION_DAYS", "7")) * 86400


def _export_prefix(workspace_id: str) -> str:
    return f"{EXPORT_ARTIFACT_PREFIX}{hashlib.sha256(workspace_id.encode()).hexdigest()[:8]}_"


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app

    @router.post("/api/v1/workspaces/{workspace_id}/exports", status_code=201)
    async def create_export(workspace_id: str, payload: CreateExportRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        fmt = payload.format.lower()
        run_id = payload.run_id
        run_rec = await asyncio.to_thread(app.state.repo_runs.get_run, run_id) if run_id else None
        if run_rec is not None and run_rec.get("workspace_id") != workspace_id:
            raise HTTPException(status_code=404, detail="Run not found in this workspace")
        ans = app.state.answers.get(run_id) if run_rec is not None else None
        if ans is None:
            if run_rec and (run_rec.get("answer") or {}).get("summary_markdown"):
                ans_data = run_rec["answer"]
                from app.domain.models import Claim, CriterionResult
                ans = Answer(
                    answer_id=ans_data.get("answer_id") or f"ans_{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    summary_markdown=ans_data.get("summary_markdown", ""),
                    claims=tuple(
                        Claim(
                            claim_id=cl["claim_id"],
                            label=cl["label"],
                            statement=cl["statement"],
                            value=Decimal(str(cl["value"])) if cl.get("value") is not None else None,
                            unit=cl.get("unit"),
                            currency=cl.get("currency"),
                            evidence_ids=tuple(cl.get("evidence_ids", ())),
                        )
                        for cl in ans_data.get("claims", [])
                    ),
                    charts=tuple(chart_from_dict(ch) for ch in ans_data.get("charts") or ()),
                    limitations=tuple(ans_data.get("limitations", ())),
                    snapshot_id=str(ans_data.get("snapshot_id") or ""),
                    semantic_version_id=str(ans_data.get("semantic_version_id") or ""),
                    created_at=datetime.now(timezone.utc),
                    outcome=str(ans_data.get("outcome") or "success"),
                    criteria=tuple(
                        CriterionResult(
                            criterion=str(cr.get("criterion", "")),
                            status=str(cr.get("status", "")),
                            evidence_ids=tuple(cr.get("evidence_ids", ())),
                            detail=str(cr.get("detail", "")),
                        )
                        for cr in ans_data.get("criteria") or ()
                    ),
                )
        if ans is None:
            if not payload.summary_markdown.strip():
                raise HTTPException(status_code=404, detail="No answer found for this run and no content supplied")
            ans = Answer(
                answer_id=f"ans_{uuid.uuid4().hex[:8]}",
                run_id=run_id,
                summary_markdown=payload.summary_markdown,
                claims=(),
                charts=(),
                limitations=(),
                snapshot_id=payload.snapshot_id,
                semantic_version_id=payload.semantic_version_id,
                created_at=datetime.now(timezone.utc),
            )
        cited = {e for claim in ans.claims for e in claim.evidence_ids} | {ch.evidence_id for ch in ans.charts}
        cited |= set(EVIDENCE_ID_RE.findall(ans.summary_markdown))
        sources: list[dict[str, Any]] = []
        if run_rec is not None and cited:
            stored = await asyncio.to_thread(app.state.repo_evidence.list_run, run_id)
            sources = [e for e in stored if e.get("evidence_id") in cited]
        if fmt == "csv":
            raw = await asyncio.to_thread(
                c.export_service.export_csv,
                ("claim_id", "statement"),
                tuple((cl.claim_id, cl.statement) for cl in ans.claims),
            )
            ctype = "text/csv"
        elif fmt == "pdf":
            raw = await asyncio.to_thread(partial(c.export_service.export_pdf, ans, evidence=sources))
            ctype = "application/pdf"
        elif fmt == "docx":
            raw = await asyncio.to_thread(partial(c.export_service.export_docx, ans, evidence=sources))
            ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            raw = await asyncio.to_thread(partial(c.export_service.export_html, ans, evidence=sources))
            ctype = "text/html"

        await asyncio.to_thread(c.artifacts.delete_older_than, EXPORT_ARTIFACT_PREFIX, _export_retention_seconds())
        art_id = f"{_export_prefix(workspace_id)}{uuid.uuid4().hex[:10]}"
        staged = c.artifacts.stage_bytes(art_id, f"export.{fmt}", raw, ctype)
        stored = c.artifacts.publish_staged(art_id, staged, art_id)
        c.artifacts.cleanup_job_staging(art_id)
        return {
            "artifact_id": stored.artifact_id,
            "sha256": stored.sha256,
            "byte_size": stored.byte_size,
            "format": fmt,
            "download_url": f"/api/v1/workspaces/{workspace_id}/exports/{stored.artifact_id}",
        }

    @router.get("/api/v1/workspaces/{workspace_id}/exports/{artifact_id}")
    def download_export(workspace_id: str, artifact_id: str) -> Response:
        c: AppContainer = app.state.container
        if not artifact_id.startswith(_export_prefix(workspace_id)):
            raise HTTPException(status_code=404, detail="Export not found")
        data = c.artifacts.read_bytes(artifact_id)
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{artifact_id}"', "X-Content-Type-Options": "nosniff"},
        )

    return router
