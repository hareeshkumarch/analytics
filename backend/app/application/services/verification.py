import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from app.application.services.text_utils import CITATION_GROUP_RE as _CITATION, EVIDENCE_ID_RE as _EVIDENCE_ID
from app.domain.models import CriterionResult, RunOutcome

GROUNDING_CRITERION = "Every figure in the answer matches the evidence it cites"

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
_NUMBER = re.compile(
    r"(?<![\w.])([$€£₹¥]?)"
    r"(-?(?:\d{1,3}(?:,\d{3})+|[1-9]\d?(?:,\d{2})*,\d{3}|\d+)(?:\.\d+)?)"
    r"\s?(%|[KMB]\b|k\b|bn\b)?"
)
_SEGMENT = re.compile(r"(?<=[.!?;:])\s+|\n+")
_MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
YEAR_MIN, YEAR_MAX = Decimal(1900), Decimal(2100)
_YEAR_BEFORE = re.compile(
    r"\b(?:in|since|during|from|to|until|till|by|of|for|year|years|fy|cy|before|after|through|between|and|early|late|mid|calendar|fiscal)\s*$",
    re.IGNORECASE,
)
_MONTH_OR_QUARTER_BEFORE = re.compile(rf"\b(?:{_MONTHS}|q[1-4]|h[12])[\s,.-]*$", re.IGNORECASE)
_MONTH_AFTER = re.compile(rf"^\s*(?:{_MONTHS})\b", re.IGNORECASE)
_YEAR_RANGE_AFTER = re.compile(r"^\s*[-–/]\s*\d{2,4}\b")
_YEAR_RANGE_BEFORE = re.compile(r"\b(?:19|20)\d{2}\s*[-–/]\s*$")
_STRUCTURAL_BEFORE = re.compile(
    r"(?:\b(?:top|bottom|first|last|step|steps|stage|phase|rank|ranked|no\.|number|version|v|page|part|tier|level|"
    r"quarter|week|day|days|month|months)\s*#?|#)\s*$",
    re.IGNORECASE,
)
_ORDINAL_AFTER = re.compile(r"^(?:st|nd|rd|th)\b", re.IGNORECASE)
_LEADING_YEAR = re.compile(r"^(\d{4})-\d{2}")
_SCALE = {"k": Decimal(1_000), "K": Decimal(1_000), "M": Decimal(1_000_000), "B": Decimal(1_000_000_000), "bn": Decimal(1_000_000_000)}


@dataclass(frozen=True, slots=True)
class _Figure:
    text: str
    mantissa: Decimal
    decimals: int
    scale: Decimal
    percent: bool


def _plain_integer(symbol: str, suffix: str, digits: str) -> bool:
    return not (symbol or suffix or "," in digits or "." in digits)


def _is_identifier(digits: str) -> bool:
    unsigned = digits.lstrip("-")
    return len(unsigned) > 1 and unsigned.startswith("0")


def _is_year(value: Decimal, before: str, after: str) -> bool:
    return YEAR_MIN <= value <= YEAR_MAX and bool(
        _YEAR_BEFORE.search(before) or _MONTH_OR_QUARTER_BEFORE.search(before) or _YEAR_RANGE_AFTER.match(after) or _MONTH_AFTER.match(after)
    )


def _figures(segment: str) -> list[_Figure]:
    figures: list[_Figure] = []
    for match in _NUMBER.finditer(segment):
        symbol, digits, suffix = match.group(1), match.group(2), match.group(3) or ""
        before, after = segment[: match.start()], segment[match.end() :]
        if _ORDINAL_AFTER.match(after):
            continue
        mantissa = Decimal(digits.replace(",", ""))
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        if _plain_integer(symbol, suffix, digits) and (
            _is_identifier(digits) or _is_year(mantissa, before, after) or _STRUCTURAL_BEFORE.search(before) or _YEAR_RANGE_BEFORE.search(before)
        ):
            continue
        figures.append(_Figure(match.group(0).strip(), mantissa, decimals, _SCALE.get(suffix, Decimal(1)), suffix == "%"))
    return figures


