"""Student reports of a wrong or made-up advising answer.

Rows live in the reviews SQLite database (table ``answer_issue_reports``),
next to reviewer scores. On Azure that file is
``/home/data/evaluation/reviews.sqlite3``. Locally it is
``evals/reviews.sqlite3``.

A report always stores the reason, optional comment, and the flagged
question and answer. The conversation id, model, prior turns, and links
are stored only when the student opts in. Nothing in this module sends
email or calls another service.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

REASON_INCORRECT = "incorrect_info"
REASON_MADE_UP = "made_up"
REASON_OUTDATED = "outdated"
REASON_OTHER = "other"
REASON_KNOWLEDGE_GAP = "knowledge_gap"

REASONS: tuple[tuple[str, str], ...] = (
    (REASON_INCORRECT, "Incorrect information"),
    (REASON_MADE_UP, "Made-up link or course"),
    (REASON_OUTDATED, "Outdated information"),
    (REASON_OTHER, "Other"),
    (REASON_KNOWLEDGE_GAP, "Knowledge gap"),
)
REASON_LABELS: dict[str, str] = dict(REASONS)
_REASON_KEYS = set(REASON_LABELS)

QUESTION_MAX = 4_000
ANSWER_MAX = 12_000
COMMENT_MAX = 1_000
TURN_MAX = 4_000
TURNS_MAX = 40
LABEL_MAX = 120
CITATION_MAX = 40
LIST_DEFAULT = 200
RETENTION_DEFAULT_DAYS = 90
PURGE_INTERVAL_SECONDS = 3_600

_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:/+-]{1,120}$")
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_BARE_LINK = re.compile(
    r"(?i)(?<![\w/[(])((?:https?://|mailto:)[^\s<>\"']+)"
)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS answer_issue_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (
        reason IN ('incorrect_info', 'made_up', 'outdated', 'other', 'knowledge_gap')
    ),
    comment TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    include_conversation INTEGER NOT NULL CHECK (include_conversation IN (0, 1)),
    conversation_id TEXT,
    provider TEXT,
    model TEXT,
    turns_json TEXT,
    citations_json TEXT
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_answer_issue_reports_created
ON answer_issue_reports(created_at)
"""


