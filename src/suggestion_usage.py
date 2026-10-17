"""All-time counts of curated suggestion picks.

A row is written only when a student selects a starter chip or an
autocomplete row. It stores the curated suggestion id, the event type
(``chip`` or ``dropdown``), the selected program, and a full UTC
timestamp. It does not store typed text, an IP address, a user id, a
conversation id, or a session id.

Rows are kept indefinitely so reviewers can see how popularity moved.
There is no retention delete. A separate dedupe table stores an HMAC of
the browser session, the suggestion id, and the UTC day, so one browser
counts a suggestion at most once per day. That HMAC is not part of the
event export.

Ranking mode defaults to ``all_time``: the count is every stored pick.
No other mode is implemented. An unknown mode leaves popularity off.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import logging
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .question_suggestions import load_suggestion_catalog, suggestions_enabled
except ImportError:  # pragma: no cover - app.py loads src as a top-level path
    from question_suggestions import load_suggestion_catalog, suggestions_enabled

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_EVENT_TYPES = {"chip", "dropdown"}
_PROGRAMS = {"isda", "mlis", "both"}
_ACTIONS = {"hide", "restore", "reset"}
_RANKING_MODE = "all_time"
_STARTER_LIMIT = 4
_REASON = re.compile(r"\s+")

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS suggestion_pick_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('chip', 'dropdown')),
    program TEXT NOT NULL CHECK (program IN ('isda', 'mlis', 'both')),
    picked_at TEXT NOT NULL
)
"""
_CREATE_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_suggestion_pick_events_suggestion
ON suggestion_pick_events (suggestion_id)
"""
_CREATE_DEDUPE = """
CREATE TABLE IF NOT EXISTS suggestion_pick_dedupe (
    dedupe_key TEXT PRIMARY KEY,
    suggestion_id TEXT NOT NULL,
    day TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""
_CREATE_HIDDEN = """
CREATE TABLE IF NOT EXISTS suggestion_hidden (
    suggestion_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    hidden_at TEXT NOT NULL
)
"""
_CREATE_AUDIT = """
CREATE TABLE IF NOT EXISTS suggestion_moderation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_EVENT_COLUMNS = ("picked_at", "suggestion_id", "event_type", "program")
_process_dedupe_secret = secrets.token_bytes(32)


class SuggestionUsageError(ValueError):
    """A caller sent a suggestion id, type, program, or reason we cannot store."""


def dynamic_suggestions_enabled() -> bool:
    """Off unless DYNAMIC_SUGGESTIONS_ENABLED is an explicit true value.

    Unset, ``false``, ``0``, ``no``, ``off``, and any other value keep the
    curated starter chips and the prefix matcher unchanged.
    """
    value = os.getenv("DYNAMIC_SUGGESTIONS_ENABLED")
    if value is None:
        return False
    return value.strip().lower() in _TRUE_VALUES


def ranking_mode() -> str:
    """Configured ranking mode. Only ``all_time`` is implemented."""
    value = os.getenv("SUGGESTION_RANKING_MODE", _RANKING_MODE)
    text = str(value or "").strip().lower()
    return text or _RANKING_MODE


def ranking_uses_popularity() -> bool:
    """True only for the implemented all-time count."""
    mode = ranking_mode()
    if mode != _RANKING_MODE:
        logger.warning(
            "SUGGESTION_RANKING_MODE=%s is not implemented; popularity stays off",
            mode,
        )
        return False
    return True


def popularity_min_count() -> int:
    """Picks required before a suggestion is promoted or boosted."""
    raw = os.getenv("SUGGESTION_POPULARITY_MIN_COUNT", "3")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Invalid SUGGESTION_POPULARITY_MIN_COUNT; using 3")
        return 3
    return min(1000, max(1, value))


def _utc_now(moment: datetime | None = None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _dedupe_secret() -> bytes:
    configured = os.getenv("SESSION_SECRET_KEY", "").strip()
    if configured:
        return configured.encode("utf-8")
    return _process_dedupe_secret


def _dedupe_key(session_token: str, suggestion_id: str, day: str, secret: bytes) -> str:
    message = f"{session_token}|{suggestion_id}|{day}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _clean_reason(value: object) -> str:
    if not isinstance(value, str):
        raise SuggestionUsageError("A reason is required")
    reason = _REASON.sub(" ", value).strip()
    if len(reason) < 3 or len(reason) > 500:
        raise SuggestionUsageError("Reason must be between 3 and 500 characters")
    return reason


def choose_starters(
    questions: list[dict],
    fallback_ids: list[str],
    counts: dict[str, int],
    hidden: set[str],
    *,
    min_count: int,
    limit: int = _STARTER_LIMIT,
) -> list[dict]:
    """Pinned items, then all-time picks at the threshold, then the curated four.

    Hidden suggestions are left out. A suggestion below ``min_count`` is not
    promoted, but it can still fill an empty slot through the curated fallback.
    """
    by_id = {
        item["id"]: item
        for item in questions
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    }
    visible = [item for item in by_id.values() if item["id"] not in hidden]
    pinned = [item for item in visible if item.get("pinned") is True]
    popular = [
        item
        for item in visible
        if not item.get("pinned") and int(counts.get(item["id"], 0)) >= min_count
    ]
    popular.sort(
        key=lambda item: (-int(counts.get(item["id"], 0)), item["text"].casefold())
    )
    fallback = [
        by_id[name] for name in fallback_ids if name in by_id and name not in hidden
    ]
    chosen: list[dict] = []
    seen: set[str] = set()
    for group in (pinned, popular, fallback, visible):
        for item in group:
            if item["id"] in seen:
                continue
            chosen.append(item)
            seen.add(item["id"])
            if len(chosen) >= limit:
                return chosen
    return chosen


def popularity_counts(
    questions: list[dict], counts: dict[str, int], hidden: set[str], *, min_count: int
) -> dict[str, int]:
    """Counts the dropdown may use as a boost. Hidden and below-threshold ids are omitted."""
    allowed = {
        item["id"]
        for item in questions
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    return {
        suggestion_id: count
        for suggestion_id, count in counts.items()
        if suggestion_id in allowed
        and suggestion_id not in hidden
        and count >= min_count
    }


class SuggestionUsageStore:
    """Pick events, hide state, and moderation audit in the reviews database."""

    def __init__(
        self, database_path: str | Path, *, dedupe_secret: bytes | None = None
    ):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._dedupe_secret = (
            dedupe_secret if dedupe_secret is not None else _dedupe_secret()
        )
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_CREATE_EVENTS)
            connection.execute(_CREATE_EVENTS_INDEX)
            connection.execute(_CREATE_DEDUPE)
            connection.execute(_CREATE_HIDDEN)
            connection.execute(_CREATE_AUDIT)

    def known_ids(self, catalog: dict | None = None) -> set[str]:
        source = catalog if catalog is not None else load_suggestion_catalog()
        return {
            item["id"]
            for item in source.get("questions", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
        }

    def record_pick(
        self,
        *,
        suggestion_id: str,
        event_type: str,
        program: str,
        session_token: str,
        known_ids: set[str] | None = None,
        now: datetime | None = None,
    ) -> str:
        """Record one pick. Returns ``recorded`` or ``duplicate``.

        ``session_token`` is hashed into the dedupe key and then discarded.
        """
        if not isinstance(suggestion_id, str) or suggestion_id not in (
            known_ids if known_ids is not None else self.known_ids()
        ):
            raise SuggestionUsageError("Unknown suggestion")
        if event_type not in _EVENT_TYPES:
            raise SuggestionUsageError("Unknown suggestion event")
        if program not in _PROGRAMS:
            raise SuggestionUsageError("Unknown program")
        if not isinstance(session_token, str) or not session_token:
            raise SuggestionUsageError("Suggestion event could not be recorded")

        moment = _utc_now(now)
        picked_at = moment.isoformat()
        day = moment.date().isoformat()
        dedupe = _dedupe_key(session_token, suggestion_id, day, self._dedupe_secret)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                """
                INSERT INTO suggestion_pick_dedupe (dedupe_key, suggestion_id, day, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO NOTHING
                """,
                (dedupe, suggestion_id, day, picked_at),
            )
            if inserted.rowcount == 0:
                return "duplicate"
            connection.execute(
                """
                INSERT INTO suggestion_pick_events
                    (suggestion_id, event_type, program, picked_at)
                VALUES (?, ?, ?, ?)
                """,
                (suggestion_id, event_type, program, picked_at),
            )
        return "recorded"

    def counts(self) -> dict[str, int]:
        """All-time pick counts. Age does not change a count."""
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT suggestion_id, COUNT(*) AS picks
                FROM suggestion_pick_events
                GROUP BY suggestion_id
                """).fetchall()
        return {row["suggestion_id"]: int(row["picks"]) for row in rows}

    def hidden_rows(self) -> dict[str, dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT suggestion_id, reason, reviewer, hidden_at
                FROM suggestion_hidden
                """).fetchall()
        return {
            row["suggestion_id"]: {
                "reason": row["reason"],
                "reviewer": row["reviewer"],
                "hidden_at": row["hidden_at"],
            }
            for row in rows
        }

    def recent_audit(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT suggestion_id, action, reason, reviewer, created_at
                FROM suggestion_moderation_audit
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def moderate(
        self,
        suggestion_id: str,
        action: str,
        *,
        reason: object,
        reviewer: str,
        known_ids: set[str] | None = None,
        now: datetime | None = None,
    ) -> None:
        if action not in _ACTIONS:
            raise SuggestionUsageError("Unknown moderation action")
        allowed = known_ids if known_ids is not None else self.known_ids()
        if not isinstance(suggestion_id, str) or suggestion_id not in allowed:
            raise SuggestionUsageError("Unknown suggestion")
        clean_reason = _clean_reason(reason)
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise SuggestionUsageError("Reviewer is required")
        moment = _utc_now(now).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if action == "hide":
                connection.execute(
                    """
                    INSERT INTO suggestion_hidden
                        (suggestion_id, reason, reviewer, hidden_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(suggestion_id) DO UPDATE SET
                        reason = excluded.reason,
                        reviewer = excluded.reviewer,
                        hidden_at = excluded.hidden_at
                    """,
                    (suggestion_id, clean_reason, reviewer, moment),
                )
            elif action == "restore":
                connection.execute(
                    "DELETE FROM suggestion_hidden WHERE suggestion_id = ?",
                    (suggestion_id,),
                )
            else:
                connection.execute(
                    "DELETE FROM suggestion_pick_events WHERE suggestion_id = ?",
                    (suggestion_id,),
                )
                connection.execute(
                    "DELETE FROM suggestion_pick_dedupe WHERE suggestion_id = ?",
                    (suggestion_id,),
                )
            connection.execute(
                """
                INSERT INTO suggestion_moderation_audit
                    (suggestion_id, action, reason, reviewer, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (suggestion_id, action, clean_reason, reviewer, moment),
            )

    def events_csv(self) -> str:
        """Event export. Columns are the timestamp and the curated id, never typed text."""
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(_EVENT_COLUMNS)
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT picked_at, suggestion_id, event_type, program
                FROM suggestion_pick_events
                ORDER BY picked_at, id
                """)
            for row in rows:
                writer.writerow([row[column] for column in _EVENT_COLUMNS])
        return output.getvalue()

    def event_column_names(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "PRAGMA table_info(suggestion_pick_events)"
            ).fetchall()
        return [row["name"] for row in rows]


def student_suggestion_payload(store: SuggestionUsageStore | None = None) -> dict:
    """Curated list, plus starters and all-time counts when the dynamic flag is on.

    The payload never includes typed input. When the flag is off, or the
    ranking mode is not ``all_time``, the curated list is returned alone.
    """
    if not suggestions_enabled():
        return {"enabled": False, "questions": []}
    try:
        catalog = load_suggestion_catalog()
    except Exception:
        logger.exception("Could not load curated question suggestions")
        return {"enabled": False, "questions": []}
    payload = {"enabled": True, "questions": catalog["questions"], "dynamic": False}
    if (
        not dynamic_suggestions_enabled()
        or store is None
        or not ranking_uses_popularity()
    ):
        return payload
    try:
        counts = store.counts()
        hidden = set(store.hidden_rows())
    except Exception:
        logger.exception("Could not read suggestion pick counts")
        return payload
    minimum = popularity_min_count()
    starters = choose_starters(
        catalog["questions"],
        catalog["fallback_starter_ids"],
        counts,
        hidden,
        min_count=minimum,
    )
    payload["dynamic"] = True
    payload["ranking_mode"] = _RANKING_MODE
    payload["min_count"] = minimum
    payload["starters"] = [item["id"] for item in starters]
    payload["popularity"] = popularity_counts(
        catalog["questions"], counts, hidden, min_count=minimum
    )
    return payload


def reviewer_ranking(store: SuggestionUsageStore) -> dict[str, Any]:
    """Counts, hide state, and the starter slots a reviewer can moderate."""
    catalog = load_suggestion_catalog()
    counts = store.counts()
    hidden = store.hidden_rows()
    minimum = popularity_min_count()
    dynamic = dynamic_suggestions_enabled() and ranking_uses_popularity()
    starter_ids = []
    if dynamic:
        starter_ids = [
            item["id"]
            for item in choose_starters(
                catalog["questions"],
                catalog["fallback_starter_ids"],
                counts,
                set(hidden),
                min_count=minimum,
            )
        ]
    rows = []
    for item in catalog["questions"]:
        suggestion_id = item.get("id")
        if not suggestion_id:
            continue
        state = hidden.get(suggestion_id, {})
        rows.append(
            {
                "id": suggestion_id,
                "text": item["text"],
                "topic": item.get("topic", ""),
                "programs": item.get("programs", []),
                "pinned": item.get("pinned") is True,
                "hidden": suggestion_id in hidden,
                "hide_reason": state.get("reason", ""),
                "hidden_by": state.get("reviewer", ""),
                "count": int(counts.get(suggestion_id, 0)),
                "starter": suggestion_id in starter_ids,
            }
        )
    rows.sort(key=lambda row: (-row["count"], row["text"].casefold()))
    return {
        "enabled": dynamic_suggestions_enabled(),
        "ranking_mode": ranking_mode(),
        "min_count": minimum,
        "suggestions": rows,
        "audit": store.recent_audit(),
    }
