from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Union

from app.domain.limits import PUBLIC_ERROR_CHARS

MAX_QUERY_ROWS = 1000
DEFAULT_LIST_ROWS = 100
DEFAULT_BUCKETS = 10
MAX_BUCKETS = 100


class FilterOperator(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class AggregateFunction(str, Enum):
    SUM = "sum"
    COUNT = "count"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COUNT_DISTINCT = "count_distinct"


@dataclass(frozen=True, slots=True)
class FilterPredicate:
    field_id: str
    op: FilterOperator
    value: str | int | float | bool | list[str | int | float] | None = None


@dataclass(frozen=True, slots=True)
class SortSpec:
    field_id: str
    direction: Literal["asc", "desc"] = "asc"


@dataclass(frozen=True, slots=True)
class AggregateMetricSpec:
    alias: str
    func: AggregateFunction
    field_id: str


@dataclass(frozen=True, slots=True)
class AggregateQueryIR:
    operation: Literal["aggregate"] = "aggregate"
    table_id: str = ""
    metrics: tuple[AggregateMetricSpec, ...] = ()
    group_by: tuple[str, ...] = ()
    filters: tuple[FilterPredicate, ...] = ()
    order_by: tuple[SortSpec, ...] = ()
    limit: int = MAX_QUERY_ROWS
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class DetailQueryIR:
    operation: Literal["detail"] = "detail"
    table_id: str = ""
    select_fields: tuple[str, ...] = ()
    filters: tuple[FilterPredicate, ...] = ()
    order_by: tuple[SortSpec, ...] = ()
    limit: int = MAX_QUERY_ROWS
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class ComparePeriodsQueryIR:
    operation: Literal["compare_periods"] = "compare_periods"
    table_id: str = ""
    date_field: str = ""
    metric: AggregateMetricSpec = field(
        default_factory=lambda: AggregateMetricSpec("value", AggregateFunction.SUM, "")
    )
    current_start: str = ""
    current_end: str = ""
    previous_start: str = ""
    previous_end: str = ""
    group_by: tuple[str, ...] = ()
    filters: tuple[FilterPredicate, ...] = ()
    limit: int = MAX_QUERY_ROWS
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class ContributionBreakdownQueryIR:
    operation: Literal["contribution_breakdown"] = "contribution_breakdown"
    table_id: str = ""
    metric: AggregateMetricSpec = field(
        default_factory=lambda: AggregateMetricSpec("value", AggregateFunction.SUM, "")
    )
    dimension_field: str = ""
    filters: tuple[FilterPredicate, ...] = ()
    limit: int = DEFAULT_LIST_ROWS
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class DistributionQueryIR:
    operation: Literal["distribution"] = "distribution"
    table_id: str = ""
    field_id: str = ""
    buckets: int = DEFAULT_BUCKETS
    filters: tuple[FilterPredicate, ...] = ()
    limit: int = DEFAULT_LIST_ROWS
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class ReconciliationLookupQueryIR:
    operation: Literal["reconciliation_lookup"] = "reconciliation_lookup"
    left_table_id: str = ""
    right_table_id: str = ""
    left_key: str = ""
    right_key: str = ""
    left_amount_field: str = ""
    right_amount_field: str = ""
    filters: tuple[FilterPredicate, ...] = ()
    limit: int = MAX_QUERY_ROWS
    purpose: str = ""


QueryIR = Union[
    AggregateQueryIR,
    DetailQueryIR,
    ComparePeriodsQueryIR,
    ContributionBreakdownQueryIR,
    DistributionQueryIR,
    ReconciliationLookupQueryIR,
]


class QueryIRParseError(ValueError):
    def __init__(self, message: str, *, failed_fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.failed_fields = tuple(dict.fromkeys(failed_fields))


_MAX_IDENT = 128


def _ident(payload: dict[str, Any], key: str, required: bool = True, *, failed_key: str | None = None) -> str:
    err_key = failed_key or key
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise QueryIRParseError(f"'{key}' must be a string", failed_fields=(err_key,))
    if required and not value:
        raise QueryIRParseError(f"'{key}' is required", failed_fields=(err_key,))
    if len(value) > _MAX_IDENT:
        raise QueryIRParseError(f"'{key}' is too long", failed_fields=(err_key,))
    return value


def _ident_list(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    values = payload.get(key) or []
    if not isinstance(values, list) or not all(isinstance(v, str) and 0 < len(v) <= _MAX_IDENT for v in values):
        raise QueryIRParseError(f"'{key}' must be a list of column names", failed_fields=(key,))
    return tuple(values)


def _limit(payload: dict[str, Any], default: int) -> int:
    value = payload.get("limit", default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_QUERY_ROWS:
        raise QueryIRParseError(f"'limit' must be an integer between 1 and {MAX_QUERY_ROWS}", failed_fields=("limit",))
    return value


def _enum(enum_type: type[Enum], value: Any, key: str, *, failed_key: str | None = None) -> Any:
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(e.value for e in enum_type)
        raise QueryIRParseError(
            f"'{key}' must be one of: {allowed}",
            failed_fields=(failed_key or key.split(".")[0],),
        ) from exc


def _metric(payload: Any, key: str) -> AggregateMetricSpec:
    if not isinstance(payload, dict):
        raise QueryIRParseError(
            f"'{key}' must be an object with alias, func and field_id",
            failed_fields=(key,),
        )
    return AggregateMetricSpec(
        alias=_ident(payload, "alias", failed_key=key),
        func=_enum(AggregateFunction, payload.get("func"), f"{key}.func", failed_key=key),
        field_id=_ident(payload, "field_id", failed_key=key),
    )


def _filters(payload: dict[str, Any]) -> tuple[FilterPredicate, ...]:
    raw = payload.get("filters") or []
    if not isinstance(raw, list):
        raise QueryIRParseError("'filters' must be a list", failed_fields=("filters",))
    out: list[FilterPredicate] = []
    for item in raw:
        if not isinstance(item, dict):
            raise QueryIRParseError(
                "each filter must be an object with field_id, op and value",
                failed_fields=("filters",),
            )
        value = item.get("value")
        if value is not None and not isinstance(value, (str, int, float, bool, list)):
            raise QueryIRParseError(
                "filter 'value' must be a string, number, boolean or list",
                failed_fields=("filters",),
            )
        out.append(
            FilterPredicate(
                field_id=_ident(item, "field_id", failed_key="filters"),
                op=_enum(FilterOperator, item.get("op"), "filters.op", failed_key="filters"),
                value=value,
            )
        )
    return tuple(out)


def _order_by(payload: dict[str, Any]) -> tuple[SortSpec, ...]:
    raw = payload.get("order_by") or []
    if not isinstance(raw, list):
        raise QueryIRParseError("'order_by' must be a list", failed_fields=("order_by",))
    out: list[SortSpec] = []
    for item in raw:
        if not isinstance(item, dict):
            raise QueryIRParseError(
                "each order_by entry must be an object with field_id and direction",
                failed_fields=("order_by",),
            )
        direction = item.get("direction", "asc")
        if direction not in ("asc", "desc"):
            raise QueryIRParseError(
                "order_by 'direction' must be 'asc' or 'desc'",
                failed_fields=("order_by",),
            )
        out.append(SortSpec(field_id=_ident(item, "field_id", failed_key="order_by"), direction=direction))
    return tuple(out)


def _purpose(payload: dict[str, Any]) -> str:
    value = payload.get("purpose", "")
    return value[:PUBLIC_ERROR_CHARS] if isinstance(value, str) else ""


def parse_query_ir(payload: dict[str, Any]) -> QueryIR:
    if not isinstance(payload, dict):
        raise QueryIRParseError("query must be a JSON object")
    operation = payload.get("operation")
    if operation == "aggregate":
        metrics_raw = payload.get("metrics") or []
        if not isinstance(metrics_raw, list) or not metrics_raw:
            raise QueryIRParseError("'metrics' must be a non-empty list", failed_fields=("metrics",))
        return AggregateQueryIR(
            table_id=_ident(payload, "table_id"),
            metrics=tuple(_metric(m, "metrics") for m in metrics_raw),
            group_by=_ident_list(payload, "group_by"),
            filters=_filters(payload),
            order_by=_order_by(payload),
            limit=_limit(payload, MAX_QUERY_ROWS),
            purpose=_purpose(payload),
        )
    if operation == "detail":
        return DetailQueryIR(
            table_id=_ident(payload, "table_id"),
            select_fields=_ident_list(payload, "select_fields"),
            filters=_filters(payload),
            order_by=_order_by(payload),
            limit=_limit(payload, DEFAULT_LIST_ROWS),
            purpose=_purpose(payload),
        )
    if operation == "compare_periods":
        return ComparePeriodsQueryIR(
            table_id=_ident(payload, "table_id"),
            date_field=_ident(payload, "date_field"),
            metric=_metric(payload.get("metric"), "metric"),
            current_start=_ident(payload, "current_start"),
            current_end=_ident(payload, "current_end"),
            previous_start=_ident(payload, "previous_start"),
            previous_end=_ident(payload, "previous_end"),
            group_by=_ident_list(payload, "group_by"),
            filters=_filters(payload),
            limit=_limit(payload, MAX_QUERY_ROWS),
            purpose=_purpose(payload),
        )
    if operation == "contribution_breakdown":
        return ContributionBreakdownQueryIR(
            table_id=_ident(payload, "table_id"),
            metric=_metric(payload.get("metric"), "metric"),
            dimension_field=_ident(payload, "dimension_field"),
            filters=_filters(payload),
            limit=_limit(payload, DEFAULT_LIST_ROWS),
            purpose=_purpose(payload),
        )
    if operation == "distribution":
        buckets = payload.get("buckets", DEFAULT_BUCKETS)
        if not isinstance(buckets, int) or isinstance(buckets, bool) or not 1 <= buckets <= MAX_BUCKETS:
            raise QueryIRParseError(f"'buckets' must be an integer between 1 and {MAX_BUCKETS}", failed_fields=("buckets",))
        return DistributionQueryIR(
            table_id=_ident(payload, "table_id"),
            field_id=_ident(payload, "field_id"),
            buckets=buckets,
            filters=_filters(payload),
            limit=_limit(payload, DEFAULT_LIST_ROWS),
            purpose=_purpose(payload),
        )
    if operation == "reconciliation_lookup":
        return ReconciliationLookupQueryIR(
            left_table_id=_ident(payload, "left_table_id"),
            right_table_id=_ident(payload, "right_table_id"),
            left_key=_ident(payload, "left_key"),
            right_key=_ident(payload, "right_key"),
            left_amount_field=_ident(payload, "left_amount_field"),
            right_amount_field=_ident(payload, "right_amount_field"),
            filters=_filters(payload),
            limit=_limit(payload, MAX_QUERY_ROWS),
            purpose=_purpose(payload),
        )
    raise QueryIRParseError(
        "'operation' must be one of: aggregate, detail, compare_periods, contribution_breakdown, "
        "distribution, reconciliation_lookup",
        failed_fields=("operation",),
    )
