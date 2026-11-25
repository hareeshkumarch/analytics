import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import (
    ApproveSemanticsRequest,
    ConnectorExtractRequest,
    ConnectorProbeRequest,
)
from app.application.ports.connectors import ExtractionRequest
from app.application.services.ingestion import table_artifact_id, table_key
from app.application.services.profiling import propose_metrics_from_schema
from app.bootstrap.container import AppContainer
from app.domain.errors import (
    DomainError,
    LimitExceededError,
    ValidationError,
)
from app.domain.limits import (
    DEFAULT_DATA_LIMITS,
)
from app.domain.models import ConnectorFamily, MetricDefinition

from app.api.support import _materialized_connector_config, _sample_files, _redacted_source_config, _reimport_state, _call_connector
from app.api.context import ApiContext

logger = logging.getLogger(__name__)


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    _db_unavailable = ctx.db_unavailable
    _upload_bytes = ctx.upload_bytes

    @router.post("/api/v1/workspaces/{workspace_id}/uploads", status_code=201)
    async def create_upload(workspace_id: str, request: Request) -> dict[str, Any]:
        limit = DEFAULT_DATA_LIMITS.max_uploaded_file_bytes
        too_large = LimitExceededError(f"Uploaded file exceeds the {limit:,} bytes limit")
        if int(request.headers.get("content-length") or 0) > limit:
            raise too_large
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > limit:
                raise too_large
        if not body:
            raise ValidationError("The uploaded file is empty")
        upload_id = await asyncio.to_thread(app.state.container.uploads.save, workspace_id, bytes(body))
        return {"upload_id": upload_id, "byte_size": len(body)}

    @router.post("/api/v1/workspaces/{workspace_id}/connectors/probe")
    async def probe_connector(workspace_id: str, payload: ConnectorProbeRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            upload = await _upload_bytes(workspace_id, payload.config)
            with _materialized_connector_config(payload.family, payload.config, upload) as cfg:
                res = await _call_connector(
                    c.connectors.get_discovery(payload.family).probe, cfg, payload.credentials
                )
        except DomainError as exc:
            return {
                "ok": False,
                "family": payload.family.value,
                "message": str(exc),
                "remediation": exc.remediation,
                "capabilities": {"transactional_snapshot": False, "resumable_cursor": False},
                "details": {},
            }
        return {
            "ok": res.ok,
            "family": res.family.value,
            "message": res.message,
            "capabilities": {
                "transactional_snapshot": res.capabilities.transactional_snapshot,
                "resumable_cursor": res.capabilities.resumable_cursor,
            },
            "details": res.details,
        }

    @router.post("/api/v1/workspaces/{workspace_id}/connectors/discover")
    async def discover_connector(workspace_id: str, payload: ConnectorProbeRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        upload = await _upload_bytes(workspace_id, payload.config)
        with _materialized_connector_config(payload.family, payload.config, upload) as cfg:
            tables = await _call_connector(
                c.connectors.get_discovery(payload.family).discover, cfg, payload.credentials
            )
        return {
            "tables": [
                {
                    "name": t.name,
                    "estimated_rows": t.estimated_rows,
                    "columns": [
                        {"name": col.name, "arrow_type": col.arrow_type, "is_leading_zero_id": col.is_leading_zero_id}
                        for col in t.columns
                    ],
                }
                for t in tables
            ]
        }

    @router.post("/api/v1/workspaces/{workspace_id}/connectors/extract", status_code=201)
    async def extract_connector(workspace_id: str, payload: ConnectorExtractRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        job_id = f"job_{uuid.uuid4().hex[:10]}"
        workspace_namespace = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:12]
        source_prefix = f"src_{workspace_namespace}_"
        source_config = _redacted_source_config(dict(payload.config))
        requested_source_id = payload.source_id or "src_" + hashlib.sha256(
            json.dumps([payload.family.value, source_config], sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24]
        source_id = (
            requested_source_id
            if requested_source_id.startswith(source_prefix)
            else f"{source_prefix}{requested_source_id.removeprefix('src_')[:42]}"
        )
        try:
            version = app.state.repo_snapshots.reserve_snapshot_version(
                workspace_id,
                source_id,
                payload.family.value,
                payload.selected_tables[0] if payload.selected_tables else "upload",
                source_config,
            )
        except SQLAlchemyError as exc:
            logger.exception("Failed to reserve a snapshot version")
            raise _db_unavailable("start the import") from exc
        req = ExtractionRequest(
            workspace_id=workspace_id,
            source_id=source_id,
            job_id=job_id,
            selected_tables=tuple(payload.selected_tables),
            selected_columns={k: tuple(v) for k, v in payload.selected_columns.items()},
            limits=DEFAULT_DATA_LIMITS,
            version=version,
        )
        upload = await _upload_bytes(workspace_id, payload.config)
        with _materialized_connector_config(payload.family, payload.config, upload) as cfg:
            batches, manifest = await _call_connector(
                c.connectors.get_extractor(payload.family).extract,
                cfg,
                payload.credentials,
                req,
                lambda: False,
            )
            batches = list(batches)
        content_hash = hashlib.sha256()
        for batch in sorted(batches, key=lambda b: b.table_name):
            content_hash.update(batch.table_name.encode("utf-8"))
            content_hash.update(batch.arrow_ipc_bytes)
        await asyncio.to_thread(app.state.repo_quota.reserve_workspace_bytes, workspace_id, manifest.total_normalized_bytes)
        try:
            try:
                for batch in batches:
                    staged = c.artifacts.stage_bytes(
                        job_id, f"{table_key(batch.table_name)}.arrow", batch.arrow_ipc_bytes, "application/vnd.apache.arrow.stream"
                    )
                    c.artifacts.publish_staged(job_id, staged, table_artifact_id(manifest.artifact_id, batch.table_name))
            finally:
                c.artifacts.cleanup_job_staging(job_id)

            proposed_metrics: list[MetricDefinition] = []
            for tbl in manifest.tables:
                proposed_metrics.extend(propose_metrics_from_schema(tbl))

            snap_dict = {
                "snapshot_id": manifest.snapshot_id,
                "workspace_id": workspace_id,
                "source_id": source_id,
                "version": manifest.version,
                "total_rows": manifest.total_rows,
                "total_normalized_bytes": manifest.total_normalized_bytes,
                "is_sampled": manifest.is_sampled,
                "consistency_level": manifest.consistency_level.value,
                "sha256": content_hash.hexdigest(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_family": payload.family.value,
                "schema_fingerprint": manifest.schema_fingerprint,
                "artifact_id": manifest.artifact_id,
                "table_artifacts": {
                    b.table_name: table_artifact_id(manifest.artifact_id, b.table_name) for b in batches
                },
                "tables": [t.table_name for t in manifest.tables],
                "table_details": [
                    {
                        "name": t.table_name,
                        "row_count": t.row_count,
                        "normalized_bytes": t.normalized_bytes,
                        "columns": [
                            {
                                "name": col.name,
                                "type": col.arrow_type,
                                "nullable": col.nullable,
                                "leading_zero_id": col.is_leading_zero_id,
                                "currency": col.currency_code,
                            }
                            for col in t.columns
                        ],
                    }
                    for t in manifest.tables
                ],
                "reimport": _reimport_state(payload),
                "diagnostics": {
                    "extraction_started_at": manifest.extraction_started_at.isoformat(),
                    "extraction_completed_at": manifest.extraction_completed_at.isoformat(),
                    "is_complete": manifest.is_complete,
                    "is_sampled": manifest.is_sampled,
                    "warnings": [],
                    "source_summary": dict(manifest.source_config_summary),
                },
            }
            sem_id = f"sem_{uuid.uuid4().hex[:10]}"
            sem_dict = {
                "semantic_version_id": sem_id,
                "snapshot_id": manifest.snapshot_id,
                "version": 1,
                "approved": False,
                "metrics": [
                    {
                        "metric_id": m.metric_id,
                        "display_name": m.display_name,
                        "expression": m.expression,
                        "grain": m.grain,
                        "unit": m.unit,
                        "currency": m.currency,
                    }
                    for m in proposed_metrics
                ],
            }
            try:
                app.state.repo_snapshots.save_snapshot(
                    workspace_id=workspace_id,
                    source_id=source_id,
                    snapshot_id=manifest.snapshot_id,
                    version=manifest.version,
                    schema_fingerprint=manifest.schema_fingerprint,
                    manifest_json=snap_dict,
                    artifact_id=manifest.artifact_id,
                    sha256=content_hash.hexdigest(),
                    source_config=source_config,
                    semantics_json=sem_dict,
                )
            except SQLAlchemyError as exc:
                logger.exception("Failed to persist snapshot to DB")
                raise _db_unavailable("save the imported dataset") from exc
        except BaseException:
            for batch in batches:
                c.artifacts.delete(table_artifact_id(manifest.artifact_id, batch.table_name))
            try:
                await asyncio.to_thread(
                    app.state.repo_quota.release_workspace_bytes, workspace_id, manifest.total_normalized_bytes
                )
            except SQLAlchemyError:
                logger.exception("Could not release %s reserved bytes for %s", manifest.total_normalized_bytes, workspace_id)
            raise
        app.state.snapshots[manifest.snapshot_id] = snap_dict
        app.state.semantics[manifest.snapshot_id] = sem_dict
        if upload is not None:
            c.uploads.delete(workspace_id, str(payload.config["upload_id"]))

        return {
            "snapshot": snap_dict,
            "semantics": sem_dict,
        }

    @router.post("/api/v1/workspaces/{workspace_id}/samples/{sample_name}/import", status_code=201)
    async def import_sample(workspace_id: str, sample_name: str) -> dict[str, Any]:
        tables = [name for name, _ in await asyncio.to_thread(_sample_files, sample_name)]
        imported = await extract_connector(
            workspace_id,
            ConnectorExtractRequest(
                family=ConnectorFamily.UPLOADED_FILE,
                config={"format": "csv", "sample": sample_name},
                selected_tables=tables,
            ),
        )
        snapshot_id = imported["snapshot"]["snapshot_id"]
        semantics = await ctx.approve_semantics(
            workspace_id, snapshot_id, ApproveSemanticsRequest(expected_version=int(imported["semantics"]["version"]))
        )
        return {"snapshot": {**imported["snapshot"], "approved": True}, "semantics": semantics}

    return router
