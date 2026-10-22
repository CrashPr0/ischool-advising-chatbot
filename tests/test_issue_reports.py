"""Student answer reports: validation, durable storage, and reviewer access."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import app as app_module
import src.issue_reports as issue_reports
from src.evaluation_store import EvaluationStore
from src.issue_reports import (
    IssueReportStore,
    IssueReportValidationError,
    build_issue_report,
)


def _report(**overrides):
    payload = {
        "reason": "incorrect_info",
        "comment": "The course number looks wrong.",
        "question": "Which course is required?",
        "answer": "Take INFO 999, which is not a real course.",
        "include_conversation": False,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def retention_clock(monkeypatch):
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    clock = {"now": now, "monotonic": 100.0}

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(issue_reports, "datetime", FrozenDatetime)
    monkeypatch.setattr(issue_reports.time, "monotonic", lambda: clock["monotonic"])
    monkeypatch.delenv("ISSUE_REPORT_RETENTION_DAYS", raising=False)
    return clock


def _dated_report(store, created_at, *, include_conversation=False):
    report_id = store.save(
        build_issue_report(
            _report(
                include_conversation=include_conversation,
                turns=[{"role": "user", "text": "private earlier question"}],
            )
        )
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE answer_issue_reports SET created_at = ? WHERE id = ?",
            (created_at.isoformat(), report_id),
        )
    return report_id


def test_retention_purges_on_startup_and_keeps_recent_and_boundary_rows(
    tmp_path, retention_clock, caplog, monkeypatch
):
    monkeypatch.setenv("ISSUE_REPORT_RETENTION_DAYS", "90")
    database = tmp_path / "reviews.sqlite3"
    store = IssueReportStore(database)
    now = retention_clock["now"]
    old = _dated_report(store, now - timedelta(days=91), include_conversation=True)
    recent = _dated_report(store, now - timedelta(days=89))
    boundary = _dated_report(store, now - timedelta(days=90))

    with caplog.at_level(logging.INFO, logger=issue_reports.__name__):
        restarted = IssueReportStore(database)

    assert restarted.retention_days == 90
    assert restarted.get(old) is None
    assert {row["id"] for row in restarted.list_reports()} == {recent, boundary}
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT turns_json FROM answer_issue_reports WHERE id = ?", (old,)
        ).fetchone() is None
    assert "Deleted 1 expired issue reports" in caplog.text
    assert "private earlier question" not in caplog.text


@pytest.mark.parametrize("operation", ["list", "write", "counts"])
def test_retention_throttles_then_purges_on_list_or_write(
    tmp_path, retention_clock, operation, caplog, monkeypatch
):
    monkeypatch.setenv("ISSUE_REPORT_RETENTION_DAYS", "90")
    store = IssueReportStore(tmp_path / "reviews.sqlite3")
    old = _dated_report(store, retention_clock["now"] - timedelta(days=91))
    recent = _dated_report(store, retention_clock["now"] - timedelta(days=1))

    def use_store():
        if operation == "list":
            return store.list_reports()
        if operation == "counts":
            return store.reason_counts()
        store.save(build_issue_report(_report()))

    with caplog.at_level(logging.INFO, logger=issue_reports.__name__):
        retention_clock["monotonic"] += 3_599
        use_store()
        use_store()
        assert store.get(old) is not None
        assert "expired issue reports" not in caplog.text

        retention_clock["monotonic"] += 1
        result = use_store()
        assert store.get(old) is None
        assert store.get(recent) is not None
        if operation == "list":
            assert [row["id"] for row in result] == [recent]
        elif operation == "counts":
            assert result["total"] == 1
            assert result["reasons"][0]["count"] == 1
        use_store()
        assert caplog.text.count("Deleted 1 expired issue reports") == 1
        assert caplog.text.count("expired issue reports") == 1


def test_retention_env_override(tmp_path, monkeypatch, retention_clock):
    database = tmp_path / "reviews.sqlite3"
    store = IssueReportStore(database)
    old = _dated_report(store, retention_clock["now"] - timedelta(days=8))
    recent = _dated_report(store, retention_clock["now"] - timedelta(days=6))
    monkeypatch.setenv("ISSUE_REPORT_RETENTION_DAYS", "7")

    restarted = IssueReportStore(database)

    assert restarted.retention_days == 7
    assert restarted.get(old) is None
    assert restarted.get(recent) is not None


@pytest.mark.parametrize("value", [None, "", "0", "00"])
def test_retention_unset_empty_or_zero_deletes_nothing(
    tmp_path, monkeypatch, retention_clock, caplog, value
):
    database = tmp_path / "reviews.sqlite3"
    store = IssueReportStore(database)
    old = _dated_report(store, retention_clock["now"] - timedelta(days=91))
    recent = _dated_report(store, retention_clock["now"] - timedelta(days=1))
    if value is None:
        monkeypatch.delenv("ISSUE_REPORT_RETENTION_DAYS", raising=False)
    else:
        monkeypatch.setenv("ISSUE_REPORT_RETENTION_DAYS", value)

    with caplog.at_level(logging.WARNING, logger=issue_reports.__name__):
        restarted = IssueReportStore(database)

    assert restarted.retention_days is None
    assert restarted.get(old) is not None
    assert restarted.get(recent) is not None
    assert "Invalid ISSUE_REPORT_RETENTION_DAYS" not in caplog.text
    assert "expired issue reports" not in caplog.text


def test_retention_positive_integer_deletes_older_reports(
    tmp_path, monkeypatch, retention_clock
):
    database = tmp_path / "reviews.sqlite3"
    store = IssueReportStore(database)
    old = _dated_report(store, retention_clock["now"] - timedelta(days=8))
    recent = _dated_report(store, retention_clock["now"] - timedelta(days=6))
    monkeypatch.setenv("ISSUE_REPORT_RETENTION_DAYS", "7")

    restarted = IssueReportStore(database)

    assert restarted.retention_days == 7
    assert restarted.get(old) is None
    assert restarted.get(recent) is not None


@pytest.mark.parametrize("value", ["abc", "1.5", "-1", "no"])
def test_retention_invalid_env_deletes_nothing(
    tmp_path, monkeypatch, retention_clock, caplog, value
):
    database = tmp_path / "reviews.sqlite3"
    store = IssueReportStore(database)
    old = _dated_report(store, retention_clock["now"] - timedelta(days=91))
    recent = _dated_report(store, retention_clock["now"] - timedelta(days=89))
    monkeypatch.setenv("ISSUE_REPORT_RETENTION_DAYS", value)

    with caplog.at_level(logging.WARNING, logger=issue_reports.__name__):
        restarted = IssueReportStore(database)

    assert restarted.retention_days is None
    assert restarted.get(old) is not None
    assert restarted.get(recent) is not None
    assert "Invalid ISSUE_REPORT_RETENTION_DAYS" in caplog.text
    assert "keeping reports indefinitely" in caplog.text


def test_opt_out_drops_transcript_fields_before_they_are_stored(tmp_path):
    record = build_issue_report(
        _report(
            include_conversation=False,
            conversation_id="tab-secret-1",
            provider="openai",
            model="gpt-secret",
            turns=[{"role": "user", "text": "private earlier question"}],
            citations=["https://evil.example/not-stored"],
        )
    )
    store = IssueReportStore(tmp_path / "reviews.sqlite3")
    report_id = store.save(record)
    stored = store.get(report_id)

    assert stored["include_conversation"] is False
    assert stored["question"] == "Which course is required?"
    assert stored["answer"].startswith("Take INFO 999")
    assert stored["comment"] == "The course number looks wrong."
    assert stored["conversation_id"] is None
    assert stored["provider"] is None
    assert stored["model"] is None
    assert stored["turns"] is None
    assert stored["citations"] is None

    connection = sqlite3.connect(tmp_path / "reviews.sqlite3")
    row = connection.execute(
        """
        SELECT conversation_id, provider, model, turns_json, citations_json
        FROM answer_issue_reports WHERE id = ?
        """,
        (report_id,),
    ).fetchone()
    connection.close()
    assert row == (None, None, None, None, None)


def test_opt_in_stores_transcript_model_and_only_links_present_in_the_text(tmp_path):
    answer = (
        "See the [catalog](https://catalog.sjsu.edu/isda) and "
        "https://www.sjsu.edu/ischool/."
    )
    record = build_issue_report(
        _report(
            reason="made_up",
            include_conversation=True,
            conversation_id="tab-alpha-1",
            provider="openai",
            model="gpt-test-report",
            comment="",
            answer=answer,
            turns=[
                {"role": "user", "text": "What came before?"},
                {"role": "assistant", "text": "Earlier note with https://sjsu.edu/advising"},
                {"role": "user", "text": "Which course is required?"},
                {"role": "bot", "text": answer},
            ],
            citations=["https://evil.example/not-in-the-answer"],
        ),
        history_lookup=lambda *_args: ("", ""),
    )
    store = IssueReportStore(tmp_path / "reviews.sqlite3")
    stored = store.get(store.save(record))

    assert stored["include_conversation"] is True
    assert stored["conversation_id"] == "tab-alpha-1"
    assert stored["provider"] == "openai"
    assert stored["model"] == "gpt-test-report"
    assert stored["comment"] == ""
    assert [turn["role"] for turn in stored["turns"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert stored["citations"] == [
        "https://catalog.sjsu.edu/isda",
        "https://www.sjsu.edu/ischool/",
        "https://sjsu.edu/advising",
    ]
    assert "https://evil.example/not-in-the-answer" not in stored["citations"]


def test_server_history_model_replaces_the_browser_value():
    record = build_issue_report(
        _report(
            include_conversation=True,
            provider="anthropic",
            model="claude-liar",
            turns=[{"role": "user", "text": "Which course is required?"}],
        ),
        history_lookup=lambda conversation_id, question, answer: (
            "openai",
            "gpt-from-history",
        ),
        fallback_provider="azure_openai",
        fallback_model="gpt-later",
    )

    assert record["provider"] == "openai"
    assert record["model"] == "gpt-from-history"


def test_invalid_reports_are_rejected():
    with pytest.raises(IssueReportValidationError):
        build_issue_report(_report(reason="rude"))
    with pytest.raises(IssueReportValidationError):
        build_issue_report(_report(include_conversation="true"))
    with pytest.raises(IssueReportValidationError):
        build_issue_report(_report(question="   "))
    with pytest.raises(IssueReportValidationError):
        build_issue_report(_report(comment="x" * 1001))
    with pytest.raises(IssueReportValidationError):
        build_issue_report(
            _report(include_conversation=True, conversation_id="nope")
        )


def test_reports_share_the_reviews_database_and_list_newest_first(tmp_path):
    database = tmp_path / "reviews.sqlite3"
    store = IssueReportStore(database)
    first = store.save(build_issue_report(_report(question="First question?")))
    second = store.save(
        build_issue_report(_report(reason="outdated", question="Second question?"))
    )

    listed = store.list_reports()
    assert [item["id"] for item in listed] == [second, first]
    assert listed[0]["reason_label"] == "Outdated information"

    results = tmp_path / "latest.json"
    results.write_text(
        json.dumps(
            {
                "created_at": "2026-10-09T00:00:00+00:00",
                "left_model": {"provider": "openai", "model": "left", "label": "Left"},
                "right_model": {
                    "provider": "openai",
                    "model": "right",
                    "label": "Right",
                },
                "results": [
                    {
                        "id": "q1",
                        "question": "What should I register for?",
                        "left": {"answer": "Course A."},
                        "right": {"answer": "Course B."},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    EvaluationStore(results, database)
    assert store.get(first)["question"] == "First question?"
    connection = sqlite3.connect(database)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    connection.close()
    assert "answer_issue_reports" in tables
    assert "evaluation_reviews" in tables


@pytest.fixture
def report_client(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    monkeypatch.delenv("EVAL_ALLOWED_REVIEWERS", raising=False)
    monkeypatch.setattr(app_module, "_issue_report_store", None)
    monkeypatch.setattr(app_module, "_issue_report_store_key", None)
    monkeypatch.setattr(app_module, "_evaluation_store", None)
    monkeypatch.setattr(app_module, "_evaluation_store_key", None)
    monkeypatch.setattr(app_module, "_rate_limiter", app_module._FixedWindowRateLimiter())
    return app_module.app.test_client()


def _csrf(client):
    response = client.get("/api/issue-reports/csrf")
    assert response.status_code == 200
    return response.get_json()["csrf"]


def _post_report(client, payload, csrf=None):
    headers = {}
    if csrf is not None:
        headers["X-Issue-Report-CSRF"] = csrf
    return client.post("/api/issue-reports", json=payload, headers=headers)


def test_report_endpoint_requires_csrf_and_stores_only_the_flagged_answer(report_client):
    rejected = _post_report(report_client, _report())
    assert rejected.status_code == 403

    created = _post_report(
        report_client,
        _report(
            conversation_id="tab-secret-1",
            turns=[{"role": "user", "text": "do not keep this"}],
            model="gpt-secret",
        ),
        _csrf(report_client),
    )
    assert created.status_code == 200
    report_id = created.get_json()["id"]

    anonymous = report_client.get("/api/evaluation/issue-reports")
    assert anonymous.status_code == 401

    reviewer = app_module.app.test_client()
    denied = reviewer.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer One", "access_token": "wrong"},
    )
    assert denied.status_code == 401
    login = reviewer.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer One", "access_token": "test-review-token"},
    )
    assert login.status_code == 200
    listed = reviewer.get("/api/evaluation/issue-reports")
    assert listed.status_code == 200
    body = listed.get_json()["reports"]
    assert len(body) == 1
    assert body[0]["id"] == report_id
    assert body[0]["include_conversation"] is False
    assert body[0]["turns"] is None
    assert body[0]["conversation_id"] is None
    assert body[0]["model"] is None
    assert "do not keep this" not in json.dumps(body)


def test_report_endpoint_keeps_an_opt_in_transcript(report_client):
    answer = "Use https://catalog.sjsu.edu/example."
    created = _post_report(
        report_client,
        _report(
            reason="outdated",
            answer=answer,
            include_conversation=True,
            conversation_id="tab-beta-22",
            provider="openai",
            model="gpt-browser",
            turns=[
                {"role": "user", "text": "Earlier question"},
                {"role": "assistant", "text": answer},
            ],
            citations=["https://evil.example/extra"],
        ),
        _csrf(report_client),
    )
    assert created.status_code == 200

    reviewer = app_module.app.test_client()
    assert (
        reviewer.post(
            "/api/evaluation/session",
            json={"reviewer": "Reviewer Two", "access_token": "test-review-token"},
        ).status_code
        == 200
    )
    report = reviewer.get("/api/evaluation/issue-reports").get_json()["reports"][0]
    assert report["include_conversation"] is True
    assert report["conversation_id"] == "tab-beta-22"
    assert report["provider"] == "openai"
    assert report["model"] == "gpt-browser"
    assert report["turns"][0]["text"] == "Earlier question"
    assert report["citations"] == ["https://catalog.sjsu.edu/example"]


def test_chat_history_model_wins_over_the_value_in_the_report(
    monkeypatch, report_client, ready_chat_index
):
    from config import Config

    class _EchoBot:
        def chat(self, message, history=None):
            return {
                "user_message": message,
                "response": f"Echo: {message}",
                "source": "test",
                "confidence": 1.0,
                "retrieved_docs": [],
            }

    monkeypatch.setattr(Config, "DEFAULT_LLM_PROVIDER", "openai")
    monkeypatch.setattr(Config, "OPENAI_MODEL", "gpt-test-report")
    monkeypatch.setattr(app_module, "chatbot", _EchoBot())

    chat = report_client.post("/api/chat", json={"message": "Which course is required?"})
    assert chat.status_code == 200
    body = chat.get_json()
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-test-report"

    monkeypatch.setattr(Config, "OPENAI_MODEL", "gpt-later")
    created = _post_report(
        report_client,
        _report(
            answer=body["response"],
            include_conversation=True,
            provider="anthropic",
            model="claude-liar",
        ),
        _csrf(report_client),
    )
    assert created.status_code == 200

    reviewer = app_module.app.test_client()
    reviewer.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer One", "access_token": "test-review-token"},
    )
    report = reviewer.get("/api/evaluation/issue-reports").get_json()["reports"][0]
    assert report["model"] == "gpt-test-report"
    assert report["provider"] == "openai"


@pytest.mark.parametrize("role", [[], {}, ["user"], {"name": "assistant"}])
def test_report_validator_rejects_container_turn_roles(role):
    with pytest.raises(
        IssueReportValidationError, match="turn role must be user or assistant"
    ):
        build_issue_report(
            _report(
                include_conversation=True,
                turns=[{"role": role, "text": "Earlier question"}],
            )
        )


@pytest.mark.parametrize("role", [[], {}, ["user"], {"name": "assistant"}])
def test_report_endpoint_rejects_container_turn_roles(report_client, monkeypatch, role):
    def unexpected_store_access():
        pytest.fail("Invalid reports must be rejected before accessing storage")

    monkeypatch.setattr(app_module, "get_issue_report_store", unexpected_store_access)
    response = _post_report(
        report_client,
        _report(
            include_conversation=True,
            turns=[{"role": role, "text": "Earlier question"}],
        ),
        _csrf(report_client),
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "turn role must be user or assistant"}


def test_report_endpoint_rejects_bad_payloads_and_rate_limits(monkeypatch, report_client):
    csrf = _csrf(report_client)
    invalid = _post_report(report_client, _report(reason="spam"), csrf)
    assert invalid.status_code == 400

    monkeypatch.setenv("ISSUE_REPORT_RATE_LIMIT", "2")
    monkeypatch.setattr(app_module, "_rate_limiter", app_module._FixedWindowRateLimiter())
    csrf = _csrf(report_client)
    assert _post_report(report_client, _report(), csrf).status_code == 200
    second = _post_report(
        report_client, _report(question="Another question?"), csrf
    )
    assert second.status_code == 200
    limited = _post_report(report_client, _report(question="Third question?"), csrf)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]


def test_reason_summary_includes_zeros_and_stable_ties(tmp_path):
    store = IssueReportStore(tmp_path / "reviews.sqlite3")
    empty = store.reason_counts()
    assert empty == {
        "total": 0,
        "reasons": [
            {"key": key, "label": label, "count": 0}
            for key, label in issue_reports.REASONS
        ],
    }
    for reason in ["other", "made_up", "other", "incorrect_info", "made_up"]:
        store.save(build_issue_report(_report(reason=reason)))
    summary = store.reason_counts()
    assert summary["total"] == 5
    assert summary["reasons"] == [
        {"key": key, "label": issue_reports.REASON_LABELS[key], "count": count}
        for key, count in [("made_up", 2), ("other", 2), ("incorrect_info", 1), ("outdated", 0), ("knowledge_gap", 0)]
    ]
    assert len(store.list_reports(limit=1)) == 1
    assert store.reason_counts() == summary  # Counts cover more than the list limit.
    assert len(store.list_reports(reason="other")) == 2
    with pytest.raises(IssueReportValidationError):
        store.list_reports(reason="unknown")


def test_summary_excludes_expired_reports(tmp_path, retention_clock, monkeypatch):
    monkeypatch.setenv("ISSUE_REPORT_RETENTION_DAYS", "90")
    store = IssueReportStore(tmp_path / "reviews.sqlite3")
    _dated_report(store, retention_clock["now"] - timedelta(days=91))
    _dated_report(store, retention_clock["now"] - timedelta(days=90))
    store.save(build_issue_report(_report(reason="other")))
    retention_clock["monotonic"] += issue_reports.PURGE_INTERVAL_SECONDS
    summary = store.reason_counts()
    assert summary["total"] == 2
    assert {item["key"]: item["count"] for item in summary["reasons"]} == {
        "incorrect_info": 1, "made_up": 0, "outdated": 0, "other": 1, "knowledge_gap": 0,
    }
    assert len(store.list_reports()) == 2


def test_reviewer_report_summary_and_reason_filter(report_client):
    store = app_module.get_issue_report_store()
    for reason in ["other", "made_up", "other"]:
        store.save(build_issue_report(_report(reason=reason)))
    for query in ["", "?reason=other", "?reason=unknown"]:
        assert report_client.get("/api/evaluation/issue-reports" + query).status_code == 401
    assert report_client.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer One", "access_token": "test-review-token"},
    ).status_code == 200
    endpoint = "/api/evaluation/issue-reports"
    body = report_client.get(endpoint).get_json()
    assert len(body["reports"]) == 3
    assert body["summary"]["total"] == 3
    assert [reason["key"] for reason in body["summary"]["reasons"]] == [
        "other", "made_up", "incorrect_info", "outdated", "knowledge_gap",
    ]
    for reason in issue_reports.REASON_LABELS:
        response = report_client.get(endpoint, query_string={"reason": reason})
        assert response.status_code == 200
        filtered = response.get_json()
        assert all(report["reason"] == reason for report in filtered["reports"])
        assert len(filtered["reports"]) == (2 if reason == "other" else 1 if reason == "made_up" else 0)
        assert filtered["summary"] == body["summary"]
    for reason in ["unknown", "", "other' OR 1=1 --"]:
        assert report_client.get(endpoint, query_string={"reason": reason}).status_code == 400
