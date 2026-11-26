import json
import re
from typing import Any

EVIDENCE_ID_RE = re.compile(r"ev_[0-9a-f]{12}")
CITATION_GROUP_RE = re.compile(r"\[(ev_[0-9a-f]{12}(?:\s*,\s*ev_[0-9a-f]{12})*)\]")

_CONTROL_CHAR_TABLE = str.maketrans(
    "",
    "",
    "".join(chr(code) for code in (*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0x7F)),
)
_WHITESPACE_RUN_RE = re.compile(r"[\r\n\t]+")


def strip_unknown_citations(text: str, known_ids: set[str]) -> tuple[str, list[str]]:
    """Drop cited evidence ids that no tool call produced, so the UI never links to a missing record."""
    dropped: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        ids = [part.strip() for part in match.group(1).split(",")]
        kept = [i for i in ids if i in known_ids]
        dropped.extend(i for i in ids if i not in known_ids)
        return f"[{', '.join(kept)}]" if kept else ""

    return CITATION_GROUP_RE.sub(_replace, text), dropped


def clean_text(raw: Any, limit: int | None = None, *, strip: bool = True) -> str:
    """Strip disallowed ASCII control characters while preserving tabs/newlines."""
    cleaned = str(raw or "").translate(_CONTROL_CHAR_TABLE)
    if strip:
        cleaned = cleaned.strip()
    return cleaned[:limit] if limit is not None else cleaned


def clean_single_line(raw: Any, limit: int = 300) -> str:
    """Strip control characters and collapse newlines/tabs into single spaces."""
    cleaned = str(raw or "").translate(_CONTROL_CHAR_TABLE).strip()
    return _WHITESPACE_RUN_RE.sub(" ", cleaned).strip()[:limit]


def _strip_json_info_tag(text: str) -> str:
    stripped = text.lstrip()
    if stripped[:4].lower() == "json" and (len(stripped) == 4 or stripped[4].isspace() or stripped[4] == "{"):
        return stripped[4:].lstrip()
    return stripped


def _extract_fenced_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    current: list[str] = []

    for line in text.splitlines():
        stripped = line.lstrip()
        if not in_fence:
            if not (stripped.startswith("```") or stripped.startswith("~~~")):
                continue
            ch = stripped[0]
            n = len(stripped) - len(stripped.lstrip(ch))
            if n < 3:
                continue
            after = stripped[n:].strip()
            closing = ch * n
            if after.endswith(closing) and len(after) > len(closing):
                inner = _strip_json_info_tag(after[: -len(closing)].strip())
                if inner:
                    blocks.append(inner)
                continue
            in_fence = True
            fence_char = ch
            fence_len = n
            first_body = _strip_json_info_tag(after)
            current = [first_body] if first_body.startswith("{") else []
        else:
            closing = fence_char * fence_len
            if stripped.startswith(closing) and stripped.strip(fence_char).strip() == "":
                body = "\n".join(current).strip()
                if body:
                    blocks.append(body)
                in_fence = False
                current = []
            else:
                current.append(line)

    if in_fence and current:
        body = "\n".join(current).strip()
        if body:
            blocks.append(body)
    return blocks


def _decode_first_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def extract_json_object(text: str, *, error_message: str = "Expected a JSON object") -> dict[str, Any]:
    """Extract the first JSON object from fenced markdown blocks or raw text."""
    cleaned = text.strip()
    if "```" in cleaned or "~~~" in cleaned:
        for block in _extract_fenced_blocks(cleaned):
            parsed = _decode_first_object(block)
            if parsed is not None:
                return parsed
    parsed = _decode_first_object(cleaned)
    if parsed is not None:
        return parsed
    raise ValueError(error_message)

