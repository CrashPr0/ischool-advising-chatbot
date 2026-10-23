"""All-time suggestion picks, starter fallback, and reviewer moderation."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

import app as app_module
from src.question_suggestions import load_suggestion_catalog, rank_question_suggestions
from src.suggestion_usage import (
    SuggestionUsageStore,
    choose_starters,
    student_suggestion_payload,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 17, 15, 4, 5, tzinfo=timezone.utc)
SECRET = b"test-dedupe-secret"
PLANTED = "partial transcript 998877"


def _catalog():
    return load_suggestion_catalog()


def _id_for(text):
    for item in _catalog()["questions"]:
        if item["text"] == text:
            return item["id"]
    raise AssertionError(text)


def _store(tmp_path):
    return SuggestionUsageStore(tmp_path / "reviews.sqlite3", dedupe_secret=SECRET)


def _prepare(monkeypatch, tmp_path, *, dynamic="true", mode="all_time", minimum="3"):
    database = tmp_path / "reviews.sqlite3"
    monkeypatch.setenv("EVAL_DB_PATH", str(database))
    monkeypatch.setenv("DYNAMIC_SUGGESTIONS_ENABLED", dynamic)
    monkeypatch.setenv("SUGGESTION_RANKING_MODE", mode)
    monkeypatch.setenv("SUGGESTION_POPULARITY_MIN_COUNT", minimum)
    monkeypatch.setenv("QUESTION_SUGGESTIONS_ENABLED", "true")
    monkeypatch.delenv("POPULAR_QUESTIONS_PUBLIC", raising=False)
    monkeypatch.setattr(app_module, "_suggestion_usage_store", None)
    monkeypatch.setattr(app_module, "_suggestion_usage_store_key", None)
    monkeypatch.setattr(
        app_module, "_rate_limiter", app_module._FixedWindowRateLimiter()
    )
    return database


def _record(
    store, suggestion_id, session, *, event_type="dropdown", program="both", now=NOW
):
    return store.record_pick(
        suggestion_id=suggestion_id,
        event_type=event_type,
        program=program,
        session_token=session,
        now=now,
    )


def _sign_in(client):
    response = client.post(
        "/api/evaluation/session",
        json={"reviewer": "reviewer@sjsu.edu", "access_token": "test-review-token"},
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()["csrf"]


def test_catalog_ids_are_stable_and_the_fallback_four_match_the_empty_chat():
    catalog = _catalog()
    ids = [item["id"] for item in catalog["questions"]]
    assert len(ids) == len(set(ids))
    assert all(item["pinned"] is False for item in catalog["questions"])
    assert catalog["fallback_starter_ids"] == [
        _id_for("Who is my assigned ISDA advisor?"),
        _id_for("What are the ISDA core courses?"),
        _id_for("What are the MLIS core courses?"),
        _id_for("How do I add or drop a course?"),
    ]
    template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
    for suggestion_id in catalog["fallback_starter_ids"]:
        text = next(
            item["text"] for item in catalog["questions"] if item["id"] == suggestion_id
        )
        assert f'data-question="{text}"' in template


def test_picks_store_a_timestamp_and_drop_typed_text(tmp_path):
    store = _store(tmp_path)
    suggestion_id = _id_for("How do I add or drop a course?")
    assert (
        _record(store, suggestion_id, "browser-a", event_type="chip", program="isda")
        == "recorded"
    )
    columns = store.event_column_names()
    assert columns == ["id", "suggestion_id", "event_type", "program", "picked_at"]
    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT suggestion_id, event_type, program, picked_at FROM suggestion_pick_events"
        ).fetchone()
    assert row[0] == suggestion_id
    assert row[1] == "chip"
    assert row[2] == "isda"
    parsed = datetime.fromisoformat(row[3])
    assert parsed == NOW
    raw = store.database_path.read_bytes()
    assert b"How do I add or drop" not in raw
    for forbidden in ("session", "conversation", "remote_addr", "user_id"):
        assert forbidden not in columns


def test_same_session_counts_once_per_day_and_old_picks_still_count(tmp_path):
    store = _store(tmp_path)
    popular = _id_for("Are there scholarships for ISDA students?")
    recent = _id_for("How do waitlists work at SJSU?")
    for index in range(5):
        assert (
            _record(
                store,
                popular,
                f"student-{index}",
                program="isda",
                now=NOW - timedelta(days=400),
            )
            == "recorded"
        )
    assert (
        _record(
            store, popular, "student-0", program="isda", now=NOW - timedelta(days=400)
        )
        == "duplicate"
    )
    assert _record(store, popular, "student-0", program="isda", now=NOW) == "recorded"
    assert (
        _record(store, recent, "student-9", now=NOW - timedelta(days=1)) == "recorded"
    )
    counts = store.counts()
    assert counts[popular] == 6
    assert counts[recent] == 1
    with sqlite3.connect(store.database_path) as connection:
        kept = connection.execute(
            "SELECT COUNT(*) FROM suggestion_pick_events"
        ).fetchone()[0]
    assert kept == 7


def test_threshold_pinned_fallback_and_hidden_shape_the_starters():
    questions = [
        {
            "id": "pinned-one",
            "text": "Pinned question?",
            "programs": ["both"],
            "pinned": True,
        },
        {
            "id": "popular-one",
            "text": "Popular question?",
            "programs": ["both"],
            "pinned": False,
        },
        {
            "id": "low-one",
            "text": "Low question?",
            "programs": ["both"],
            "pinned": False,
        },
        {
            "id": "fallback-one",
            "text": "Fallback one?",
            "programs": ["both"],
            "pinned": False,
        },
        {
            "id": "fallback-two",
            "text": "Fallback two?",
            "programs": ["both"],
            "pinned": False,
        },
        {
            "id": "fallback-three",
            "text": "Fallback three?",
            "programs": ["both"],
            "pinned": False,
        },
        {
            "id": "fallback-four",
            "text": "Fallback four?",
            "programs": ["both"],
            "pinned": False,
        },
    ]
    fallback = ["fallback-one", "fallback-two", "fallback-three", "fallback-four"]
    counts = {"popular-one": 4, "low-one": 2, "pinned-one": 0}
    starters = [
        item["id"]
        for item in choose_starters(questions, fallback, counts, set(), min_count=3)
    ]
    assert starters == ["pinned-one", "popular-one", "fallback-one", "fallback-two"]
    hidden = choose_starters(
        questions, fallback, counts, {"popular-one", "pinned-one"}, min_count=3
    )
    assert [item["id"] for item in hidden] == fallback
    empty = choose_starters(questions, fallback, {}, set(), min_count=3)
    assert [item["id"] for item in empty][:4] == ["pinned-one", *fallback[:3]]


def test_popularity_reorders_matches_without_inventing_them():
    questions = [
        {
            "id": "add-course",
            "text": "How do I add a course?",
            "programs": ["both"],
            "pinned": False,
        },
        {
            "id": "drop-course",
            "text": "How do I drop a course?",
            "programs": ["both"],
            "pinned": False,
        },
    ]
    plain = [item["id"] for item in rank_question_suggestions("how do i", questions)]
    boosted = [
        item["id"]
        for item in rank_question_suggestions(
            "how do i", questions, popularity={"drop-course": 10}
        )
    ]
    assert plain[0] == "add-course"
    assert boosted[0] == "drop-course"
    assert (
        rank_question_suggestions("zzzz", questions, popularity={"drop-course": 80})
        == []
    )


def test_browser_boost_matches_python(tmp_path):
    questions = [
        {"id": "add-course", "text": "How do I add a course?", "programs": ["both"]},
        {"id": "drop-course", "text": "How do I drop a course?", "programs": ["both"]},
    ]
    expected = [
        item["text"]
        for item in rank_question_suggestions(
            "how do i", questions, popularity={"drop-course": 10}
        )
    ]
    payload = tmp_path / "cases.json"
    payload.write_text(
        json.dumps({"questions": questions, "expected": expected}), encoding="utf-8"
    )
    runner = tmp_path / "check.js"
    runner.write_text(
        """
