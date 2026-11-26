from decimal import Decimal
import threading
import time
from typing import Any, Callable, Generator, cast
import uuid

from opentelemetry import trace

from app.adapters.providers.registry import ProviderRegistry
from app.application.ports.clock import Clock
from app.adapters.providers.http_utils import STREAM_UNSUPPORTED
from app.application.ports.providers import BudgetLedger, ModelRequest, ModelResponse
from app.application.services.budget_service import DEFAULT_MAX_TOKENS
from app.application.services.text_utils import clean_single_line
from app.domain.errors import CircuitOpenError, ProviderError
from app.domain.limits import DEFAULT_GLOBAL_LIMITS
from app.domain.providers import (
    AgentRole,
    CircuitBreakerState,
    CircuitStatus,
    ModelProfile,
    ProviderId,
    ProviderProfile,
    RoutingPolicy,
    RoutingPreset,
)

_tracer = trace.get_tracer("ledgerline.routing")


def _drain(stream: Generator[str, None, ModelResponse], on_piece: Callable[[str], None]) -> ModelResponse:
    while True:
        try:
            piece = next(stream)
        except StopIteration as done:
            return cast(ModelResponse, done.value)
        on_piece(piece)


def routing_policy_from_saved(saved: dict[str, Any] | None, default: RoutingPolicy) -> RoutingPolicy:
    if not saved:
        return default
    raw_routes = saved.get("role_routes") or {}
    routes = dict(default.role_routes)
    if isinstance(raw_routes, dict):
        routes.update(
            {
                AgentRole(role): tuple(models)
                for role, models in raw_routes.items()
                if role in AgentRole._value2member_map_
            }
        )
    raw_providers = saved.get("allowed_providers")
    providers = (
        tuple(ProviderId(p) for p in raw_providers if p in ProviderId._value2member_map_)
        if isinstance(raw_providers, list)
        else default.allowed_providers
    )
    ceiling = saved.get("hard_cost_ceiling_usd")
    return RoutingPolicy(
        policy_version=int(saved.get("policy_version", default.policy_version)),
        preset=RoutingPreset(saved.get("preset", default.preset.value)),
        fallback_enabled=bool(saved.get("fallback_enabled", default.fallback_enabled)),
        role_routes=routes,
        allowed_providers=providers,
        hard_cost_ceiling_usd=Decimal(str(ceiling)) if ceiling is not None else None,
    )


LATENCY_EWMA_WEIGHT = 0.3

class CredentialSemaphorePool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}

    def acquire(self, key: str, max_concurrent: int, timeout: float = 2.0) -> bool:
        with self._lock:
            sem = self._semaphores.get(key)
            if sem is None:
                sem = threading.BoundedSemaphore(max(1, max_concurrent))
                self._semaphores[key] = sem
        return sem.acquire(timeout=timeout)

    def release(self, key: str) -> None:
        with self._lock:
            sem = self._semaphores.get(key)
        if sem is not None:
            try:
                sem.release()
            except ValueError:
                pass


