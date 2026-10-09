"""Opt-in server-built transcripts and private reviewer retrieval."""
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import app as app_module
from src.saved_chats import SavedChatStore, SavedChatValidationError, build_transcript


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    monkeypatch.delenv("EVAL_ALLOWED_REVIEWERS", raising=False)
    monkeypatch.delenv("SAVED_CHAT_RETENTION_DAYS", raising=False)
    monkeypatch.setattr(app_module, "_saved_chat_store", None)
    monkeypatch.setattr(app_module, "_saved_chat_store_key", None)
    monkeypatch.setattr(app_module, "_rate_limiter", app_module._FixedWindowRateLimiter())
    monkeypatch.setattr(app_module, "_history_store", app_module._SessionHistoryStore(100, 50, 3600))
    return app_module.app.test_client()


def csrf(client):
    response = client.get("/api/saved-chats/csrf")
    assert response.status_code == 200
    return response.get_json()["csrf"]


def add_history(client, conversation_id="conversation-123", question="Private question", answer="Private answer"):
    with client.session_transaction() as session:
        session["chat_session_id"] = session.get("chat_session_id", "a" * 32)
        key = f'{session["chat_session_id"]}:{conversation_id}'
    app_module._history_store.append(key, {
        "user_message": question, "response": answer, "source": "test",
        "provider": "private-provider", "model": "private-model",
    })


def post(client, token, **overrides):
    data = {"conversation_id": "conversation-123", "consent": True}
    data.update(overrides)
    return client.post("/api/saved-chats", json=data, headers={"X-Issue-Report-CSRF": token})


def rows():
    return app_module.get_saved_chat_store().list_chats()


def test_yes_stores_server_history_only_and_logs_no_contents(client, caplog):
    token = csrf(client)
    add_history(client)
    with caplog.at_level(logging.INFO):
        response = post(client, token, transcript=[{"content": "Forged transcript"}],
                        name="Forged name", email="fake@example.com")
    assert response.status_code == 200
    assert response.get_json()["saved"] is True
    stored = rows()
    assert len(stored) == 1
    assert stored[0]["id"] == response.get_json()["id"]
    assert stored[0]["conversation_id"] == "conversation-123"
    assert [(t["role"], t["content"]) for t in stored[0]["transcript"]] == [
        ("user", "Private question"), ("assistant", "Private answer")]
    for turn in stored[0]["transcript"]:
        assert set(turn) == {"role", "content", "time"}
        assert datetime.fromisoformat(turn["time"]).utcoffset() == timedelta(0)
    for text in ("Private question", "Private answer", "Forged", "private-model"):
        assert text not in caplog.text
        if text != "Private question" and text != "Private answer":
            assert text not in json.dumps(stored)


@pytest.mark.parametrize("consent", [False, None, "true", 1, [], {}])
def test_no_or_non_boolean_consent_writes_nothing(client, consent):
    token = csrf(client)
    add_history(client)
    assert post(client, token, consent=consent).get_json() == {"ok": True, "saved": False}
    assert rows() == []


def test_missing_consent_and_declined_chat_not_stored_even_after_more_chat(client):
    token = csrf(client)
    add_history(client)
    response = client.post("/api/saved-chats", json={"conversation_id": "conversation-123"},
                           headers={"X-Issue-Report-CSRF": token})
    assert response.get_json() == {"ok": True, "saved": False}
    assert post(client, token, consent=False).status_code == 200
    add_history(client, question="Keep chatting", answer="Another answer")
    store = app_module.get_saved_chat_store()
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM saved_chats").fetchone()[0] == 0


def test_resaving_upserts_one_row_with_latest_transcript(client):
    token = csrf(client)
    add_history(client)
    first = post(client, token).get_json()["id"]
    created = rows()[0]["created_at"]
    add_history(client, question="Follow up", answer="More advice")
    second = post(client, token).get_json()["id"]
    assert first == second
    assert len(rows()) == 1
    assert rows()[0]["created_at"] == created
    assert rows()[0]["updated_at"] >= created
    assert len(rows()[0]["transcript"]) == 4


@pytest.mark.parametrize("token", [None, "wrong"])
def test_csrf_missing_or_wrong_rejected(client, token):
    csrf(client)
    headers = {} if token is None else {"X-Issue-Report-CSRF": token}
    response = client.post("/api/saved-chats", json={"consent": True}, headers=headers)
    assert response.status_code == 403
    assert rows() == []


def test_token_without_cookie_is_rejected(client):
    token = csrf(client)
    other = app_module.app.test_client()
    assert post(other, token).status_code == 403
    assert rows() == []


@pytest.mark.parametrize("conversation_id", ["conversation-123", "bad", "", None])
def test_no_history_or_invalid_conversation_stores_nothing(client, conversation_id):
    assert post(client, csrf(client), conversation_id=conversation_id).status_code == 400
    assert rows() == []