const fs = require("fs");
const vm = require("vm");
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const sandbox = {};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const ranked = sandbox.rankQuestionSuggestions("how do i", payload.questions, {
  popularity: {"drop-course": 10}
}).map((item) => item.text);
if (JSON.stringify(ranked) !== JSON.stringify(payload.expected)) {
  throw new Error(JSON.stringify(ranked));
}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "suggestions.js"), str(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_flag_off_writes_nothing_and_keeps_the_curated_payload(
    monkeypatch, tmp_path, caplog
):
    database = _prepare(monkeypatch, tmp_path, dynamic="false")
    caplog.set_level("INFO")
    client = app_module.app.test_client()
    posted = client.post(
        "/api/question-suggestion-events",
        json={
            "id": _id_for("How do I add or drop a course?"),
            "type": "chip",
            "program": "isda",
            "text": PLANTED,
        },
    )
    assert posted.status_code == 200
    assert posted.get_json() == {"ok": True, "recorded": False, "enabled": False}
    assert not database.exists()
    assert PLANTED not in caplog.text
    listed = client.get("/api/question-suggestions")
    body = listed.get_json()
    assert body["dynamic"] is False
    assert "starters" not in body
    assert "popularity" not in body
    assert client.get("/api/popular-questions").status_code == 404


def test_event_route_dedupes_rate_limits_and_stores_no_typed_text(
    monkeypatch, tmp_path, caplog
):
    database = _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("SUGGESTION_EVENT_RATE_LIMIT", "2")
    caplog.set_level("INFO")
    client = app_module.app.test_client()
    suggestion_id = _id_for("What are the ISDA core courses?")
    body = {
        "id": suggestion_id,
        "type": "dropdown",
        "program": "isda",
        "text": PLANTED,
        "q": PLANTED,
    }
    first = client.post("/api/question-suggestion-events", json=body)
    second = client.post("/api/question-suggestion-events", json=body)
    assert first.get_json()["recorded"] is True
    assert second.get_json()["duplicate"] is True
    other = app_module.app.test_client()
    third = other.post(
        "/api/question-suggestion-events",
        json={
            "id": _id_for("What are the MLIS core courses?"),
            "type": "chip",
            "program": "mlis",
        },
    )
    assert third.status_code == 429
    raw = database.read_bytes()
    assert PLANTED.encode() not in raw
    assert b"ISDA core courses" not in raw
    assert PLANTED not in caplog.text
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT COUNT(*) FROM suggestion_pick_events"
        ).fetchone()[0]
    assert stored == 1
    monkeypatch.setattr(
        app_module, "_rate_limiter", app_module._FixedWindowRateLimiter()
    )
    unknown = client.post(
        "/api/question-suggestion-events",
        json={"id": PLANTED, "type": "dropdown", "program": "both"},
    )
    assert unknown.status_code == 400
    assert PLANTED.encode() not in database.read_bytes()


