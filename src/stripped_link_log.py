"""Durable record of links the post-generation allowlist removes.

Azure App Service keeps ``/home`` across restarts. The stream log does not.
This file uses the same place as the reviews database:
``/home/data/evaluation/stripped_links.sqlite3`` on App Service, and
``evals/stripped_links.sqlite3`` locally. It is a separate database so review
scores are left untouched.

One row is one failed check. The columns are the question from that turn,
the URLs that were removed, and whether the reply was edited in place or
replaced by the missing-context fallback. Kept links, session ids, and other
request metadata are not stored.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from .durable_storage import resolve_stripped_link_log_path
except ImportError:  # pragma: no cover
    from durable_storage import resolve_stripped_link_log_path

logger = logging.getLogger(__name__)

OUTCOME_EDITED = "edited"
OUTCOME_FALLBACK = "fallback"
_OUTCOMES = {OUTCOME_EDITED, OUTCOME_FALLBACK}

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS stripped_link_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    question TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('edited', 'fallback')),
    stripped_urls TEXT NOT NULL
)
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrippedLinkLog:
    """Append-only SQLite log of invented links removed from a model reply."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(_CREATE_SQL)

    def record(self, *, question: str, stripped_urls: list[str], outcome: str) -> None:
        """Insert one check. ``stripped_urls`` are the links that were removed."""
        if outcome not in _OUTCOMES:
            raise ValueError(f"Unknown stripped-link outcome: {outcome}")
        payload = json.dumps(list(stripped_urls), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO stripped_link_checks
                    (created_at, question, outcome, stripped_urls)
                VALUES (?, ?, ?, ?)
                """,
                (_utc_now(), question, outcome, payload),
            )

    def records(self) -> list[dict]:
        """Return every stored check, oldest first."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT created_at, question, outcome, stripped_urls
                FROM stripped_link_checks
                ORDER BY id
                """
            ).fetchall()
        return [
            {
                "created_at": row["created_at"],
                "question": row["question"],
                "outcome": row["outcome"],
                "stripped_urls": json.loads(row["stripped_urls"]),
            }
            for row in rows
        ]


def record_stripped_links(question: str, stripped_urls: list[str], outcome: str) -> None:
    """Store one failed check. A write error does not change the student reply."""
    if not stripped_urls or outcome not in _OUTCOMES:
        return
    try:
        StrippedLinkLog(resolve_stripped_link_log_path()).record(
            question=question,
            stripped_urls=list(stripped_urls),
            outcome=outcome,
        )
    except Exception:
        logger.exception("Could not write the stripped-link log")
