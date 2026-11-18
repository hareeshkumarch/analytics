import json
import re

import pyarrow as pa

from app.domain.models import ChartSpec, ColumnProfile, MetricDefinition, TableSchema

ISO_CURRENCIES = frozenset(
    {
        "aed", "aud", "brl", "cad", "chf", "cny", "czk", "dkk", "eur", "gbp", "hkd", "huf", "idr",
        "ils", "inr", "jpy", "krw", "mxn", "myr", "nok", "nzd", "php", "pln", "rub", "sar", "sek",
        "sgd", "thb", "try", "twd", "usd", "vnd", "zar",
    }
)
MEASURE_HINTS = ("amount", "total", "price", "cost", "revenue", "sales", "fee", "value", "balance", "spend")
NUMERIC_TYPE_HINTS = ("int", "decimal", "float", "double", "numeric", "real")
TEMPORAL_HINTS = ("date", "time", "timestamp")
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(name: str) -> list[str]:
    return _TOKEN.findall(name.lower())


def detect_currency(column_name: str, declared: str | None = None) -> str | None:
    if declared:
        return declared.upper()
    for token in _tokens(column_name):
        if token in ISO_CURRENCIES:
            return token.upper()
    return None


def _is_numeric(arrow_type: str) -> bool:
    lowered = arrow_type.lower()
    return any(hint in lowered for hint in NUMERIC_TYPE_HINTS)


def _is_measure(column_name: str, arrow_type: str) -> bool:
    tokens = _tokens(column_name)
    named_like_measure = any(hint in token for token in tokens for hint in MEASURE_HINTS)
    return named_like_measure and (_is_numeric(arrow_type) or arrow_type.lower() in ("string", "utf8", "large_string"))


def _identifier_column(schema: TableSchema) -> str | None:
    if schema.primary_key_candidates:
        return schema.primary_key_candidates[0]
    for col in schema.columns:
        tokens = _tokens(col.name)
        if tokens and tokens[-1] == "id":
            return col.name
    return None


def _profile_key(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)


def profile_arrow_table(table: pa.Table, is_sampled: bool = False) -> tuple[ColumnProfile, ...]:
    profiles: list[ColumnProfile] = []
    for col_name in table.column_names:
        values = table[col_name].to_pylist()
        non_null = [v for v in values if v is not None]
        distinct_count = len({_profile_key(value) for value in non_null})
        try:
            min_value = str(min(non_null)) if non_null else None
            max_value = str(max(non_null)) if non_null else None
        except TypeError:
            ordered_values = sorted(str(value) for value in non_null)
            min_value = ordered_values[0] if ordered_values else None
            max_value = ordered_values[-1] if ordered_values else None
        profiles.append(
            ColumnProfile(
                column_name=col_name,
                null_count=len(values) - len(non_null),
                distinct_count=distinct_count,
                duplicate_count=max(0, len(non_null) - distinct_count),
                min_value=min_value,
                max_value=max_value,
                is_sampled=is_sampled,
            )
        )
    return tuple(profiles)


def propose_metrics_from_schema(schema: TableSchema) -> tuple[MetricDefinition, ...]:
    proposals: list[MetricDefinition] = []
    for col in schema.columns:
        if not _is_measure(col.name, col.arrow_type):
            continue
        currency = detect_currency(col.name, col.currency_code)
        proposals.append(
            MetricDefinition(
                metric_id=f"{schema.table_name}__{col.name}__sum",
                display_name=f"Total {col.name}",
                expression=f"SUM({col.name})",
                grain=schema.table_name,
                unit=currency or "units",
                currency=currency,
                approved=False,
            )
        )
    id_column = _identifier_column(schema)
    proposals.append(
        MetricDefinition(
            metric_id=f"{schema.table_name}__row_count",
            display_name=f"{schema.table_name} count",
            expression=f"COUNT(DISTINCT {id_column})" if id_column else "COUNT(*)",
            grain=schema.table_name,
            unit="count",
            approved=False,
        )
    )
    return tuple(proposals)


def recommend_chart_candidates(
    schema: TableSchema,
    profiles: tuple[ColumnProfile, ...],
    artifact_id: str,
    evidence_id: str,
) -> tuple[ChartSpec, ...]:
    date_cols = [c.name for c in schema.columns if any(h in t for t in _tokens(c.name) for h in TEMPORAL_HINTS)]
    num_cols = [c.name for c in schema.columns if _is_numeric(c.arrow_type)]
    cat_cols = [
        p.column_name
        for p in profiles
        if 1 < p.distinct_count <= 25 and p.column_name not in date_cols and p.column_name not in num_cols
    ]

    def chart(kind: str, suffix: str, title: str, x_field: str | None) -> ChartSpec:
        return ChartSpec(
            chart_id=f"chart_{schema.table_name}_{suffix}",
            chart_type=kind,
            title=title,
            x_field=x_field,
            y_fields=(num_cols[0],),
            unit=None,
            timezone="UTC",
            artifact_id=artifact_id,
            evidence_id=evidence_id,
        )

    charts: list[ChartSpec] = []
    if date_cols and num_cols:
        charts.append(chart("line", "trend", f"{num_cols[0]} over {date_cols[0]}", date_cols[0]))
    if cat_cols and num_cols:
        charts.append(chart("bar", "breakdown", f"{num_cols[0]} by {cat_cols[0]}", cat_cols[0]))
    if num_cols:
        charts.append(chart("kpi", "kpi", f"Total {num_cols[0]}", None))
    return tuple(charts)
