from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from app.domain.providers import (
    AgentRole,
    BudgetReservation,
    ModelProfile,
    ProviderId,
    ProviderProfile,
)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    strict: bool = False


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    request_id: str
    run_id: str
    role: AgentRole
    model_id: str
    messages: tuple[dict[str, Any], ...]
    tools: tuple[ToolDefinition, ...] = ()
    structured_schema: dict[str, Any] | None = None
    stream: bool = False
    max_output_tokens: int = 2048
    temperature: float = 0.0
    timeout_seconds: int = 30
    continuation_state: dict[str, Any] | None = None
    tool_choice: str | None = None


@dataclass(frozen=True, slots=True)
class ModelResponse:
    provider_id: ProviderId
    model_id: str
    text: str
    tool_calls: tuple[ToolCallResult, ...] = ()
    structured_output: dict[str, Any] | None = None
    finish_reason: str = "stop"
    refusal_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    reported_cost_usd: Decimal | None = None
    continuation_state: dict[str, Any] | None = None


class ModelGateway(Protocol):
    provider_id: ProviderId

    def invoke(
        self,
        request: ModelRequest,
        provider_profile: ProviderProfile,
        model_profile: ModelProfile,
        api_key: str,
    ) -> ModelResponse: ...


class BudgetLedger(Protocol):
    def configure_run(
        self,
        run_id: str,
        *,
        max_attempts: int,
        max_tokens: int = 500_000,
        max_cost_usd: Decimal | None = None,
    ) -> None: ...

    def get_run_totals(self, run_id: str) -> dict[str, object]: ...

    def reserve(
        self,
        run_id: str,
        operation_id: str,
        attempt_id: str,
        estimated_tokens: int,
        estimated_cost_usd: Decimal | None,
        cost_is_unknown: bool,
        hard_cost_ceiling_usd: Decimal | None,
    ) -> BudgetReservation: ...

    def settle(
        self,
        reservation_id: str,
        actual_tokens: int,
        actual_cost_usd: Decimal | None,
    ) -> BudgetReservation: ...

    def release_unresolved(self, reservation_id: str, reason: str) -> None: ...