def test_all_time_ranking_promotes_only_past_the_threshold_and_falls_back(
    monkeypatch, tmp_path
):
    _prepare(monkeypatch, tmp_path, minimum="3")
    store = app_module.get_suggestion_usage_store()
    popular = _id_for("Are there scholarships for ISDA students?")
    low = _id_for("How do waitlists work at SJSU?")
    for index in range(4):
        _record(store, popular, f"s-{index}", now=NOW - timedelta(days=200))
    _record(store, low, "s-low", now=NOW)
    payload = student_suggestion_payload(store)
    assert payload["dynamic"] is True
    assert payload["ranking_mode"] == "all_time"
    assert payload["starters"][0] == popular
    assert low not in payload["starters"]
    assert popular in payload["popularity"]
    assert low not in payload["popularity"]
    assert payload["starters"][1:] == _catalog()["fallback_starter_ids"][:3]
    monkeypatch.setenv("SUGGESTION_RANKING_MODE", "recent")
    paused = student_suggestion_payload(store)
    assert paused["dynamic"] is False
    assert "starters" not in paused


def test_hidden_suggestions_lose_the_boost_until_restored(monkeypatch, tmp_path):
    database = _prepare(monkeypatch, tmp_path, minimum="1")
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    monkeypatch.delenv("EVAL_ALLOWED_REVIEWERS", raising=False)
    store = app_module.get_suggestion_usage_store()
    popular = _id_for("Are there scholarships for ISDA students?")
    _record(store, popular, "s-1", now=NOW - timedelta(days=10))
    anonymous = app_module.app.test_client()
    assert anonymous.get("/api/evaluation/suggestion-ranking").status_code == 401
    assert anonymous.get("/api/evaluation/suggestion-events.csv").status_code == 401
    denied = anonymous.post(
        f"/api/evaluation/suggestion-ranking/{popular}/hide",
        json={"reason": "Not appropriate for starters"},
    )
    assert denied.status_code == 401

    reviewer = app_module.app.test_client()
    csrf = _sign_in(reviewer)
    missing = reviewer.post(
        f"/api/evaluation/suggestion-ranking/{popular}/hide",
        json={"reason": "Not appropriate for starters"},
    )
    assert missing.status_code == 403
    hidden = reviewer.post(
        f"/api/evaluation/suggestion-ranking/{popular}/hide",
        json={"reason": "Not appropriate for starters"},
        headers={"X-Evaluation-CSRF": csrf},
    )
    assert hidden.status_code == 200
    payload = student_suggestion_payload(store)
    assert popular not in payload["starters"]
    assert popular not in payload["popularity"]
    ranking = reviewer.get("/api/evaluation/suggestion-ranking").get_json()
    row = next(item for item in ranking["suggestions"] if item["id"] == popular)
    assert row["hidden"] is True
    assert row["hidden_by"] == "reviewer@sjsu.edu"
    assert row["count"] == 1
    assert ranking["audit"][0]["action"] == "hide"
    assert ranking["audit"][0]["reason"] == "Not appropriate for starters"

    restored = reviewer.post(
        f"/api/evaluation/suggestion-ranking/{popular}/restore",
        json={"reason": "Safe to show again"},
        headers={"X-Evaluation-CSRF": csrf},
    )
    assert restored.status_code == 200
    assert popular in student_suggestion_payload(store)["popularity"]

    reset = reviewer.post(
        f"/api/evaluation/suggestion-ranking/{popular}/reset",
        json={"reason": "Start this count over"},
        headers={"X-Evaluation-CSRF": csrf},
    )
    assert reset.status_code == 200
    assert store.counts().get(popular, 0) == 0
    with sqlite3.connect(database) as connection:
        audit = connection.execute("""
            SELECT action, reviewer, reason FROM suggestion_moderation_audit
            ORDER BY id
            """).fetchall()
    assert [row[0] for row in audit] == ["hide", "restore", "reset"]
    assert {row[1] for row in audit} == {"reviewer@sjsu.edu"}

    exported = reviewer.get("/api/evaluation/suggestion-events.csv")
    assert exported.status_code == 200
    assert exported.mimetype == "text/csv"
    text = exported.get_data(as_text=True)
    assert text.splitlines()[0] == "picked_at,suggestion_id,event_type,program"
    assert popular not in text
    assert "scholarship" not in text.lower()

    monkeypatch.setenv("EVAL_ALLOWED_REVIEWERS", "reviewer@sjsu.edu")
    monkeypatch.delenv("EVAL_ACCESS_TOKEN", raising=False)
    outsider = app_module.app.test_client()
    blocked = outsider.post(
        "/api/evaluation/session", json={"reviewer": "other@sjsu.edu"}
    )
    assert blocked.status_code == 403
    assert outsider.get("/api/evaluation/suggestion-ranking").status_code == 401