def test_history_is_scoped_to_own_session_and_conversation(client):
    token = csrf(client)
    add_history(client, conversation_id="another-conversation")
    assert post(client, token).status_code == 400
    other = app_module.app.test_client()
    other_token = csrf(other)
    assert post(other, other_token, conversation_id="another-conversation").status_code == 400
    assert rows() == []


def test_list_reviewer_only_and_newest_first(client):
    token = csrf(client)
    add_history(client)
    post(client, token)
    add_history(client, conversation_id="another-conversation")
    post(client, token, conversation_id="another-conversation")
    assert client.get("/api/evaluation/saved-chats").status_code == 401
    login = client.post("/api/evaluation/session", json={
        "reviewer": "Reviewer One", "access_token": "test-review-token"})
    assert login.status_code == 200
    response = client.get("/api/evaluation/saved-chats")
    assert response.status_code == 200
    assert [c["conversation_id"] for c in response.get_json()["chats"]] == [
        "another-conversation", "conversation-123"]


@pytest.mark.parametrize("operation", ["startup", "list", "write"])
def test_90_day_purge(client, operation):
    token = csrf(client)
    add_history(client)
    post(client, token)
    store = app_module.get_saved_chat_store()
    assert store.retention_days == 90
    old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE saved_chats SET created_at = ?", (old,))
    store._last_purge = None
    if operation == "startup":
        store = SavedChatStore(store.database_path)
    elif operation == "write":
        store.save("new-conversation", [{"role": "user", "content": "Recent", "time": old}])
    listed = store.list_chats()
    assert all(c["conversation_id"] != "conversation-123" for c in listed)
    assert len(listed) == (1 if operation == "write" else 0)


@pytest.mark.parametrize("value", ["bad", "0", "-1", "14"])
def test_retention_environment_conventions(tmp_path, monkeypatch, value):
    monkeypatch.setenv("SAVED_CHAT_RETENTION_DAYS", value)
    store = SavedChatStore(tmp_path / "reviews.sqlite3")
    assert store.retention_days == (14 if value == "14" else 90)


def test_schema_has_no_ip_user_agent_or_identity_columns(client):
    store = app_module.get_saved_chat_store()
    with sqlite3.connect(store.database_path) as connection:
        columns = {r[1] for r in connection.execute("PRAGMA table_info(saved_chats)")}
    assert columns == {"id", "conversation_id", "created_at", "updated_at", "transcript_json"}
    assert store.database_path.name == "reviews.sqlite3"


def test_rate_limit(client, monkeypatch):
    monkeypatch.setenv("SAVED_CHAT_RATE_LIMIT", "2")
    token = csrf(client)
    add_history(client)
    assert post(client, token).status_code == 200
    assert post(client, token).status_code == 200
    response = post(client, token)
    assert response.status_code == 429
    assert response.headers["Retry-After"]
    assert len(rows()) == 1


def test_transcript_limits(client):
    token = csrf(client)
    add_history(client, answer="x" * 12001)
    assert post(client, token).status_code == 400
    assert rows() == []
    with pytest.raises(SavedChatValidationError):
        build_transcript([{"user": "Q", "assistant": "A", "time": "now"}] * 101)
    # The default 50-exchange history fits in one copy.
    assert len(build_transcript([{"user": "Q", "assistant": "A", "time": "now"}] * 50)) == 100


def test_real_chat_can_save_timestamps_without_changing_public_history(client, monkeypatch, ready_chat_index):
    class EchoBot:
        def chat(self, message, history=None):
            return {"user_message": message, "response": f"Echo: {message}", "source": "test", "confidence": 1.0, "retrieved_docs": []}
    monkeypatch.setattr(app_module, "chatbot", EchoBot())
    response = client.post("/api/chat", json={"conversation_id": "conversation-123", "message": "My name is Pat"})
    assert response.status_code == 200
    assert post(client, csrf(client)).status_code == 200
    assert rows()[0]["transcript"][0]["content"] == "My name is Pat"
    public = client.get("/api/history?conversation_id=conversation-123").get_json()["history"]
    assert set(public[0]) == {"user", "assistant", "source"}


def test_another_browser_cannot_replace_a_saved_copy_with_same_tab_id(client):
    token = csrf(client)
    add_history(client)
    assert post(client, token).status_code == 200
    original = rows()[0]
    other = app_module.app.test_client()
    other_token = csrf(other)
    with other.session_transaction() as session:
        session["chat_session_id"] = "b" * 32
    add_history(other, question="Unrelated browser question", answer="Unrelated answer")
    response = post(other, other_token)
    assert response.status_code == 400
    assert rows() == [original]
