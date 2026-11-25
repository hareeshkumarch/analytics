import re
from datetime import datetime, time
from decimal import Decimal
from typing import Any, Callable, NamedTuple, Sequence, TypeVar

import pyarrow as pa

from app.domain.models import ColumnSchema

T = TypeVar("T")

_LEADING_ZERO = re.compile(r"0[0-9]+")
_GROUPED_INTEGER = r"\d+|[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d?(?:,\d{2})*,\d{3}"
_AMOUNT = re.compile(
    rf"(?P<sign>-?)(?P<currency>(?:₹|rs\.?|inr)\s*)?(?P<inner_sign>-?)"
    rf"(?P<integer>{_GROUPED_INTEGER})(?:\.(?P<fraction>\d+))?",
    re.IGNORECASE,
)
_ACCOUNTING_NEGATIVE = re.compile(r"\((?P<body>[^()]*)\)")
_DATE = re.compile(
    r"(?:(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"|(?P<first>\d{1,2})(?P<sep>[/.-])(?P<second>\d{1,2})(?P=sep)(?P<loose_year>\d{4}))"
    r"(?:[T ](?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<sec>\d{2})(?:\.(?P<micros>\d{1,6}))?)?)?"
)


class _Amount(NamedTuple):
    value: Decimal
    scale: int
    has_currency: bool


def _parse_amount(text: str) -> _Amount | None:
    accounting = _ACCOUNTING_NEGATIVE.fullmatch(text)
    match = _AMOUNT.fullmatch(accounting["body"].strip() if accounting else text)
    if match is None or len(match["sign"] + match["inner_sign"]) + bool(accounting) > 1:
        return None
    fraction = match["fraction"] or ""
    value = Decimal(match["integer"].replace(",", "") + "." + (fraction or "0"))
    negative = bool(match["sign"] or match["inner_sign"] or accounting)
    return _Amount(-value if negative else value, len(fraction), match["currency"] is not None)


def _moment(match: re.Match[str], month_first: bool) -> datetime:
    if match["year"]:
        year, month, day = int(match["year"]), int(match["month"]), int(match["day"])
    else:
        first, second = int(match["first"]), int(match["second"])
        year = int(match["loose_year"])
        month, day = (first, second) if month_first else (second, first)
    micros = int((match["micros"] or "").ljust(6, "0"))
    clock = (int(match["hour"] or 0), int(match["minute"] or 0), int(match["sec"] or 0), micros)
    return datetime(year, month, day, *clock)


def _parse_each(parse: Callable[[str], T | None], texts: list[str | None]) -> list[T | None] | None:
    parsed: list[T | None] = []
    for text in texts:
        value = parse(text) if text else None
        if text and value is None:
            return None
        parsed.append(value)
    return parsed


def _typed(name: str, values: list[Any], arrow_type: pa.DataType) -> tuple[pa.Array, ColumnSchema]:
    return pa.array(values, type=arrow_type), ColumnSchema(name=name, arrow_type=str(arrow_type))


def _infer_numeric(name: str, texts: list[str | None]) -> tuple[pa.Array, ColumnSchema] | None:
    amounts = _parse_each(_parse_amount, texts)
    if amounts is None:
        return None
    present = [a for a in amounts if a is not None]
    currency = "INR" if any(a.has_currency for a in present) else None
    max_scale = max(a.scale for a in present)
    values = [None if a is None else a.value for a in amounts]
    if max_scale == 0 and currency is None:
        try:
            return _typed(name, [None if v is None else int(v) for v in values], pa.int64())
        except (pa.ArrowInvalid, OverflowError):
            pass
    scale = min(max(max_scale, 2), 18)
    try:
        array = pa.array(values, type=pa.decimal128(38, scale))
    except (pa.ArrowInvalid, OverflowError):
        return None
    schema = ColumnSchema(
        name=name,
        arrow_type=f"decimal128(38,{scale})",
        currency_code=currency,
        decimal_precision=38,
        decimal_scale=scale,
    )
    return array, schema


def _infer_temporal(name: str, texts: list[str | None]) -> tuple[pa.Array, ColumnSchema] | None:
    matches = _parse_each(_DATE.fullmatch, texts)
    if matches is None:
        return None
    loose = [m for m in matches if m is not None and m["first"]]
    day_first = any(int(m["first"]) > 12 for m in loose)
    month_first = any(int(m["second"]) > 12 for m in loose)
    if day_first and month_first:
        return None
    try:
        moments = [None if m is None else _moment(m, month_first) for m in matches]
    except ValueError:
        return None
    if all(m is None or m.time() == time.min for m in moments):
        return _typed(name, [None if m is None else m.date() for m in moments], pa.date32())
    return _typed(name, moments, pa.timestamp("us"))


def infer_column(name: str, values: Sequence[str | None]) -> tuple[pa.Array, ColumnSchema]:
    texts = [v.strip() if v else None for v in values]
    present = [t for t in texts if t]
    is_leading_zero_id = any(_LEADING_ZERO.fullmatch(t) for t in present)
    if present and not is_leading_zero_id:
        inferred = _infer_numeric(name, texts) or _infer_temporal(name, texts)
        if inferred is not None:
            return inferred
    schema = ColumnSchema(name=name, arrow_type="string", is_leading_zero_id=is_leading_zero_id)
    return pa.array([v or None for v in values], type=pa.string()), schema


def infer_table(
    headers: Sequence[str], columns: Sequence[Sequence[str | None]]
) -> tuple[pa.Table, tuple[ColumnSchema, ...]]:
    inferred = [infer_column(header, values) for header, values in zip(headers, columns)]
    return pa.table({schema.name: array for array, schema in inferred}), tuple(s for _, s in inferred)
