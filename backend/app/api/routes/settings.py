import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.adapters.persistence.crypto import CredentialCipher
from app.api.schemas import (
    SaveProviderKeyRequest,
    UpdateRoutingRequest,
)
from app.application.ports.providers import ModelRequest
from app.bootstrap.container import AppContainer
from app.domain.errors import (
    VersionConflictError,
)
from app.domain.providers import AgentRole, ProviderId, RoutingPolicy

from app.api.context import ApiContext
from app.api.responses import ProvidersOut, RoutingPolicyOut

PROVIDER_PROBE_COOLDOWN_SECONDS = 10.0

logger = logging.getLogger(__name__)


def register(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    _db_unavailable = ctx.db_unavailable
    _policy_for_workspace = ctx.policy_for_workspace

    @router.get("/api/v1/workspaces/{workspace_id}/providers", response_model=ProvidersOut, response_model_exclude_unset=True)
    def list_providers(workspace_id: str) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            db_keys = {cr["provider"] for cr in app.state.repo_creds.list_active_credentials(workspace_id)}
        except SQLAlchemyError as exc:
            logger.exception("Failed to load provider keys from DB")
            raise _db_unavailable("load saved provider keys") from exc
        def key_status(provider: ProviderId) -> str:
            if c.routing_service._api_keys.get(provider) or f"{workspace_id}:{provider.value}" in app.state.encrypted_keys:
                return "ready"
            if f"{workspace_id}:{provider.value}" in app.state.undecryptable_keys:
                return "needs_key"
            return "ready" if provider.value in db_keys else "missing"

        statuses = {p.provider_id: key_status(p.provider_id) for p in c.provider_profiles.values()}
        return {
            "workspace_id": workspace_id,
            "providers": [
                {
                    "provider_id": p.provider_id.value,
                    "api_profile": p.api_profile.value,
                    "endpoint_profile_id": p.endpoint_profile_id,
                    "configured": statuses[p.provider_id] == "ready",
                    "key_status": statuses[p.provider_id],
                }
                for p in c.provider_profiles.values()
            ],
            "models": [
                {
                    "model_id": m.model_id,
                    "provider_id": m.provider_id.value,
                    "roles": [r.value for r in m.roles],
                    "enabled": m.enabled,
                    "verification_date": m.verification_date,
                }
                for m in c.model_profiles.values()
            ],
        }

    @router.post("/api/v1/workspaces/{workspace_id}/providers/{provider_id}/test")
    async def test_provider_connection(workspace_id: str, provider_id: str) -> dict[str, Any]:
        c: AppContainer = app.state.container
        try:
            pid = ProviderId(provider_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}") from exc
        probed = app.state.provider_probes
        if time.monotonic() - probed.get((workspace_id, pid), -PROVIDER_PROBE_COOLDOWN_SECONDS) < PROVIDER_PROBE_COOLDOWN_SECONDS:
            raise HTTPException(status_code=429, detail="This provider was tested a moment ago. Try again in a few seconds.")
        api_key = c.routing_service._api_keys.get(pid, "")
        if not api_key:
            return {
                "provider_id": pid.value,
                "status": "setup_required",
                "message": "No API key configured for this provider",
            }
        model = next((m for m in c.model_profiles.values() if m.provider_id == pid and m.enabled), None)
        if not model:
            return {"provider_id": pid.value, "status": "no_enabled_model"}

        t0 = time.monotonic()
        probed[(workspace_id, pid)] = t0
        req = ModelRequest(
            request_id=f"probe_{uuid.uuid4().hex[:8]}",
            run_id="synthetic_probe",
            role=AgentRole.DATA_SPECIALIST,
            model_id=model.model_id,
            messages=({"role": "user", "content": "Reply with OK."},),
            max_output_tokens=16,
            timeout_seconds=10,
        )
        resp = await asyncio.to_thread(
            c.providers.get(pid).invoke, req, c.provider_profiles[pid], model, api_key
        )
        return {
            "provider_id": pid.value,
            "model_id": model.model_id,
            "status": "ok",
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "usage_complete": resp.input_tokens > 0 or resp.output_tokens > 0,
        }

    @router.get("/api/v1/workspaces/{workspace_id}/routing", response_model=RoutingPolicyOut, response_model_exclude_unset=True)
    def get_routing_policy(workspace_id: str) -> dict[str, Any]:
        current = _policy_for_workspace(workspace_id)
        return {
            "workspace_id": workspace_id,
            "policy_version": current.policy_version,
            "preset": current.preset.value,
            "fallback_enabled": current.fallback_enabled,
            "role_routes": {role.value: list(models) for role, models in current.role_routes.items()},
            "hard_cost_ceiling_usd": (
                str(current.hard_cost_ceiling_usd) if current.hard_cost_ceiling_usd is not None else None
            ),
        }

    @router.put("/api/v1/workspaces/{workspace_id}/routing", response_model=RoutingPolicyOut, response_model_exclude_unset=True)
    def update_routing_policy(workspace_id: str, payload: UpdateRoutingRequest) -> dict[str, Any]:
        c: AppContainer = app.state.container
        current = _policy_for_workspace(workspace_id)

        if payload.expected_version != current.policy_version:
            raise HTTPException(status_code=409, detail="Routing policy version conflict")

        role_routes = dict(current.role_routes)
        if payload.role_routes is not None:
            for role_name, model_ids in payload.role_routes.items():
                try:
                    role = AgentRole(role_name)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=f"Unknown routing role: {role_name}") from exc
                normalized_ids = tuple(dict.fromkeys(model_id.strip() for model_id in model_ids if model_id.strip()))
                if not normalized_ids:
                    raise HTTPException(status_code=422, detail=f"At least one model is required for {role_name}")
                for model_id in normalized_ids:
                    model = c.model_profiles.get(model_id)
                    if model is None or not model.enabled or role not in model.roles:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Model {model_id!r} is not enabled for role {role_name}",
                        )
                role_routes[role] = normalized_ids

        hard_cost_ceiling = current.hard_cost_ceiling_usd
        if "hard_cost_ceiling_usd" in payload.model_fields_set:
            hard_cost_ceiling = payload.hard_cost_ceiling_usd
        preset = payload.preset or current.preset
        fallback_enabled = (
            payload.fallback_enabled if payload.fallback_enabled is not None else current.fallback_enabled
        )

        pol_dict = {
            "preset": preset.value,
            "fallback_enabled": fallback_enabled,
            "role_routes": {r.value: list(models) for r, models in role_routes.items()},
            "allowed_providers": [p.value for p in current.allowed_providers],
            "hard_cost_ceiling_usd": str(hard_cost_ceiling) if hard_cost_ceiling is not None else None,
        }
        try:
            res = app.state.repo_routing.save_policy(workspace_id, pol_dict, expected_version=payload.expected_version)
        except VersionConflictError as exc:
            raise HTTPException(status_code=409, detail="Routing policy version conflict") from exc
        except SQLAlchemyError as exc:
            logger.exception("Failed to save routing policy to DB")
            raise _db_unavailable("save the routing policy") from exc
        new_version = res["policy_version"]

        new_policy = RoutingPolicy(
            policy_version=new_version,
            preset=preset,
            fallback_enabled=fallback_enabled,
            role_routes=role_routes,
            allowed_providers=current.allowed_providers,
            hard_cost_ceiling_usd=hard_cost_ceiling,
        )
        app.state.routing_policies[workspace_id] = new_policy
        return {
            "workspace_id": workspace_id,
            "policy_version": new_policy.policy_version,
            "preset": new_policy.preset.value,
            "fallback_enabled": new_policy.fallback_enabled,
            "role_routes": {role.value: list(models) for role, models in new_policy.role_routes.items()},
            "hard_cost_ceiling_usd": (
                str(new_policy.hard_cost_ceiling_usd)
                if new_policy.hard_cost_ceiling_usd is not None
                else None
            ),
        }

    @router.put("/api/v1/workspaces/{workspace_id}/settings/provider-key")
    def save_provider_key(workspace_id: str, payload: SaveProviderKeyRequest) -> dict[str, str]:
        cipher = CredentialCipher()
        encrypted = cipher.encrypt(payload.api_key)
        try:
            app.state.repo_creds.save_credential(workspace_id, payload.provider_id.value, encrypted)
        except SQLAlchemyError as exc:
            logger.exception("Failed to persist provider key to DB")
            raise _db_unavailable("save the provider key") from exc
        app.state.encrypted_keys[f"{workspace_id}:{payload.provider_id.value}"] = encrypted
        app.state.undecryptable_keys.discard(f"{workspace_id}:{payload.provider_id.value}")
        c: AppContainer = app.state.container
        c.routing_service._api_keys[payload.provider_id] = payload.api_key
        return {"provider_id": payload.provider_id.value, "status": "configured"}

    return router