class CircuitBreakerManager:
    def __init__(
        self,
        clock: Clock,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        open_seconds: float = 30.0,
    ) -> None:
        self._clock = clock
        self._threshold = failure_threshold
        self._window = window_seconds
        self._open_seconds = open_seconds
        self._states: dict[str, CircuitBreakerState] = {}
        self._lock = threading.RLock()

    def get_state(self, key: str) -> CircuitBreakerState:
        with self._lock:
            now = self._clock.monotonic()
            st = self._states.get(key, CircuitBreakerState(key=key))
            if st.status == CircuitStatus.OPEN and st.opened_at is not None:
                if now - st.opened_at >= self._open_seconds:
                    st = CircuitBreakerState(
                        key=key,
                        status=CircuitStatus.HALF_OPEN,
                        failure_timestamps=st.failure_timestamps,
                        opened_at=st.opened_at,
                        half_open_probe_in_flight=False,
                    )
                    self._states[key] = st
            return st

    def try_acquire(self, key: str) -> bool:
        with self._lock:
            st = self.get_state(key)
            if st.status == CircuitStatus.CLOSED:
                return True
            if st.status == CircuitStatus.HALF_OPEN and not st.half_open_probe_in_flight:
                self._states[key] = CircuitBreakerState(
                    key=key,
                    status=CircuitStatus.HALF_OPEN,
                    failure_timestamps=st.failure_timestamps,
                    opened_at=st.opened_at,
                    half_open_probe_in_flight=True,
                )
                return True
            return False

    def release_probe(self, key: str) -> None:
        with self._lock:
            st = self._states.get(key)
            if st is not None and st.status == CircuitStatus.HALF_OPEN and st.half_open_probe_in_flight:
                self._states[key] = CircuitBreakerState(
                    key=key,
                    status=CircuitStatus.HALF_OPEN,
                    failure_timestamps=st.failure_timestamps,
                    opened_at=st.opened_at,
                    half_open_probe_in_flight=False,
                )

    def record_success(self, key: str) -> None:
        with self._lock:
            self._states[key] = CircuitBreakerState(key=key, status=CircuitStatus.CLOSED)

    def record_transient_failure(self, key: str) -> CircuitBreakerState:
        with self._lock:
            now = self._clock.monotonic()
            st = self.get_state(key)
            recent = tuple(ts for ts in st.failure_timestamps if now - ts <= self._window) + (now,)
            if st.status == CircuitStatus.HALF_OPEN or len(recent) >= self._threshold:
                new_st = CircuitBreakerState(
                    key=key,
                    status=CircuitStatus.OPEN,
                    failure_timestamps=recent,
                    opened_at=now,
                    half_open_probe_in_flight=False,
                )
            else:
                new_st = CircuitBreakerState(
                    key=key,
                    status=CircuitStatus.CLOSED,
                    failure_timestamps=recent,
                )
            self._states[key] = new_st
            return new_st


