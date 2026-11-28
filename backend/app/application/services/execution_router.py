import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.application.services.small_talk import is_small_talk
from app.domain.models import RunMode

MAX_DIRECT_QUESTION_CHARS = 200
MAX_DIRECT_QUESTION_WORDS = 30
_MULTI_STEP = re.compile(
    r"\b(compar\w*|versus|vs\.?|against|between|differen\w*|chang\w*|growth|grew|trend\w*|over time|why|"
    r"reconcil\w*|correlat\w*|forecast\w*|predict\w*|explain\w*|caus\w*|year over year|month over month|yoy|mom|"
    r"and then|also)\b",
    re.IGNORECASE,
)


class ExecutionRoute(str, Enum):
    # A greeting, thanks or goodbye: answered with a fixed reply, no model or tool calls.
    SMALL_TALK = "small_talk"
    DIRECT_QUERY = "direct_query"
    STANDARD = "standard"
    DEEP = "deep"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: ExecutionRoute
    reason: str


def choose_route(
    *,
    question: str,
    mode: RunMode,
    snapshot: dict[str, Any] | None,
    has_prior_turns: bool,
    has_clarification: bool,
) -> RouteDecision:
    # Checked before the mode: a greeting needs no investigation, Quick or Deep. A clarification means the
    # run is already part-way through answering a real question, so it never counts as small talk.
    if not has_clarification and is_small_talk(question):
        return RouteDecision(ExecutionRoute.SMALL_TALK, "A greeting or thanks with no question about the data.")
    if mode == RunMode.DEEP:
        return RouteDecision(ExecutionRoute.DEEP, "Deep mode was requested.")
    tables = (snapshot or {}).get("table_details") or []
    text = question.strip()
    if len(tables) != 1:
        return RouteDecision(ExecutionRoute.STANDARD, "The dataset has no single table to query directly.")
    if has_prior_turns or has_clarification:
        return RouteDecision(ExecutionRoute.STANDARD, "The question depends on earlier turns or a clarification.")
    if len(text) > MAX_DIRECT_QUESTION_CHARS or len(text.split()) > MAX_DIRECT_QUESTION_WORDS or text.count("?") > 1:
        return RouteDecision(ExecutionRoute.STANDARD, "The question is long or asks several things.")
    if any(ch.isalpha() and not ch.isascii() for ch in text):
        return RouteDecision(ExecutionRoute.STANDARD, "The question is not in English, so a single query step can't be assumed.")
    match = _MULTI_STEP.search(text)
    if match:
        return RouteDecision(ExecutionRoute.STANDARD, f"The question asks for analysis beyond one query ({match.group(0).lower()!r}).")
    return RouteDecision(ExecutionRoute.DIRECT_QUERY, "One table and a single, short question: answered with one query step.")
