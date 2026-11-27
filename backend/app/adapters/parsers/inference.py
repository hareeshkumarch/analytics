import re
from datetime import datetime, time
from decimal import Decimal
from typing import Any, Callable, NamedTuple, Sequence, TypeVar

import pyarrow as pa

from app.domain.models import ColumnSchema

T = TypeVar("T")

DAY_FIRST = "day_first"
MONTH_FIRST = "month_first"
DATE_ORDERS = (DAY_FIRST, MONTH_FIRST)
MAX_DAY_OR_MONTH_AMBIGUITY = 12
MAX_DECIMAL_SCALE = 18
DECIMAL_SCALE_NOTE = (
    f"Some values have more than {MAX_DECIMAL_SCALE} decimal places, more than a decimal column can hold exactly, "
    "so this column was kept as text."
)
AMBIGUOUS_DATE_NOTE = (
    "Dates like 03/04/2026 could be day-first or month-first and no value settles it, so this column was kept as text. "
    "Set date_order to day_first or month_first when importing to read it as dates."
)

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


def _infer_numeric(name: str, texts: list[str | None]) -> tuple[pa.Array, ColumnSchema] | str | None:
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
    if max_scale > MAX_DECIMAL_SCALE:
        return DECIMAL_SCALE_NOTE
    scale = max(max_scale, 2)
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


def _infer_temporal(name: str, texts: list[str | None], date_order: str | None) -> tuple[pa.Array, ColumnSchema] | str | None:
    matches = _parse_each(_DATE.fullmatch, texts)
    if matches is None:
        return None
    loose = [m for m in matches if m is not None and m["first"]]
    day_first = any(int(m["first"]) > MAX_DAY_OR_MONTH_AMBIGUITY for m in loose)
    month_first = any(int(m["second"]) > MAX_DAY_OR_MONTH_AMBIGUITY for m in loose)
    if day_first and month_first:
        return None
    if loose and date_order in DATE_ORDERS:
        month_first = date_order == MONTH_FIRST
    elif loose and not day_first and not month_first:
        return AMBIGUOUS_DATE_NOTE
    try:
        moments = [None if m is None else _moment(m, month_first) for m in matches]
    except ValueError:
        return None
    if all(m is None or m.time() == time.min for m in moments):
        return _typed(name, [None if m is None else m.date() for m in moments], pa.date32())
    return _typed(name, moments, pa.timestamp("us"))


def infer_column(name: str, values: Sequence[str | None], date_order: str | None = None) -> tuple[pa.Array, ColumnSchema]:
    texts = [v.strip() if v else None for v in values]
    present = [t for t in texts if t]
    is_leading_zero_id = any(_LEADING_ZERO.fullmatch(t) for t in present)
    note: str | None = None
    if present and not is_leading_zero_id:
        numeric = _infer_numeric(name, texts)
        inferred = numeric if numeric is not None else _infer_temporal(name, texts, date_order)
        if isinstance(inferred, str):
            note = inferred
        elif inferred is not None:
            return inferred
    schema = ColumnSchema(name=name, arrow_type="string", is_leading_zero_id=is_leading_zero_id, note=note)
    return pa.array([v or None for v in values], type=pa.string()), schema


def infer_table(
    headers: Sequence[str], columns: Sequence[Sequence[str | None]], date_order: str | None = None
) -> tuple[pa.Table, tuple[ColumnSchema, ...]]:
    inferred = [infer_column(header, values, date_order) for header, values in zip(headers, columns)]
    return pa.table({schema.name: array for array, schema in inferred}), tuple(s for _, s in inferred)