class RoutingService:
    def __init__(
        self,
        providers: ProviderRegistry,
        provider_profiles: dict[ProviderId, ProviderProfile],
        model_profiles: dict[str, ModelProfile],
        circuit_breakers: CircuitBreakerManager,
        budget_ledger: BudgetLedger,
        api_keys: dict[ProviderId, str],
        semaphores: CredentialSemaphorePool | None = None,
    ) -> None:
        self._providers = providers
        self._provider_profiles = provider_profiles
        self._models = model_profiles
        self._circuits = circuit_breakers
        self._ledger = budget_ledger
        self._api_keys = api_keys
        self._semaphores = semaphores or CredentialSemaphorePool()
        self._latency_ms: dict[str, float] = {}

    def record_latency(self, model_id: str, latency_ms: int) -> None:
        previous = self._latency_ms.get(model_id)
        self._latency_ms[model_id] = latency_ms if previous is None else previous * (1 - LATENCY_EWMA_WEIGHT) + latency_ms * LATENCY_EWMA_WEIGHT

    def set_budget_ledger(self, budget_ledger: BudgetLedger) -> None:
        self._ledger = budget_ledger

    def tokens_left(self, run_id: str) -> int:
        return DEFAULT_MAX_TOKENS - int(str(self._ledger.get_run_totals(run_id).get("total_tokens", 0)))

    def select_candidates(
        self,
        *,
        role: AgentRole,
        policy: RoutingPolicy,
        requires_structured: bool,
        requires_tools: bool,
        requires_streaming: bool,
        estimated_input_tokens: int,
        manual_model_id: str | None = None,
    ) -> list[ModelProfile]:
        ordered_ids = [manual_model_id] if manual_model_id else list(policy.role_routes.get(role, ()))
        eligible: list[ModelProfile] = []
        for mid in ordered_ids:
            model = self._models.get(mid)
            if not model or not model.enabled:
                continue
            if model.provider_id not in policy.allowed_providers:
                continue
            if not self._api_keys.get(model.provider_id):
                continue
            if role not in model.roles:
                continue
            if estimated_input_tokens + model.max_output_tokens > model.context_window:
                continue
            caps = model.capabilities
            if requires_structured and not caps.structured_output:
                continue
            if requires_tools and not caps.tool_calling:
                continue
            if requires_structured and requires_tools and not caps.structured_with_tools:
                continue
            if requires_streaming and not caps.streaming:
                continue
            if policy.hard_cost_ceiling_usd is not None and (
                model.input_price_per_1m is None or model.output_price_per_1m is None
            ):
                continue
            cb_key = f"{model.provider_id.value}:{model.model_id}"
            if self._circuits.get_state(cb_key).status == CircuitStatus.OPEN:
                continue
            eligible.append(model)

        if policy.preset == RoutingPreset.COST and not manual_model_id:
            eligible.sort(
                key=lambda m: (m.input_price_per_1m or Decimal("999999"))
                + (m.output_price_per_1m or Decimal("999999"))
            )
        elif policy.preset == RoutingPreset.LATENCY and not manual_model_id:
            eligible.sort(key=lambda m: self._latency_ms.get(m.model_id, float("inf")))
        elif policy.preset == RoutingPreset.QUALITY and not manual_model_id:
            eligible.sort(key=lambda m: (not m.capabilities.reasoning_tokens, -m.context_window))
        return eligible

    def execute_step(
        self,
        *,
        request: ModelRequest,
        policy: RoutingPolicy,
        estimated_input_tokens: int,
        manual_model_id: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_reset: Callable[[], None] | None = None,
    ) -> ModelResponse:
        partial = [False]

        def emit(piece: str) -> None:
            partial[0] = True
            if on_delta is not None:
                on_delta(piece)

        candidates = self.select_candidates(
            role=request.role,
            policy=policy,
            requires_structured=request.structured_schema is not None,
            requires_tools=bool(request.tools),
            requires_streaming=request.stream,
            estimated_input_tokens=estimated_input_tokens,
            manual_model_id=manual_model_id,
        )
        if not candidates:
            raise ProviderError(
                f"No eligible model candidate satisfies role={request.role.value} and hard capability/policy filters",
                code="no_eligible_route",
                retryable=False,
            )

        max_attempts = DEFAULT_GLOBAL_LIMITS.max_attempts_per_logical_step
        max_repairs = DEFAULT_GLOBAL_LIMITS.max_schema_repairs_per_step
        attempts_used = 0
        repairs_used = 0
        last_error: Exception | None = None

        for idx, model in enumerate(candidates):
            if attempts_used >= max_attempts:
                break
            if idx > 0 and not policy.fallback_enabled:
                break

            cb_key = f"{model.provider_id.value}:{model.model_id}"
            if not self._circuits.try_acquire(cb_key):
                continue

            prov_profile = self._provider_profiles[model.provider_id]
            sem_key = f"cred:{model.provider_id.value}"
            if not self._semaphores.acquire(sem_key, prov_profile.max_concurrent_requests):
                self._circuits.release_probe(cb_key)
                continue

            current_request = request
            while attempts_used < max_attempts:
                attempts_used += 1
                attempt_id = f"att_{uuid.uuid4().hex[:12]}"
                op_id = f"op_{request.request_id}"
                max_tokens = estimated_input_tokens + min(request.max_output_tokens, model.max_output_tokens)
                cost_unknown = model.input_price_per_1m is None or model.output_price_per_1m is None
                est_cost: Decimal | None = None
                if not cost_unknown and model.input_price_per_1m is not None and model.output_price_per_1m is not None:
                    est_cost = (
                        Decimal(estimated_input_tokens) * model.input_price_per_1m
                        + Decimal(min(request.max_output_tokens, model.max_output_tokens)) * model.output_price_per_1m
                    ) / Decimal("1000000")

                try:
                    reservation = self._ledger.reserve(
                        run_id=request.run_id,
                        operation_id=op_id,
                        attempt_id=attempt_id,
                        estimated_tokens=max_tokens,
                        estimated_cost_usd=est_cost,
                        cost_is_unknown=cost_unknown,
                        hard_cost_ceiling_usd=policy.hard_cost_ceiling_usd,
                        provider_id=model.provider_id.value,
                        model_id=model.model_id,
                        role=request.role.value,
                    )
                except BaseException:
                    self._semaphores.release(sem_key)
                    self._circuits.release_probe(cb_key)
                    raise

                gateway = self._providers.get(model.provider_id)
                api_key = self._api_keys[model.provider_id]

                if partial[0] and on_reset is not None:
                    on_reset()
                partial[0] = False
                started = time.monotonic()
                stream_fn = getattr(gateway, "stream", None)
                streams = (
                    on_delta is not None
                    and model.capabilities.streaming
                    and stream_fn is not None
                    and not current_request.tools
                    and current_request.structured_schema is None
                )
                try:
                    with _tracer.start_as_current_span(
                        "model.call",
                        attributes={
                            "llm.provider": model.provider_id.value,
                            "llm.model": model.model_id,
                            "llm.role": request.role.value,
                            "llm.streaming": bool(streams),
                            "run.id": request.run_id,
                        },
                    ) as span:
                        if streams and stream_fn is not None:
                            try:
                                resp = _drain(stream_fn(current_request, prov_profile, model, api_key), emit)
                            except ProviderError as stream_exc:
                                if stream_exc.code != STREAM_UNSUPPORTED or partial[0]:
                                    raise
                                resp = gateway.invoke(current_request, prov_profile, model, api_key)
                                emit(resp.text)
                        else:
                            resp = gateway.invoke(current_request, prov_profile, model, api_key)
                        if not resp.text.strip() and not resp.tool_calls and resp.structured_output is None and not resp.refusal_reason:
                            raise ProviderError(
                                f"{model.provider_id.value} returned an empty reply", code="provider_empty_response", retryable=True
                            )
                        span.set_attribute("llm.input_tokens", resp.input_tokens)
                        span.set_attribute("llm.output_tokens", resp.output_tokens + resp.reasoning_tokens)
                except ProviderError as exc:
                    last_error = exc
                    self._ledger.release_unresolved(reservation.reservation_id, str(exc))
                    if exc.code == "schema_mismatch" and repairs_used < max_repairs and attempts_used < max_attempts:
                        repairs_used += 1
                        clean_err = clean_single_line(exc, limit=200)
                        current_request = ModelRequest(
                            request_id=f"{request.request_id}_repair",
                            run_id=request.run_id,
                            role=request.role,
                            model_id=model.model_id,
                            messages=request.messages
                            + ({"role": "user", "content": f"Fix JSON schema error: '{clean_err}'. Output valid JSON conforming to the schema only."},),
                            tools=request.tools,
                            structured_schema=request.structured_schema,
                            stream=request.stream,
                            max_output_tokens=request.max_output_tokens,
                            temperature=0.0,
                            timeout_seconds=request.timeout_seconds,
                            tool_choice=request.tool_choice,
                        )
                        continue
                    if exc.retryable:
                        self._circuits.record_transient_failure(cb_key)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    self._ledger.release_unresolved(reservation.reservation_id, str(exc))
                    self._circuits.record_transient_failure(cb_key)
                    break
                else:
                    try:
                        self._circuits.record_success(cb_key)
                        actual_tokens = resp.input_tokens + resp.output_tokens + resp.reasoning_tokens
                        actual_cost = resp.reported_cost_usd
                        if (
                            actual_cost is None
                            and not cost_unknown
                            and model.input_price_per_1m
                            and model.output_price_per_1m
                        ):
                            actual_cost = (
                                Decimal(resp.input_tokens) * model.input_price_per_1m
                                + Decimal(resp.output_tokens + resp.reasoning_tokens) * model.output_price_per_1m
                            ) / Decimal("1000000")
                        latency_ms = int((time.monotonic() - started) * 1000)
                        self.record_latency(model.model_id, latency_ms)
                        self._ledger.settle(
                            reservation.reservation_id,
                            actual_tokens,
                            actual_cost,
                            latency_ms=latency_ms,
                        )
                    finally:
                        self._semaphores.release(sem_key)
                    return resp
            self._semaphores.release(sem_key)
            self._circuits.release_probe(cb_key)

        if partial[0] and on_reset is not None:
            on_reset()
        if last_error:
            raise last_error
        raise CircuitOpenError("All eligible provider circuits are currently open")
