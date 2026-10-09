"""Explicitly consented chat copies in the private reviewer SQLite database."""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
# Default history keeps 50 exchanges (100 messages); leave headroom so a
# normal long chat can still be saved whole.
TURNS_MAX = 200
MESSAGE_MAX = 12_000
LIST_DEFAULT = 200
RETENTION_DEFAULT_DAYS = 90
PURGE_INTERVAL_SECONDS = 3_600

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS saved_chats (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    transcript_json TEXT NOT NULL
)
"""
_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_saved_chats_created ON saved_chats(created_at)
"""


class SavedChatValidationError(ValueError):
    """The stored history cannot be saved within the transcript limits."""


def build_transcript(history: list[dict]) -> list[dict[str, str]]:
    """Copy only questions, answers, and server times from trusted history.

    Reject oversized histories rather than silently saving an incomplete copy.
    """
    if not history:
        raise SavedChatValidationError("This conversation has no available history")
    if len(history) * 2 > TURNS_MAX:
        raise SavedChatValidationError(f"A copy can contain at most {TURNS_MAX} messages")
    transcript = []
    for entry in history:
        for field, role in (("user", "user"), ("assistant", "assistant")):
            content = entry.get(field)
            timestamp = entry.get("time")
            if not isinstance(content, str) or not isinstance(timestamp, str):
                raise SavedChatValidationError("This conversation's history is unavailable")
            if len(content) > MESSAGE_MAX:
                raise SavedChatValidationError(f"Messages must contain at most {MESSAGE_MAX} characters")
            transcript.append({"role": role, "content": content, "time": timestamp})
    return transcript


class SavedChatStore:
    """One consented snapshot per conversation, with issue-report retention conventions."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.retention_days = int(
                os.getenv("SAVED_CHAT_RETENTION_DAYS", str(RETENTION_DEFAULT_DAYS))
            )
            if self.retention_days <= 0:
                raise ValueError("retention must be positive")
        except (TypeError, ValueError):
            logger.warning(
                "Invalid SAVED_CHAT_RETENTION_DAYS value; using %s",
                RETENTION_DEFAULT_DAYS,
            )
            self.retention_days = RETENTION_DEFAULT_DAYS
        self._purge_lock = threading.Lock()
        self._last_purge: float | None = None
        self._initialize()
        self._purge_expired()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(_CREATE_SQL)
            connection.execute(_CREATE_INDEX_SQL)

    def _purge_expired(self) -> None:
        """Delete expired rows atomically; each worker throttles its own checks."""
        with self._purge_lock:
            now = time.monotonic()
            if (
                self._last_purge is not None
                and now - self._last_purge < PURGE_INTERVAL_SECONDS
            ):
                return
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            except OverflowError:
                # An arbitrarily large positive window expires no existing rows.
                cutoff = datetime.min.replace(tzinfo=timezone.utc)
            with self._connect() as connection:
                deleted = connection.execute(
                    "DELETE FROM saved_chats WHERE created_at < ?",
                    (cutoff.isoformat(),),
                ).rowcount
            self._last_purge = time.monotonic()
            logger.info("Deleted %s expired saved chats", deleted)

    def save(
        self, conversation_id: str, transcript: list[dict[str, str]], *, chat_id: str | None = None
    ) -> str:
        self._purge_expired()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO saved_chats (id, conversation_id, created_at, updated_at, transcript_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    transcript_json = excluded.transcript_json
                WHERE ? IS NULL OR saved_chats.id = excluded.id
                RETURNING id
                """,
                (chat_id or secrets.token_urlsafe(24), conversation_id, now, now,
                 json.dumps(transcript, ensure_ascii=False), chat_id),
            ).fetchone()
        if row is None:
            raise SavedChatValidationError("This conversation id is already in use; start a new chat")
        return str(row["id"])

    def list_chats(self, *, limit: int = LIST_DEFAULT) -> list[dict]:
        self._purge_expired()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM saved_chats ORDER BY updated_at DESC, id DESC LIMIT ?",
                (max(1, min(int(limit), LIST_DEFAULT)),),
            ).fetchall()
        return [
            {"id": row["id"], "conversation_id": row["conversation_id"],
             "created_at": row["created_at"], "updated_at": row["updated_at"],
             "transcript": json.loads(row["transcript_json"])}
            for row in rows
        ]
