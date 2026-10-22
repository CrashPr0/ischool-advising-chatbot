"""Anonymized student questions for popular-question analysis.

Rows live in the reviews SQLite database (table
``anonymized_student_questions``), beside reviewer scores. Each row is the
scrubbed question text and a UTC timestamp. No user, session, conversation,
or network identifier is stored.

A write failure is logged and never raised, so chat can continue.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .pii_scrubber import scrub_pii
except ImportError:  # pragma: no cover - app.py loads src as a top-level path
    from pii_scrubber import scrub_pii

logger = logging.getLogger(__name__)

POPULAR_WINDOWS_DAYS = (7, 30, 90)
DEFAULT_POPULAR_WINDOW_DAYS = 30
_WHITESPACE = re.compile(r"\s+")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS anonymized_student_questions (
    created_at TEXT NOT NULL,
    question TEXT NOT NULL
)
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_anonymized_questions_table(connection: sqlite3.Connection) -> None:
    """Create the table if it is missing. Safe to call more than once."""
    connection.execute(_CREATE_SQL)


def record_anonymized_question(question: str, database_path: str | Path) -> None:
    """Store one scrubbed question. Failures are logged and not raised."""
    try:
        if not isinstance(question, str):
            return
        text = scrub_pii(question).strip()
        if not text:
            return
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=1)
        try:
            connection.execute("PRAGMA busy_timeout=1000")
            ensure_anonymized_questions_table(connection)
            connection.execute(
                """
                INSERT INTO anonymized_student_questions (created_at, question)
                VALUES (?, ?)
                """,
                (_utc_now(), text),
            )
            connection.commit()
        finally:
            connection.close()
    except Exception:
        logger.exception("Could not store an anonymized student question")


def collapse_question_whitespace(question: str) -> str:
    """Return the question with surrounding and repeated whitespace folded."""
    return _WHITESPACE.sub(" ", question).strip()


def question_group_key(question: str) -> str:
    """Group key: case-folded text with whitespace collapsed."""
    return collapse_question_whitespace(question).casefold()


def _parse_timestamp(value: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def popular_questions(
    database_path: str | Path,
    *,
    days: int,
    now: datetime | None = None,
) -> list[dict]:
    """Count questions asked in the window, most frequent first.

    Matching ignores capitalization and extra whitespace. The displayed
    text is the whitespace-collapsed form of the newest stored variant.
    A missing database or table returns an empty list. This function
    does not write.
    """
    if days not in POPULAR_WINDOWS_DAYS:
        raise ValueError("days must be 7, 30, or 90")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    cutoff = moment - timedelta(days=days)

    path = Path(database_path)
    if not path.is_file():
        return []
    try:
        connection = sqlite3.connect(path, timeout=1)
        try:
            connection.execute("PRAGMA busy_timeout=1000")
            rows = connection.execute("""
                SELECT created_at, question
                FROM anonymized_student_questions
                ORDER BY created_at ASC, rowid ASC
                """).fetchall()
        finally:
            connection.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        logger.exception("Could not read anonymized student questions")
        raise
    except Exception:
        logger.exception("Could not read anonymized student questions")
        raise

    groups: dict[str, dict] = {}
    for created_at, question in rows:
        if not isinstance(created_at, str) or not isinstance(question, str):
            continue
        stamp = _parse_timestamp(created_at)
        if stamp is None or stamp < cutoff:
            continue
        key = question_group_key(question)
        if not key:
            continue
        display = collapse_question_whitespace(question)
        current = groups.get(key)
        if current is None:
            groups[key] = {"stamp": stamp, "question": display, "count": 1}
            continue
        current["count"] += 1
        if stamp >= current["stamp"]:
            current["stamp"] = stamp
            current["question"] = display

    ranked = sorted(
        groups.values(),
        key=lambda item: (-item["count"], item["question"].casefold()),
    )
    return [{"question": item["question"], "count": item["count"]} for item in ranked]
