"""Anonymized student questions for popular-question analysis.

Rows live in the reviews SQLite database (table
``anonymized_student_questions``), beside reviewer scores. Each row is the
scrubbed question text and a UTC timestamp. No user, session, conversation,
or network identifier is stored.

A write failure is logged and never raised, so chat can continue.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from .pii_scrubber import scrub_pii
except ImportError:  # pragma: no cover - app.py loads src as a top-level path
    from pii_scrubber import scrub_pii

logger = logging.getLogger(__name__)

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
