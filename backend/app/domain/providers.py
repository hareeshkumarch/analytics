from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class ProviderId(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    GROQ = "groq"
    XAI = "xai"
    KIMI = "kimi"
    NVIDIA_NIM = "nvidia_nim"


class ApiProfile(str, Enum):
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GEMINI_INTERACTIONS = "gemini_interactions"
    GROQ_CHAT = "groq_chat"
    XAI_RESPONSES = "xai_responses"
    KIMI_CHAT = "kimi_chat"
    NVIDIA_NIM_CHAT = "nvidia_nim_chat"


class AgentRole(str, Enum):
    PLANNER = "planner"
    DATA_SPECIALIST = "data_specialist"
    RECONCILIATION_SPECIALIST = "reconciliation_specialist"
    EVIDENCE_REVIEWER = "evidence_reviewer"
    ANSWER_COMPOSER = "answer_composer"
    SEMANTIC_ASSISTANT = "semantic_assistant"


class RoutingPreset(str, Enum):
    BALANCED = "balanced"
    COST = "cost"
    LATENCY = "latency"
    QUALITY = "quality"


class CircuitStatus(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    structured_output: bool
    tool_calling: bool
    streaming: bool
    structured_with_tools: bool
    reasoning_tokens: bool
    sampling_params: bool = True
    refusal_fallback: bool = False


@dataclass(frozen=True, slots=True)
class ModelProfile:
    model_id: str
    provider_id: ProviderId
    api_profile: ApiProfile
    roles: tuple[AgentRole, ...]
    context_window: int
    max_output_tokens: int
    capabilities: ModelCapabilities
    input_price_per_1m: Decimal | None
    output_price_per_1m: Decimal | None
    verification_date: str
    enabled: bool = True
    contract_tested: bool = False
    live_verified: bool = False


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    provider_id: ProviderId
    api_profile: ApiProfile
    endpoint_profile_id: str
    base_url: str
    default_timeout_seconds: int = 30
    max_concurrent_requests: int = 4
    allow_schema_egress: bool = True
    allow_raw_record_egress: bool = False


@dataclass(frozen=True, slots=True)
class CircuitBreakerState:
    key: str
    status: CircuitStatus = CircuitStatus.CLOSED
    failure_timestamps: tuple[float, ...] = ()
    opened_at: float | None = None
    half_open_probe_in_flight: bool = False


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    reservation_id: str
    run_id: str
    operation_id: str
    attempt_id: str
    reserved_tokens: int
    reserved_cost_usd: Decimal | None
    cost_is_unknown: bool
    settled: bool = False
    actual_tokens: int | None = None
    actual_cost_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    policy_version: int
    preset: RoutingPreset
    fallback_enabled: bool
    role_routes: dict[AgentRole, tuple[str, ...]] = field(default_factory=dict)
    allowed_providers: tuple[ProviderId, ...] = ()
    hard_cost_ceiling_usd: Decimal | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0
    reported_cost_usd: Decimal | None = None
    estimated_cost_usd: Decimal | None = None
    cost_is_unknown: bool = False
