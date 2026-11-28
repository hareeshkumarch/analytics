from typing import Any, Literal, Union
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.limits import DEFAULT_GLOBAL_LIMITS, DISPLAY_NAME_CHARS, IDENTIFIER_CHARS, MAX_CONNECTOR_FIELDS, MAX_CONNECTOR_VALUE_CHARS
from app.domain.models import ConnectorFamily, RunMode
from app.domain.providers import ProviderId, RoutingPreset
from app.domain.query_ir import (
    DEFAULT_BUCKETS,
    MAX_BUCKETS,
    AggregateFunction,
    AggregateMetricSpec,
    AggregateQueryIR,
    ComparePeriodsQueryIR,
    ContributionBreakdownQueryIR,
    DetailQueryIR,
    DistributionQueryIR,
    FilterOperator,
    FilterPredicate,
    QueryIR,
    ReconciliationLookupQueryIR,
    SortSpec,
)


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilterPredicateSchema(StrictApiModel):
    field_id: str = Field(..., min_length=1, max_length=128)
    op: FilterOperator
    value: Any = None

    def to_domain(self) -> FilterPredicate:
        return FilterPredicate(field_id=self.field_id, op=self.op, value=self.value)


class SortSpecSchema(StrictApiModel):
    field_id: str = Field(..., min_length=1, max_length=128)
    direction: Literal["asc", "desc"] = "asc"

    def to_domain(self) -> SortSpec:
        return SortSpec(field_id=self.field_id, direction=self.direction)


class AggregateMetricSchema(StrictApiModel):
    alias: str = Field(..., min_length=1, max_length=128)
    func: AggregateFunction
    field_id: str = Field(..., min_length=1, max_length=128)

    def to_domain(self) -> AggregateMetricSpec:
        return AggregateMetricSpec(alias=self.alias, func=self.func, field_id=self.field_id)


class AggregateQueryIRSchema(StrictApiModel):
    operation: Literal["aggregate"] = "aggregate"
    table_id: str = Field(..., min_length=1, max_length=128)
    metrics: list[AggregateMetricSchema]
    group_by: list[str] = []
    filters: list[FilterPredicateSchema] = []
    order_by: list[SortSpecSchema] = []
    limit: int = Field(1000, ge=1, le=1000)
    purpose: str = ""

    def to_domain(self) -> AggregateQueryIR:
        return AggregateQueryIR(
            table_id=self.table_id,
            metrics=tuple(m.to_domain() for m in self.metrics),
            group_by=tuple(self.group_by),
            filters=tuple(f.to_domain() for f in self.filters),
            order_by=tuple(o.to_domain() for o in self.order_by),
            limit=self.limit,
            purpose=self.purpose,
        )


class DetailQueryIRSchema(StrictApiModel):
    operation: Literal["detail"] = "detail"
    table_id: str = Field(..., min_length=1, max_length=128)
    select_fields: list[str]
    filters: list[FilterPredicateSchema] = []
    order_by: list[SortSpecSchema] = []
    limit: int = Field(1000, ge=1, le=1000)
    purpose: str = ""

    def to_domain(self) -> DetailQueryIR:
        return DetailQueryIR(
            table_id=self.table_id,
            select_fields=tuple(self.select_fields),
            filters=tuple(f.to_domain() for f in self.filters),
            order_by=tuple(o.to_domain() for o in self.order_by),
            limit=self.limit,
            purpose=self.purpose,
        )


class ComparePeriodsQueryIRSchema(StrictApiModel):
    operation: Literal["compare_periods"] = "compare_periods"
    table_id: str = Field(..., min_length=1, max_length=128)
    date_field: str = Field(..., min_length=1, max_length=128)
    metric: AggregateMetricSchema
    current_start: str = Field(..., min_length=1)
    current_end: str = Field(..., min_length=1)
    previous_start: str = Field(..., min_length=1)
    previous_end: str = Field(..., min_length=1)
    group_by: list[str] = []
    filters: list[FilterPredicateSchema] = []
    limit: int = Field(1000, ge=1, le=1000)
    purpose: str = ""

    def to_domain(self) -> ComparePeriodsQueryIR:
        return ComparePeriodsQueryIR(
            table_id=self.table_id,
            date_field=self.date_field,
            metric=self.metric.to_domain(),
            current_start=self.current_start,
            current_end=self.current_end,
            previous_start=self.previous_start,
            previous_end=self.previous_end,
            group_by=tuple(self.group_by),
            filters=tuple(f.to_domain() for f in self.filters),
            limit=self.limit,
            purpose=self.purpose,
        )