def test_suggestions_flag_off_still_hides_the_list_when_dynamic_is_on(
    monkeypatch, tmp_path
):
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("QUESTION_SUGGESTIONS_ENABLED", "off")
    assert student_suggestion_payload(app_module.get_suggestion_usage_store()) == {
        "enabled": False,
        "questions": [],
    }


def test_event_script_does_not_send_the_typed_value():
    script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    recorder = script.split("function recordSuggestionPick(", 1)[1].split(
        "function ", 1
    )[0]
    assert "userInput" not in recorder
    assert "/api/question-suggestion-events" in recorder
    assert "text" not in recorder
    usage = (ROOT / "src" / "suggestion_usage.py").read_text(encoding="utf-8")
    assert "anonymized_questions" not in usage
    assert "DELETE FROM suggestion_pick_events WHERE picked_at" not in usage
    page = (ROOT / "templates" / "evaluation.html").read_text(encoding="utf-8")
    assert 'id="suggestion-ranking"' in page
    assert "Download event CSV" in page


def test_event_route_acknowledges_mara_and_msis_without_recording(monkeypatch, tmp_path):
    database = _prepare(monkeypatch, tmp_path)
    client = app_module.app.test_client()
    for program in ("mara", "MSIS"):
        posted = client.post(
            "/api/question-suggestion-events",
            json={"id": "x", "type": "chip", "program": program},
        )
        assert posted.status_code == 200
        assert posted.get_json() == {"ok": True, "recorded": False, "duplicate": False}
    assert not database.exists()
