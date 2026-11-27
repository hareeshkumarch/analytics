import math
import re
from collections.abc import Collection, Iterable
from typing import Any

from app.domain.models import ChartSpec

MAX_CHARTS = 3
MAX_POINTS = 50
MAX_SERIES = 3
MAX_TITLE_CHARS = 120
MIN_CHART_POINTS = 2
CHARTABLE_OPERATIONS = frozenset(
    {"aggregate", "contribution_breakdown", "compare_periods", "distribution", "reconciliation_lookup"}
)
_NUMBER = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")
_ISO_DATE = re.compile(r"\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?")
_CONTROL = re.compile(r"[\x00-\x1F\x7F]+")

Row = tuple[str | float | None, ...]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def _label(value: Any) -> str:
    return "(blank)" if value is None or value == "" else str(value)


def _is_numeric_column(rows: list[list[Any]], index: int) -> bool:
    values = [r[index] for r in rows if r[index] is not None]
    return bool(values) and all(_number(v) is not None for v in values)


def _distribution(columns: list[str], rows: list[list[Any]]) -> tuple[str, str, tuple[str, ...], list[Row]] | None:
    if not {"bucket_min", "bucket_max", "row_count"} <= set(columns):
        return None
    lo, hi, count = columns.index("bucket_min"), columns.index("bucket_max"), columns.index("row_count")
    points: list[Row] = []
    for r in rows:
        low, high = _number(r[lo]), _number(r[hi])
        if low is None or high is None:
            continue
        points.append((f"{low:g}–{high:g}", _number(r[count])))
    return "histogram", "range", ("row_count",), points


def _compare_periods(columns: list[str], rows: list[list[Any]]) -> tuple[str, str, tuple[str, ...], list[Row]] | None:
    if not {"current_value", "previous_value"} <= set(columns):
        return None
    cur, prev = columns.index("current_value"), columns.index("previous_value")
    groups = [i for i, name in enumerate(columns) if i not in (cur, prev)]
    if not groups:
        if len(rows) != 1:
            return None
        return "bar", "period", ("value",), [
            ("Previous period", _number(rows[0][prev])),
            ("Current period", _number(rows[0][cur])),
        ]
    key = groups[0]
    points: list[Row] = [(_label(r[key]), _number(r[prev]), _number(r[cur])) for r in rows]
    return "bar", columns[key], ("previous_value", "current_value"), points


def _reconciliation(columns: list[str], rows: list[list[Any]]) -> tuple[str, str, tuple[str, ...], list[Row]] | None:
    if not {"match_key", "left_total", "right_total"} <= set(columns):
        return None
    key, left, right = columns.index("match_key"), columns.index("left_total"), columns.index("right_total")
    points: list[Row] = [(_label(r[key]), _number(r[left]), _number(r[right])) for r in rows]
    return "bar", "match_key", ("left_total", "right_total"), points


def _grouped(columns: list[str], rows: list[list[Any]]) -> tuple[str, str, tuple[str, ...], list[Row]] | None:
    if len(rows) < MIN_CHART_POINTS or len(columns) < MIN_CHART_POINTS:
        return None
    numeric = [i for i in range(len(columns)) if _is_numeric_column(rows, i)]
    labels = [i for i in range(len(columns)) if i not in numeric]
    x = labels[0] if labels else 0
    series = [i for i in numeric if i != x][:MAX_SERIES]
    if not series or len(labels) > 1:
        return None
    points: list[Row] = [(_label(r[x]), *(_number(r[i]) for i in series)) for r in rows]
    is_dates = all(isinstance(p[0], str) and _ISO_DATE.fullmatch(p[0]) for p in points)
    if is_dates:
        points.sort(key=lambda p: str(p[0]))
    return ("line" if is_dates else "bar"), columns[x], tuple(columns[i] for i in series), points


def _chart_for(evidence: dict[str, Any]) -> ChartSpec | None:
    operation = evidence.get("operation")
    columns = [str(c) for c in evidence.get("columns") or []]
    rows = [list(r) for r in evidence.get("rows") or [] if isinstance(r, (list, tuple)) and len(r) == len(columns)]
    if operation not in CHARTABLE_OPERATIONS or not rows:
        return None
    if operation == "distribution":
        shape = _distribution(columns, rows)
    elif operation == "compare_periods":
        shape = _compare_periods(columns, rows)
    elif operation == "reconciliation_lookup":
        shape = _reconciliation(columns, rows)
    else:
        shape = _grouped(columns, rows)
    if shape is None:
        return None
    chart_type, x_field, y_fields, points = shape
    points = [p for p in points if any(v is not None for v in p[1:])]
    if len(points) < MIN_CHART_POINTS:
        return None
    cut = len(points) > MAX_POINTS
    points = points[-MAX_POINTS:] if chart_type == "line" else points[:MAX_POINTS]
    evidence_id = str(evidence["evidence_id"])
    purpose = _CONTROL.sub(" ", str(evidence.get("purpose") or "")).strip()
    title = (purpose or f"{', '.join(y_fields)} by {x_field}")[:MAX_TITLE_CHARS]
    row_count = evidence.get("row_count")
    return ChartSpec(
        chart_id=f"chart_{evidence_id.removeprefix('ev_')}",
        chart_type=chart_type,
        title=title,
        x_field=x_field,
        y_fields=y_fields,
        evidence_id=evidence_id,
        rows=tuple(points),
        truncated=bool(
            cut or evidence.get("truncated") or (isinstance(row_count, int) and row_count > len(rows))
        ),
    )


def build_answer_charts(evidence: Iterable[dict[str, Any]], cited_ids: Collection[str]) -> tuple[ChartSpec, ...]:
    charts: list[ChartSpec] = []
    seen: set[str] = set()
    for item in evidence:
        evidence_id = item.get("evidence_id")
        if not evidence_id or evidence_id in seen or evidence_id not in cited_ids:
            continue
        seen.add(evidence_id)
        chart = _chart_for(item)
        if chart is not None:
            charts.append(chart)
        if len(charts) == MAX_CHARTS:
            break
    return tuple(charts)


def chart_from_dict(data: dict[str, Any]) -> ChartSpec:
    return ChartSpec(
        chart_id=str(data["chart_id"]),
        chart_type=str(data["chart_type"]),
        title=str(data.get("title") or ""),
        x_field=str(data.get("x_field") or ""),
        y_fields=tuple(str(y) for y in data.get("y_fields") or ()),
        evidence_id=str(data.get("evidence_id") or ""),
        rows=tuple(tuple(r) for r in data.get("rows") or ()),
        truncated=bool(data.get("truncated")),
    )
