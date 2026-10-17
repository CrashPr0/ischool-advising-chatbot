"""Curated questions for the student chat autocomplete.

The list is staff-written. It is the topic samples already offered in the
chat page, plus course prompts taken from catalog titles in the advising
corpus. It is not the anonymized student-question log and it is not the
reviewer popular-question aggregate.

Matching runs in the browser (``static/suggestions.js``). This module only
loads the list and ranks it for tests. The HTTP route does not receive the
text the student has typed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_PROGRAMS = {"isda", "mlis", "both"}
_MAX_TEXT_LENGTH = 180
_MAX_TOPIC_LENGTH = 60
_DEFAULT_LIMIT = 6
_MAX_LIMIT = 8
_POPULARITY_CAP = 60
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

DEFAULT_SUGGESTIONS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "question_suggestions.json"
)

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'"})


def suggestions_enabled() -> bool:
    """True unless QUESTION_SUGGESTIONS_ENABLED is explicitly off.

    Unset uses the curated list. ``false``, ``0``, ``no``, ``off``, and any
    other value hide the dropdown.
    """
    value = os.getenv("QUESTION_SUGGESTIONS_ENABLED")
    if value is None:
        return True
    return value.strip().lower() in _TRUE_VALUES


def normalize_suggestion_text(value: object) -> str:
    """Lowercase, drop punctuation, and split course codes such as INFO289."""
    if not isinstance(value, str):
        return ""
    text = value.translate(_APOSTROPHES).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    return re.sub(r"\s+", " ", text).strip()


def _stem_token(token: str) -> str:
    if (
        len(token) >= 5
        and token.endswith("s")
        and not token.endswith("ss")
        and not token[-2].isdigit()
    ):
        return token[:-1]
    return token


def _tokens_match(query_token: str, candidate_token: str) -> bool:
    query_token = _stem_token(query_token)
    candidate_token = _stem_token(candidate_token)
    if query_token == candidate_token:
        return True
    if len(query_token) >= 3 and candidate_token.startswith(query_token):
        return True
    if len(candidate_token) >= 4 and query_token.startswith(candidate_token):
        return True
    shared = 0
    for left, right in zip(query_token, candidate_token):
        if left != right:
            break
        shared += 1
    shorter = min(len(query_token), len(candidate_token))
    return shared >= 5 and shorter >= 5


def _match_score(query: str, question: str) -> int:
    """Prefix and token score. Zero means the question is not a suggestion."""
    normalized_query = normalize_suggestion_text(query)
    normalized_question = normalize_suggestion_text(question)
    if len(normalized_query) < 2 or not normalized_question:
        return 0
    if normalized_query == normalized_question:
        return 1000

    query_tokens = normalized_query.split(" ")
    question_tokens = normalized_question.split(" ")
    matched = 0
    long_match = False
    for token in query_tokens:
        if any(_tokens_match(token, candidate) for candidate in question_tokens):
            matched += 1
            if len(_stem_token(token)) >= 4:
                long_match = True
    coverage_complete = matched == len(query_tokens)
    prefix = normalized_question.startswith(normalized_query)
    substring = normalized_query in normalized_question
    if not prefix and not substring and (not coverage_complete or not long_match):
        return 0

    score = (matched * 100) // len(query_tokens)
    if coverage_complete:
        score += 40
    if prefix:
        score += 240
    elif substring:
        position = normalized_question.find(normalized_query)
        score += 140 + max(0, 15 - position)
    return score


def _program_adjustment(programs: list[str], selected: str) -> int:
    if selected == "both" or "both" in programs or selected in programs:
        return 8
    return -12


def _clean_programs(value: object) -> list[str] | None:
    if value is None:
        return ["both"]
    if not isinstance(value, list) or not value:
        return None
    programs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        name = item.strip().lower()
        if name not in _PROGRAMS or name in programs:
            return None
        programs.append(name)
    return programs


def _clean_id(value: object) -> str | None:
    """Return an id, ``''`` when omitted, or None when the value is unusable."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    name = value.strip().lower()
    if not name:
        return ""
    if not 2 <= len(name) <= 64 or _ID.fullmatch(name) is None:
        return None
    return name


def _clean_flag(value: object) -> bool | None:
    if value is None:
        return False
    if not isinstance(value, bool):
        return None
    return value


