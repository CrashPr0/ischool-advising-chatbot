"""Anonymized student questions stored for popular-question analysis."""

import logging
import sqlite3
from datetime import datetime

import app as app_module
import anonymized_questions
from src.anonymized_questions import (
    ensure_anonymized_questions_table,
    record_anonymized_question,
)
from src.pii_scrubber import REDACTED_EMAIL, REDACTED_STUDENT_ID


class _FakeChatbot:
    def chat(self, message, history=None, program=None):
        return {
            "user_message": message,
            "response": f"Echo: {message}",
            "source": "test",
            "confidence": 1.0,
            "retrieved_docs": [],
        }


def _rows(database):
    connection = sqlite3.connect(database)
    try:
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(anonymized_student_questions)"
            )
        ]
        stored = connection.execute(
            "SELECT created_at, question FROM anonymized_student_questions"
        ).fetchall()
        tables = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'anonymized_student_questions'
            """
        ).fetchall()
    finally:
        connection.close()
    return columns, stored, tables


def test_table_creation_is_idempotent(tmp_path):
    database = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database)
    try:
        ensure_anonymized_questions_table(connection)
        ensure_anonymized_questions_table(connection)
        connection.commit()
    finally:
        connection.close()

    record_anonymized_question("Which INFO 202 section is open?", database)
    record_anonymized_question("What is ISDA 150?", database)
    columns, stored, tables = _rows(database)

    assert tables == [("anonymized_student_questions",)]
    assert columns == ["created_at", "question"]
    assert [row[1] for row in stored] == [
        "Which INFO 202 section is open?",
        "What is ISDA 150?",
    ]


def test_record_scrubs_question_and_stores_no_identifiers(tmp_path):
    database = tmp_path / "reviews.sqlite3"
    record_anonymized_question(
        "Email ada@sjsu.edu about INFO 202. My id is 012345678.",
        database,
    )
    columns, stored, _tables = _rows(database)
    created_at, question = stored[0]

    assert columns == ["created_at", "question"]
    assert set(columns).isdisjoint(
        {"user_id", "session_id", "conversation_id", "ip", "ip_address"}
    )
    assert "ada@sjsu.edu" not in question
    assert "012345678" not in question
    assert REDACTED_EMAIL in question
    assert REDACTED_STUDENT_ID in question
    assert "INFO 202" in question
    timestamp = datetime.fromisoformat(created_at)
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset().total_seconds() == 0
    blob = database.read_bytes()
    assert b"ada@sjsu.edu" not in blob
    assert b"012345678" not in blob


def test_chat_saves_scrubbed_question_without_request_identifiers(
    monkeypatch, ready_chat_index, tmp_path
):
    database = tmp_path / "reviews.sqlite3"
    monkeypatch.setenv("EVAL_DB_PATH", str(database))
    monkeypatch.setattr(app_module, "chatbot", _FakeChatbot())
    client = app_module.app.test_client()
    conversation_id = "tab-secret-1"

    response = client.post(
        "/api/chat",
        json={
            "message": "Email ada@sjsu.edu about INFO 202 in Fall 2026.",
            "conversation_id": conversation_id,
        },
        headers={"X-Forwarded-For": "203.0.113.50"},
    )

    assert response.status_code == 200
    assert response.get_json()["response"].startswith("Echo:")
    columns, stored, _tables = _rows(database)
    assert columns == ["created_at", "question"]
    assert len(stored) == 1
    assert stored[0][1] == (
        f"Email {REDACTED_EMAIL} about INFO 202 in Fall 2026."
    )
    blob = database.read_bytes()
    assert conversation_id.encode() not in blob
    assert b"203.0.113.50" not in blob
    assert b"ada@sjsu.edu" not in blob


def test_storage_failure_does_not_break_chat(
    monkeypatch, ready_chat_index, caplog
):
    def explode(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(anonymized_questions.sqlite3, "connect", explode)
    monkeypatch.setattr(app_module, "chatbot", _FakeChatbot())
    client = app_module.app.test_client()

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/api/chat", json={"message": "Which INFO 202 section is open?"}
        )

    assert response.status_code == 200
    assert response.get_json()["response"] == "Echo: Which INFO 202 section is open?"
    assert "Could not store an anonymized student question" in caplog.text


def test_record_swallows_storage_errors(tmp_path, monkeypatch, caplog):
    import src.anonymized_questions as questions

    def explode(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(questions.sqlite3, "connect", explode)

    with caplog.at_level(logging.ERROR, logger=questions.__name__):
        questions.record_anonymized_question(
            "Which INFO 202 section is open?", tmp_path / "reviews.sqlite3"
        )

    assert "Could not store an anonymized student question" in caplog.text
