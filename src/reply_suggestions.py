"""Optional follow-up suggestion chips for a chat reply.

The model may end its answer with one line ``SUGGESTIONS: ["...", "..."]``.
``split_reply_suggestions`` removes that line from the answer text (so it can
never be shown to a student) and returns the validated list. Anything
malformed yields an empty list. ``clean_suggestions`` applies the same rules
to a list that arrives from elsewhere, such as a chatbot result.
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