def _clean_item(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    text = item.get("text")
    if not isinstance(text, str):
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > _MAX_TEXT_LENGTH or "\n" in text:
        return None
    topic = item.get("topic", "")
    if topic is None:
        topic = ""
    if not isinstance(topic, str):
        return None
    topic = re.sub(r"\s+", " ", topic).strip()
    if len(topic) > _MAX_TOPIC_LENGTH:
        return None
    programs = _clean_programs(item.get("programs"))
    if programs is None:
        return None
    suggestion_id = _clean_id(item.get("id"))
    pinned = _clean_flag(item.get("pinned"))
    if suggestion_id is None or pinned is None:
        return None
    cleaned = {"text": text, "topic": topic, "programs": programs, "pinned": pinned}
    if suggestion_id:
        cleaned["id"] = suggestion_id
    return cleaned


def _clean_id_list(value: object, known: set[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    ordered: list[str] = []
    for item in value:
        name = _clean_id(item)
        if not name or name not in known or name in ordered:
            continue
        ordered.append(name)
    return ordered


def load_suggestion_catalog(path: Path | None = None) -> dict:
    """Return curated questions and the staff fallback starter ids.

    Raises if the file is not readable. Items with a bad id, program, or
    pinned flag are skipped. Duplicate text or ids are skipped.
    """
    source = Path(path) if path is not None else DEFAULT_SUGGESTIONS_PATH
    raw = json.loads(source.read_text(encoding="utf-8"))
    items = raw.get("questions") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("question suggestions file must contain a questions list")

    loaded: list[dict] = []
    seen_text: set[str] = set()
    seen_ids: set[str] = set()
    for item in items:
        cleaned = _clean_item(item)
        if cleaned is None:
            continue
        key = normalize_suggestion_text(cleaned["text"])
        suggestion_id = cleaned.get("id", "")
        if not key or key in seen_text or suggestion_id in seen_ids:
            continue
        seen_text.add(key)
        if suggestion_id:
            seen_ids.add(suggestion_id)
        loaded.append(cleaned)
    fallback = []
    if isinstance(raw, dict):
        fallback = _clean_id_list(raw.get("fallback_starter_ids"), seen_ids)
    return {"questions": loaded, "fallback_starter_ids": fallback}


def load_question_suggestions(path: Path | None = None) -> list[dict]:
    """Return cleaned curated questions. Raises if the file is not readable."""
    return load_suggestion_catalog(path)["questions"]


def _popularity_boost(item: dict, popularity: dict | None) -> int:
    """Small reorder bonus. A zero match score never reaches this helper."""
    if not popularity or not isinstance(item, dict):
        return 0
    suggestion_id = item.get("id")
    if not isinstance(suggestion_id, str):
        return 0
    raw = popularity.get(suggestion_id, 0)
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 0
    if count <= 0:
        return 0
    return min(_POPULARITY_CAP, count)


def rank_question_suggestions(
    query: str,
    questions: list[dict],
    *,
    program: str = "both",
    limit: int = _DEFAULT_LIMIT,
    popularity: dict | None = None,
) -> list[dict]:
    """Return the best curated matches for a typed prefix. Pure, no I/O."""
    selected = program if program in _PROGRAMS else "both"
    try:
        capped = int(limit)
    except (TypeError, ValueError):
        capped = _DEFAULT_LIMIT
    capped = min(_MAX_LIMIT, max(1, capped))

    ranked: list[tuple[int, str, dict]] = []
    seen: set[str] = set()
    for item in questions:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        key = normalize_suggestion_text(text)
        if not key or key in seen:
            continue
        score = _match_score(query, text)
        if score <= 0:
            continue
        programs = (
            item.get("programs") if isinstance(item.get("programs"), list) else ["both"]
        )
        score += _program_adjustment([str(name) for name in programs], selected)
        score += _popularity_boost(item, popularity)
        if score <= 0:
            continue
        seen.add(key)
        ranked.append((score, text, item))

    ranked.sort(key=lambda row: (-row[0], row[1].casefold()))
    return [item for _score, _text, item in ranked[:capped]]


def suggestion_payload() -> dict:
    """Payload for GET /api/question-suggestions. Never includes typed input."""
    if not suggestions_enabled():
        return {"enabled": False, "questions": []}
    try:
        questions = load_question_suggestions()
    except Exception:
        logger.exception("Could not load curated question suggestions")
        return {"enabled": False, "questions": []}
    return {"enabled": True, "questions": questions}