class ContributionBreakdownQueryIRSchema(StrictApiModel):
    operation: Literal["contribution_breakdown"] = "contribution_breakdown"
    table_id: str = Field(..., min_length=1, max_length=128)
    metric: AggregateMetricSchema
    dimension_field: str = Field(..., min_length=1, max_length=128)
    filters: list[FilterPredicateSchema] = []
    limit: int = Field(100, ge=1, le=1000)
    purpose: str = ""

    def to_domain(self) -> ContributionBreakdownQueryIR:
        return ContributionBreakdownQueryIR(
            table_id=self.table_id,
            metric=self.metric.to_domain(),
            dimension_field=self.dimension_field,
            filters=tuple(f.to_domain() for f in self.filters),
            limit=self.limit,
            purpose=self.purpose,
        )


class DistributionQueryIRSchema(StrictApiModel):
    operation: Literal["distribution"] = "distribution"
    table_id: str = Field(..., min_length=1, max_length=128)
    field_id: str = Field(..., min_length=1, max_length=128)
    buckets: int = Field(DEFAULT_BUCKETS, ge=1, le=MAX_BUCKETS)
    filters: list[FilterPredicateSchema] = []
    limit: int = Field(100, ge=1, le=1000)
    purpose: str = ""

    def to_domain(self) -> DistributionQueryIR:
        return DistributionQueryIR(
            table_id=self.table_id,
            field_id=self.field_id,
            buckets=self.buckets,
            filters=tuple(f.to_domain() for f in self.filters),
            limit=self.limit,
            purpose=self.purpose,
        )


class ReconciliationLookupQueryIRSchema(StrictApiModel):
    operation: Literal["reconciliation_lookup"] = "reconciliation_lookup"
    left_table_id: str = Field(..., min_length=1, max_length=128)
    right_table_id: str = Field(..., min_length=1, max_length=128)
    left_key: str = Field(..., min_length=1, max_length=128)
    right_key: str = Field(..., min_length=1, max_length=128)
    left_amount_field: str = Field(..., min_length=1, max_length=128)
    right_amount_field: str = Field(..., min_length=1, max_length=128)
    filters: list[FilterPredicateSchema] = []
    limit: int = Field(1000, ge=1, le=1000)
    purpose: str = ""

    def to_domain(self) -> ReconciliationLookupQueryIR:
        return ReconciliationLookupQueryIR(
            left_table_id=self.left_table_id,
            right_table_id=self.right_table_id,
            left_key=self.left_key,
            right_key=self.right_key,
            left_amount_field=self.left_amount_field,
            right_amount_field=self.right_amount_field,
            filters=tuple(f.to_domain() for f in self.filters),
            limit=self.limit,
            purpose=self.purpose,
        )


QueryIRUnionSchema = Union[
    AggregateQueryIRSchema,
    DetailQueryIRSchema,
    ComparePeriodsQueryIRSchema,
    ContributionBreakdownQueryIRSchema,
    DistributionQueryIRSchema,
    ReconciliationLookupQueryIRSchema,
]


class QueryIREnvelope(StrictApiModel):
    query: QueryIRUnionSchema = Field(..., discriminator="operation")

    def to_domain(self) -> QueryIR:
        return self.query.to_domain()


class ConnectorProbeRequest(StrictApiModel):
    family: ConnectorFamily
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)


class ImportForm(StrictApiModel):
    connector_id: str = Field(..., min_length=1, max_length=IDENTIFIER_CHARS, pattern=r"^[a-z0-9_-]+$")
    values: dict[str, str | bool | int | float] = Field(default_factory=dict, max_length=MAX_CONNECTOR_FIELDS)

    @field_validator("values")
    @classmethod
    def _bounded(cls, values: dict[str, str | bool | int | float]) -> dict[str, str | bool | int | float]:
        for key, value in values.items():
            if len(key) > IDENTIFIER_CHARS or (isinstance(value, str) and len(value) > MAX_CONNECTOR_VALUE_CHARS):
                raise ValueError("Form values are too long.")
        return values