class IssueReportValidationError(ValueError):
    """Raised when a student report does not satisfy the report contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_multiline(value: object, *, limit: int, field: str, required: bool) -> str:
    if not isinstance(value, str):
        raise IssueReportValidationError(f"{field} must be a string")
    text = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if required and not text:
        raise IssueReportValidationError(f"{field} is required")
    if len(text) > limit:
        raise IssueReportValidationError(
            f"{field} must contain no more than {limit} characters"
        )
    return text


def _clean_comment(value: object) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise IssueReportValidationError("comment must be a string")
    text = value.replace("\x00", "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > COMMENT_MAX:
        raise IssueReportValidationError(
            f"comment must contain no more than {COMMENT_MAX} characters"
        )
    return text


def _client_label(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or _LABEL_RE.fullmatch(value.strip()) is None:
        raise IssueReportValidationError(f"{field} is invalid")
    return value.strip()[:LABEL_MAX]


def _trusted_label(value: str) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return None
    return text[:LABEL_MAX]


def extract_answer_links(*texts: str) -> list[str]:
    """Return http(s) and mailto links that actually appear in the text.

    Client-supplied citation lists are ignored. Only links written in the
    stored answer or an opted-in turn are kept.
    """
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        raw_urls: list[str] = []
        raw_urls.extend(match.group(1) for match in _MARKDOWN_LINK.finditer(text))
        raw_urls.extend(match.group(1) for match in _BARE_LINK.finditer(text))
        for raw in raw_urls:
            url = raw.rstrip(".,;:!?)]}>\"'")
            lowered = url.lower()
            if not lowered.startswith(("http://", "https://", "mailto:")):
                continue
            if len(url) > 500:
                url = url[:500]
            if url in seen:
                continue
            seen.add(url)
            found.append(url)
            if len(found) >= CITATION_MAX:
                return found
    return found


def _normalize_turns(value: object) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise IssueReportValidationError("turns must be a list")
    if len(value) > TURNS_MAX:
        raise IssueReportValidationError(f"turns must contain at most {TURNS_MAX} messages")
    turns: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise IssueReportValidationError("each turn must be an object")
        role = item.get("role")
        if role == "bot":
            role = "assistant"
        if role not in {"user", "assistant"}:
            raise IssueReportValidationError("turn role must be user or assistant")
        text = _clean_multiline(item.get("text"), limit=TURN_MAX, field="turn text", required=False)
        if not text:
            continue
        turns.append({"role": role, "text": text})
    return turns


def build_issue_report(
    payload: object,
    *,
    history_lookup: Callable[[str | None, str, str], tuple[str, str]] | None = None,
    fallback_provider: str = "",
    fallback_model: str = "",
) -> dict[str, Any]:
    """Validate one report and drop every transcript field the student opted out of.

    ``history_lookup`` may return the provider and model this server recorded
    for the matching turn. Those values replace anything the browser sends.
    ``fallback_*`` is the process's current model, used only when the student
    opted in and neither the history nor the browser named one.
    """
    if not isinstance(payload, dict):
        raise IssueReportValidationError("Request body must be a JSON object")

    reason = payload.get("reason")
    if not isinstance(reason, str) or reason not in _REASON_KEYS:
        raise IssueReportValidationError(
            "reason must be one of: " + ", ".join(REASON_LABELS)
        )

    if "include_conversation" not in payload:
        include = False
    else:
        include = payload.get("include_conversation")
        if not isinstance(include, bool):
            raise IssueReportValidationError("include_conversation must be a boolean")

    knowledge_gap = reason == REASON_KNOWLEDGE_GAP
    if knowledge_gap:
        include = False  # Gap reports contain student questions, never bot replies.

    question = _clean_multiline(
        payload.get("question"),
        limit=QUESTION_MAX,
        field="question",
        required=not knowledge_gap,
    )
    answer = _clean_multiline(
        "" if knowledge_gap else payload.get("answer"),
        limit=ANSWER_MAX,
        field="answer",
        required=not knowledge_gap,
    )
    comment = _clean_comment(payload.get("comment"))

    record: dict[str, Any] = {
        "reason": reason,
        "comment": comment,
        "question": question,
        "answer": answer,
        "include_conversation": include,
        "conversation_id": None,
        "provider": None,
        "model": None,
        "turns": None,
        "citations": None,
    }
    if not include:
        return record

    conversation_id = payload.get("conversation_id")
    if conversation_id not in (None, ""):
        if (
            not isinstance(conversation_id, str)
            or _CONVERSATION_ID.fullmatch(conversation_id) is None
        ):
            raise IssueReportValidationError("conversation_id is invalid")
        record["conversation_id"] = conversation_id

    turns = _normalize_turns(payload.get("turns"))
    record["turns"] = turns

    provider = ""
    model = ""
    if history_lookup is not None:
        looked_up = history_lookup(record["conversation_id"], question, answer)
        if isinstance(looked_up, tuple) and len(looked_up) == 2:
            provider = str(looked_up[0] or "")
            model = str(looked_up[1] or "")
    if not provider and "provider" in payload:
        provider = _client_label(payload.get("provider"), "provider") or ""
    if not model and "model" in payload:
        model = _client_label(payload.get("model"), "model") or ""
    if not provider:
        provider = _trusted_label(fallback_provider) or ""
    if not model:
        model = _trusted_label(fallback_model) or ""
    record["provider"] = provider or None
    record["model"] = model or None

    citation_texts = [answer]
    citation_texts.extend(turn["text"] for turn in turns if turn["role"] == "assistant")
    record["citations"] = extract_answer_links(*citation_texts)
    return record


class IssueReportStore:
    """Reports in the reviews SQLite database, with automatic expiration."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.retention_days = int(
                os.getenv("ISSUE_REPORT_RETENTION_DAYS", str(RETENTION_DEFAULT_DAYS))
            )
            if self.retention_days <= 0:
                raise ValueError("retention must be positive")
        except (TypeError, ValueError):
            logger.warning(
                "Invalid ISSUE_REPORT_RETENTION_DAYS value; using %s",
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
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_CREATE_SQL)
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'answer_issue_reports'"
            ).fetchone()[0]
            if "'knowledge_gap'" not in schema:
                # SQLite cannot alter a CHECK constraint. Rebuild atomically while
                # preserving report ids, retention timestamps, and all prior rows.
                connection.execute(
                    "ALTER TABLE answer_issue_reports RENAME TO answer_issue_reports_legacy"
                )
                connection.execute(_CREATE_SQL)
                connection.execute(
                    "INSERT INTO answer_issue_reports SELECT * FROM answer_issue_reports_legacy"
                )
                connection.execute("DROP TABLE answer_issue_reports_legacy")
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
                    "DELETE FROM answer_issue_reports WHERE created_at < ?",
                    (cutoff.isoformat(),),
                ).rowcount
            self._last_purge = time.monotonic()
            logger.info("Deleted %s expired issue reports", deleted)

    def save(self, record: dict[str, Any]) -> int:
        """Insert one validated record. Returns the new row id."""
        self._purge_expired()
        include = 1 if record.get("include_conversation") else 0
        turns = record.get("turns")
        citations = record.get("citations")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO answer_issue_reports (
                    created_at, reason, comment, question, answer,
                    include_conversation, conversation_id, provider, model,
                    turns_json, citations_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utc_now(),
                    record["reason"],
                    record.get("comment") or "",
                    record["question"],
                    record["answer"],
                    include,
                    record.get("conversation_id") if include else None,
                    record.get("provider") if include else None,
                    record.get("model") if include else None,
                    json.dumps(turns, ensure_ascii=False)
                    if include and turns is not None
                    else None,
                    json.dumps(citations, ensure_ascii=False)
                    if include and citations is not None
                    else None,
                ),
            )
            return int(cursor.lastrowid)

    def get(self, report_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM answer_issue_reports WHERE id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_report(row)

    def reason_counts(self) -> dict[str, Any]:
        """Count all retained reports, with ties in the order defined by REASONS."""
        self._purge_expired()
        with self._connect() as connection:
            counts = dict(
                connection.execute(
                    "SELECT reason, COUNT(*) FROM answer_issue_reports GROUP BY reason"
                ).fetchall()
            )
        reasons = [
            {"key": key, "label": label, "count": counts.get(key, 0)}
            for key, label in REASONS
        ]
        reasons.sort(key=lambda reason: -reason["count"])
        return {"total": sum(reason["count"] for reason in reasons), "reasons": reasons}

    def list_reports(
        self, *, limit: int = LIST_DEFAULT, reason: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the newest reports, capped so one response stays bounded."""
        if reason is not None and reason not in REASON_LABELS:
            raise IssueReportValidationError("Unknown report reason")
        self._purge_expired()
        capped = max(1, min(int(limit), LIST_DEFAULT))
        where = " WHERE reason = ?" if reason is not None else ""
        parameters = (reason, capped) if reason is not None else (capped,)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM answer_issue_reports" + where + " ORDER BY id DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [_row_to_report(row) for row in rows]


def _row_to_report(row: sqlite3.Row) -> dict[str, Any]:
    include = bool(row["include_conversation"])
    turns = None
    citations = None
    if include and row["turns_json"]:
        parsed_turns = json.loads(row["turns_json"])
        turns = parsed_turns if isinstance(parsed_turns, list) else []
    if include and row["citations_json"]:
        parsed_citations = json.loads(row["citations_json"])
        citations = parsed_citations if isinstance(parsed_citations, list) else []
    elif include:
        citations = []
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "reason": row["reason"],
        "reason_label": REASON_LABELS.get(row["reason"], row["reason"]),
        "comment": row["comment"],
        "question": row["question"],
        "answer": row["answer"],
        "include_conversation": include,
        "conversation_id": row["conversation_id"] if include else None,
        "provider": row["provider"] if include else None,
        "model": row["model"] if include else None,
        "turns": turns if include else None,
        "citations": citations if include else None,
    }
