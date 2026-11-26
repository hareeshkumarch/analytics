import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from app.application.services.text_utils import CITATION_GROUP_RE as _CITATION, EVIDENCE_ID_RE as _EVIDENCE_ID
from app.domain.models import CriterionResult

GROUNDING_CRITERION = "Every figure in the answer matches the evidence it cites"

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
_NUMBER = re.compile(
    r"(?<![\w.])([$€£₹¥]?)"
    r"(-?(?:\d{1,3}(?:,\d{3})+|[1-9]\d?(?:,\d{2})*,\d{3}|\d+)(?:\.\d+)?)"
    r"\s?(%|[KMB]\b|k\b|bn\b)?"
)
_SEGMENT = re.compile(r"(?<=[.!?;:])\s+|\n+")
_SCALE = {"k": Decimal(1_000), "K": Decimal(1_000), "M": Decimal(1_000_000), "B": Decimal(1_000_000_000), "bn": Decimal(1_000_000_000)}


@dataclass(frozen=True, slots=True)
class _Figure:
    text: str
    mantissa: Decimal
    decimals: int
    scale: Decimal
    percent: bool


def _figures(segment: str) -> list[_Figure]:
    figures: list[_Figure] = []
    for match in _NUMBER.finditer(segment):
        symbol, digits, suffix = match.group(1), match.group(2), match.group(3) or ""
        mantissa = Decimal(digits.replace(",", ""))
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        material = bool(symbol or suffix or "," in digits or decimals) or abs(mantissa) >= 100
        is_year = not (symbol or suffix or decimals or "," in digits) and 1900 <= mantissa <= 2100
        if material and not is_year:
            figures.append(_Figure(match.group(0).strip(), mantissa, decimals, _SCALE.get(suffix, Decimal(1)), suffix == "%"))
    return figures


def _numbers(rows: Iterable[Iterable[Any]]) -> list[Decimal]:
    found: list[Decimal] = []
    for row in rows:
        for cell in row:
            if isinstance(cell, bool) or cell is None:
                continue
            try:
                number = Decimal(str(cell))
            except InvalidOperation:
                continue
            if number.is_finite():
                found.append(number)
    return found


def _matches(figure: _Figure, value: Decimal) -> bool:
    quantum = Decimal(1).scaleb(-figure.decimals)
    candidates = [value, value * 100] if figure.percent else [value]
    for candidate in candidates:
        scaled = abs(candidate) / figure.scale
        if scaled.quantize(quantum) == abs(figure.mantissa).quantize(quantum):
            return True
    return False


def ungrounded_figures(markdown: str, evidence: list[dict[str, Any]]) -> list[str]:
    values_by_id = {str(e.get("evidence_id")): _numbers(e.get("rows") or []) for e in evidence}
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
        outcome = "needs_review"
    elif any(r.status == "blocked" for r in results):
        outcome = "partial_success"
    else:
        outcome = "success"
    return outcome, results