class ConnectorExtractRequest(StrictApiModel):
    family: ConnectorFamily
    source_id: str | None = Field(default=None, min_length=5, max_length=64, pattern=r"^src_[A-Za-z0-9_-]+$")
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    selected_tables: list[str] = Field(default_factory=list)
    selected_columns: dict[str, list[str]] = Field(default_factory=dict)
    form: ImportForm | None = None
    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_CHARS)


class ApproveSemanticsRequest(StrictApiModel):
    expected_version: int = Field(..., ge=1)


class MetricDefinitionEdit(StrictApiModel):
    metric_id: str = Field(..., min_length=1, max_length=IDENTIFIER_CHARS)
    display_name: str = Field(..., min_length=1, max_length=DISPLAY_NAME_CHARS)
    unit: str | None = Field(default=None, max_length=32)
    currency: str | None = Field(default=None, max_length=16)


class UpdateSemanticsRequest(StrictApiModel):
    expected_version: int = Field(..., ge=1)
    metrics: list[MetricDefinitionEdit] = Field(default_factory=list, max_length=200)
    remove_metric_ids: list[str] = Field(default_factory=list, max_length=200)


class CreateWorkspaceRequest(StrictApiModel):
    name: str = Field(..., min_length=1, max_length=DISPLAY_NAME_CHARS)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


class UpdateWorkspaceSettingsRequest(StrictApiModel):
    business_timezone: str = Field(..., min_length=1, max_length=64)


class UpdateRoutingRequest(StrictApiModel):
    expected_version: int = Field(..., ge=1)
    preset: RoutingPreset | None = None
    fallback_enabled: bool | None = None
    role_routes: dict[str, list[str]] | None = None
    hard_cost_ceiling_usd: Decimal | None = Field(default=None, gt=0)


class CreateRunRequest(StrictApiModel):
    question: str = Field(..., min_length=1, max_length=DEFAULT_GLOBAL_LIMITS.max_question_chars)
    mode: RunMode = RunMode.QUICK
    snapshot_id: str | None = None
    semantic_version_id: str | None = None
    execute_inline: bool = False
    background_async: bool = True
    follow_up_of: str | None = Field(default=None, max_length=64, pattern=r"^run_[A-Za-z0-9]+$")


class ClarifyRunRequest(StrictApiModel):
    revision: int = Field(..., ge=0)
    answer: str = Field(..., min_length=1, max_length=DEFAULT_GLOBAL_LIMITS.max_clarification_chars)


class CreatePinRequest(StrictApiModel):
    title: str = Field(..., min_length=1, max_length=DEFAULT_GLOBAL_LIMITS.max_pin_title_chars)
    question_text: str = Field(..., min_length=1, max_length=DEFAULT_GLOBAL_LIMITS.max_question_chars)
    run_id: str = Field(..., min_length=1, max_length=64)


class SetOpsSwitchRequest(StrictApiModel):
    paused: bool
    reason: str = Field(..., min_length=1, max_length=500)


class PinAlertRequest(StrictApiModel):
    # Flag the pin when its headline figure is above or below this value.
    direction: Literal["above", "below"]
    value: float


class UpdatePinRequest(StrictApiModel):
    # Every field is optional: send only what should change.
    title: str | None = Field(default=None, min_length=1, max_length=DEFAULT_GLOBAL_LIMITS.max_pin_title_chars)
    auto_refresh: bool | None = None
    schedule: Literal["off", "daily", "weekly"] | None = None
    alert: PinAlertRequest | None = None
    clear_alert: bool = False


class CreateDatasetNoteRequest(StrictApiModel):
    text: str = Field(..., min_length=3, max_length=300)


class RefreshPinsRequest(StrictApiModel):
    # Omit to re-run every pin whose dataset has a newer approved version.
    pin_ids: list[str] | None = Field(default=None, max_length=200)


class CreateExportRequest(StrictApiModel):
    format: Literal["csv", "html", "pdf", "docx"] = "html"
    run_id: str = ""
    summary_markdown: str = ""
    snapshot_id: str = ""
    semantic_version_id: str = ""


class SaveProviderKeyRequest(StrictApiModel):
    provider_id: ProviderId
    api_key: str = Field(..., min_length=1, max_length=512)
