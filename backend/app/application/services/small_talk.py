"""Recognise greetings, thanks and goodbyes that carry no question about the data.

The check is deterministic and deliberately narrow: a message counts as small talk only when every
word in it belongs to a known greeting, thanks or goodbye phrase, or to a short list of filler words
("so", "much", "great", ...). Anything else, including a greeting followed by a question, stays on the
normal analysis path.
"""

import re
from typing import Any

SMALL_TALK_MAX_CHARS = 60
SMALL_TALK_MAX_WORDS = 8
_MAX_TABLES_NAMED = 3
_TABLE_NAME_CHARS = 40

_THANKS = (
    "thank you", "thank u", "thanks", "thx", "ty", "tysm", "cheers", "much appreciated", "appreciated",
    "appreciate it", "gracias", "merci", "danke",
)
_GOODBYE = ("goodbye", "good bye", "bye", "byebye", "bye bye", "see you", "see ya", "take care", "good night")
_GREETING = (
    "hi", "hii", "hello", "hey", "heya", "hiya", "howdy", "greetings", "yo", "sup", "hola", "namaste",
    "good morning", "good afternoon", "good evening", "good day", "morning",
    "how are you", "how are you doing", "how are u", "how is it going", "how's it going", "hows it going",
    "what's up", "whats up", "nice to meet you",
)
# Words that may sit around a phrase without changing what the message means ("thanks so much", "hi there").
_FILLERS = frozenset({
    "a", "again", "all", "and", "awesome", "brilliant", "buddy", "cool", "everyone", "excellent", "folks", "for",
    "friend", "good", "great", "guys", "help", "lot", "lovely", "mate", "much", "nice", "oh", "ok", "okay",
    "perfect", "really", "so", "team", "that", "the", "there", "this", "too", "very", "wonderful", "wow", "you",
    "your",
})
_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = tuple(
    sorted(
        (
            (tuple(phrase.split()), kind)
            for kind, phrases in (("thanks", _THANKS), ("goodbye", _GOODBYE), ("greeting", _GREETING))
            for phrase in phrases
        ),
        key=lambda item: -len(item[0]),
    )
)
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def small_talk_kind(text: str) -> str | None:
    """Return "thanks", "goodbye" or "greeting" when the message is only small talk, otherwise None."""
    text = (text or "").strip()
    if not text or len(text) > SMALL_TALK_MAX_CHARS:
        return None
    # Digits and words in other scripts may be data terms ("hi 2024", "hi 销售额"); leave those to the analysis.
    if any(ch.isdigit() or (ch.isalpha() and not ch.isascii()) for ch in text):
        return None
    words = _WORD.findall(text.lower().replace("’", "'"))
    if not words or len(words) > SMALL_TALK_MAX_WORDS:
        return None
    kinds: set[str] = set()
    i = 0
    while i < len(words):
        for phrase, kind in _PHRASES:
            if tuple(words[i : i + len(phrase)]) == phrase:
                kinds.add(kind)
                i += len(phrase)
                break
        else:
            if words[i] not in _FILLERS:
                return None
            i += 1
    for kind in ("thanks", "goodbye", "greeting"):
        if kind in kinds:
            return kind
    return None


def is_small_talk(text: str) -> bool:
    return small_talk_kind(text) is not None


def _table_hint(snapshot: dict[str, Any] | None) -> str:
    names = [
        str(table.get("name") or "").replace("`", "").strip()[:_TABLE_NAME_CHARS]
        for table in (snapshot or {}).get("table_details") or []
        if isinstance(table, dict)
    ]
    names = [name for name in names if name][:_MAX_TABLES_NAMED]
    if not names:
        return ""
    listed = ", ".join(f"`{name}`" for name in names)
    return f" This dataset has {listed}{' and more' if len((snapshot or {}).get('table_details') or []) > len(names) else ''}."


def small_talk_reply(text: str, snapshot: dict[str, Any] | None = None) -> str:
    """A short, fixed reply that points the user back to their data. No model call."""
    kind = small_talk_kind(text) or "greeting"
    hint = _table_hint(snapshot)
    if kind == "thanks":
        return f"You're welcome! Ask me anything else about your data.{hint}"
    if kind == "goodbye":
        return "Goodbye! Come back any time you have a question about your data."
    return (
        "Hi! I answer questions about your data, for example a total, a trend over time or a breakdown by category."
        f"{hint} What would you like to know?"
    )
