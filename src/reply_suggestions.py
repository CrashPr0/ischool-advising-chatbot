"""Optional follow-up suggestion chips for a chat reply.

The model may end its answer with one line ``SUGGESTIONS: ["...", "..."]``.
``split_reply_suggestions`` removes that line from the answer text (so it can
never be shown to a student) and returns the validated list. Anything
malformed yields an empty list. ``clean_suggestions`` applies the same rules
to a list that arrives from elsewhere, such as a chatbot result.
``scope_suggestions`` drops chips that name a different program than the one
the student selected.
"""

from __future__ import annotations

import json
import re
from typing import Any

try:
    from .pii_scrubber import scrub_pii
except ImportError:  # pragma: no cover
    from pii_scrubber import scrub_pii

MAX_SUGGESTIONS = 3
MAX_SUGGESTION_LENGTH = 80

# The marker must start a line and the array must run to the end of the reply.
_SUGGESTIONS_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:\*\*)?SUGGESTIONS(?:\*\*)?[ \t]*:[ \t]*(\[.*\])[ \t]*\Z",
    re.DOTALL,
)
_LINK_RE = re.compile(r"(?i)(?:https?|mailto|tel|ftp)\s*:|www\.|\]\(|<[^>]*>|`")
_SPACE_RE = re.compile(r"\s+")
# Explicit program names only. Generic words such as "master's" stay neutral.
_PROGRAM_NAME_RES = {
    "isda": re.compile(
        r"\b(?:bs\s*)?isda\d*\b|\bbsisda\b|"
        r"information\s+science\s+(?:and|&)\s+data\s+analytics",
        re.IGNORECASE,
    ),
    "mlis": re.compile(
        r"\bmlis\b|library\s+(?:and\s+information\s+)?science", re.IGNORECASE
    ),
    "mara": re.compile(
        r"\bmara\d*\b|archives\s+and\s+records\s+administration", re.IGNORECASE
    ),
    "msis": re.compile(r"\bmsis\b|\binfm\s*\d|informatics", re.IGNORECASE),
}


def clean_suggestions(value: Any) -> list[str]:
    """Return at most three short, unique, link-free and PII-free strings."""
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        text = _SPACE_RE.sub(" ", item).strip()
        if not text or len(text) > MAX_SUGGESTION_LENGTH:
            continue
        if _LINK_RE.search(text) or scrub_pii(text) != text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) == MAX_SUGGESTIONS:
            break
    return cleaned


def named_programs(text: Any) -> set[str]:
    """Program keys a suggestion names explicitly (``isda``, ``mlis``, ...)."""
    if not isinstance(text, str):
        return set()
    return {key for key, pattern in _PROGRAM_NAME_RES.items() if pattern.search(text)}


def scope_suggestions(suggestions: list[str], program: Any) -> list[str]:
    """Keep chips that fit the selected program.

    With ``isda``, ``mlis``, ``mara``, or ``msis`` selected, a chip that names
    only other programs is dropped. Program-neutral chips and chips that name
    the selected program stay. No selection (or ``both``) keeps every chip.
    """
    if program not in _PROGRAM_NAME_RES:
        return list(suggestions)
    kept = []
    for text in suggestions:
        named = named_programs(text)
        if named and program not in named:
            continue
        kept.append(text)
    return kept


def split_reply_suggestions(text: str) -> tuple[str, list[str]]:
    """Split a trailing ``SUGGESTIONS:`` line off ``text``.

    Returns the answer without that line and the cleaned suggestions. When
    the line is absent the text is returned unchanged.
    """
    if not isinstance(text, str):
        return text, []
    match = _SUGGESTIONS_RE.search(text.rstrip())
    if not match:
        return text, []
    answer = text.rstrip()[: match.start()].rstrip()
    try:
        parsed = json.loads(match.group(1))
    except ValueError:
        return answer, []
    return answer, clean_suggestions(parsed)