def _numbers(rows: Iterable[Iterable[Any]]) -> list[Decimal]:
    found: list[Decimal] = []
    for row in rows:
        for cell in row:
            if isinstance(cell, bool) or cell is None:
                continue
            year = _LEADING_YEAR.match(str(cell))
            if year:
                found.append(Decimal(year.group(1)))
            try:
                number = Decimal(str(cell))
            except InvalidOperation:
                continue
            if number.is_finite():
                found.append(number)
    return found


def _evidence_values(item: dict[str, Any]) -> list[Decimal]:
    rows = item.get("rows") or []
    counts = {Decimal(len(rows))} | ({Decimal(int(item["row_count"]))} if isinstance(item.get("row_count"), int) else set())
    return [*_numbers(rows), *counts]


def _matches(figure: _Figure, value: Decimal) -> bool:
    quantum = Decimal(1).scaleb(-figure.decimals)
    candidates = [value, value * 100] if figure.percent else [value]
    for candidate in candidates:
        scaled = abs(candidate) / figure.scale
        if scaled.quantize(quantum) == abs(figure.mantissa).quantize(quantum):
            return True
    return False


def ungrounded_figures(markdown: str, evidence: list[dict[str, Any]]) -> list[str]:
    values_by_id = {str(e.get("evidence_id")): _evidence_values(e) for e in evidence}
    every_value = [v for values in values_by_id.values() for v in values]
    text = _LIST_MARKER.sub(" ", _DATE.sub(" ", markdown))
    missing: list[str] = []
    for segment in _SEGMENT.split(text):
        cited = _EVIDENCE_ID.findall(" ".join(_CITATION.findall(segment)))
        pool = [v for eid in cited for v in values_by_id.get(eid, [])] if cited else every_value
        for figure in _figures(_CITATION.sub(" ", segment)):
            if not any(_matches(figure, value) for value in pool):
                missing.append(figure.text)
    return list(dict.fromkeys(missing))


def assess_answer(
    *,
    answer_markdown: str,
    evidence: list[dict[str, Any]],
    criteria: Iterable[str],
    review_approved: bool | None,
    review_contradictions: bool,
    steps_failed: bool,
    stopped_early: bool,
) -> tuple[str, tuple[CriterionResult, ...]]:
    cited = tuple(dict.fromkeys(_EVIDENCE_ID.findall(answer_markdown)))
    missing = ungrounded_figures(answer_markdown, evidence)
    grounding = CriterionResult(
        GROUNDING_CRITERION,
        "failed" if missing else "verified",
        cited,
        f"Not found in the cited evidence: {', '.join(missing[:5])}" if missing else "",
    )
    incomplete = steps_failed or stopped_early
    if missing or review_contradictions:
        planned_status = "failed"
    elif review_approved is False or incomplete:
        planned_status = "blocked"
    else:
        planned_status = "verified"
    results = (grounding, *(CriterionResult(text, planned_status, cited) for text in dict.fromkeys(criteria)))
    if any(r.status == "failed" for r in results):
        outcome = RunOutcome.NEEDS_REVIEW
    elif any(r.status == "blocked" for r in results):
        outcome = RunOutcome.PARTIAL_SUCCESS
    else:
        outcome = RunOutcome.SUCCESS
    return outcome.value, results


def mentions_value(markdown: str, value: Decimal) -> bool:
    text = _LIST_MARKER.sub(" ", _DATE.sub(" ", _CITATION.sub(" ", markdown)))
    return any(_matches(figure, value) for figure in _figures(text))


def evidence_has_value(evidence: list[dict[str, Any]], value: Decimal) -> bool:
    return any(abs(number - value) < Decimal("0.005") for e in evidence for number in _numbers(e.get("rows") or []))
