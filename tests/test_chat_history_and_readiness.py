"""Session history is sent into chat, and a stale index never calls the model."""

import json
import time
from pathlib import Path

import app as app_module

ROOT = Path(__file__).resolve().parents[1]


class RecordingChatbot:
    """Stand-in model. Records the history list the route passed in."""

    def __init__(self):
        self.calls = []

    def chat(self, message, history=None):
        seen = (
            [dict(entry) for entry in history] if isinstance(history, list) else history
        )
        self.calls.append({"message": message, "history": seen})
        # Prove the route passed a copy. A leak here must not land in the store.
        if isinstance(history, list):
            history.append({"user": "not-stored", "assistant": "leak"})
        tier = None
        if message == "crisis please":
            tier = "crisis"
        elif message == "I feel overwhelmed":
            tier = "distress"
        result = {
            "user_message": message,
            "response": f"Reply to {message}",
            "source": "test",
            "confidence": None,
            "retrieved_docs": [],
        }
        if tier is not None:
            result["safety_tier"] = tier
        return result


def _history(client):
    return client.get("/api/history").get_json()["history"]


def test_chat_timings_json_is_off_unless_the_env_flag_is_set(
    monkeypatch, ready_chat_index
):
    class _TimingsBot:
        def chat(self, message, history=None):
            return {
                "user_message": message,
                "response": "ok",
                "source": "rag",
                "confidence": 0.7,
                "retrieved_docs": [],
                "timings": {
                    "retrieval_ms": 3.0,
                    "embedding_ms": 1.5,
                    "llm_ms": 12.0,
                    "total_ms": 16.0,
                    "llm_attempts": 1,
                    "rate_limit_count": 0,
                    "deployment": "advising",
                },
            }

    monkeypatch.setattr(app_module, "get_chatbot", lambda: _TimingsBot())
    monkeypatch.delenv("CHAT_DEBUG_TIMINGS", raising=False)
    client = app_module.app.test_client()
    hidden = client.post("/api/chat", json={"message": "What courses are required?"})
    assert hidden.status_code == 200
    assert "timings" not in hidden.get_json()

    monkeypatch.setenv("CHAT_DEBUG_TIMINGS", "false")
    still_hidden = client.post(
        "/api/chat", json={"message": "What courses are required?"}
    )
    assert "timings" not in still_hidden.get_json()

    monkeypatch.setenv("CHAT_DEBUG_TIMINGS", "true")
    shown = client.post("/api/chat", json={"message": "What courses are required?"})
    assert shown.status_code == 200
    body = shown.get_json()
    assert body["source"] == "rag"
    assert body["timings"]["embedding_ms"] == 1.5
    assert body["timings"]["deployment"] == "advising"


def test_busy_reply_is_http_200(monkeypatch, ready_chat_index):
    class _BusyBot:
        def chat(self, message, history=None):
            return {
                "user_message": message,
                "response": (
                    "The advising assistant is busy right now. "
                    "Please try again in about a minute."
                ),
                "source": "busy",
                "confidence": None,
                "retrieved_docs": [],
            }

    monkeypatch.setattr(app_module, "get_chatbot", lambda: _BusyBot())
    response = app_module.app.test_client().post(
        "/api/chat", json={"message": "What courses are required?"}
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["source"] == "busy"
    assert "busy right now" in body["response"]


def test_chat_passes_prior_turns_and_skips_crisis(monkeypatch, ready_chat_index):
    assert ready_chat_index["commit"] == "test-release"
    bot = RecordingChatbot()
    monkeypatch.setattr(app_module, "chatbot", bot)
    client = app_module.app.test_client()

    first = client.post("/api/chat", json={"message": "What courses are required?"})
    assert first.status_code == 200
    assert "safety_tier" not in first.get_json()
    assert bot.calls[0]["history"] == []
    assert _history(client) == [
        {
            "user": "What courses are required?",
            "assistant": "Reply to What courses are required?",
            "source": "test",
        }
    ]

    follow = client.post("/api/chat", json={"message": "what about the prerequisites?"})
    assert follow.status_code == 200
    assert bot.calls[1]["message"] == "what about the prerequisites?"
    assert bot.calls[1]["history"] == _history(client)[:1]
    assert [entry["user"] for entry in _history(client)] == [
        "What courses are required?",
        "what about the prerequisites?",
    ]

    crisis = client.post("/api/chat", json={"message": "crisis please"})
    assert crisis.status_code == 200
    assert crisis.get_json()["safety_tier"] == "crisis"
    assert crisis.get_json()["response"] == "Reply to crisis please"
    assert bot.calls[2]["history"] == _history(client)
    assert [entry["user"] for entry in _history(client)] == [
        "What courses are required?",
        "what about the prerequisites?",
    ]
    assert all(entry["user"] != "not-stored" for entry in _history(client))

    distress = client.post("/api/chat", json={"message": "I feel overwhelmed"})
    assert distress.status_code == 200
    assert distress.get_json()["safety_tier"] == "distress"
    stored = _history(client)
    assert [entry["user"] for entry in stored] == [
        "What courses are required?",
        "what about the prerequisites?",
        "I feel overwhelmed",
    ]
    assert stored[-1]["assistant"] == "Reply to I feel overwhelmed"


def test_conversation_ids_keep_separate_histories(monkeypatch, ready_chat_index):
    """Each tab id is its own context, and the old session key still works."""
    bot = RecordingChatbot()
    monkeypatch.setattr(app_module, "chatbot", bot)
    client = app_module.app.test_client()
    other = app_module.app.test_client()

    alpha = client.post(
        "/api/chat",
        json={"message": "alpha question", "conversation_id": "tab-alpha-1"},
    )
    beta = client.post(
        "/api/chat",
        json={"message": "beta question", "conversation_id": "tab-beta-22"},
    )
    assert alpha.status_code == 200
    assert beta.status_code == 200
    assert bot.calls[0]["history"] == []
    assert bot.calls[1]["history"] == []

    follow = client.post(
        "/api/chat",
        json={"message": "alpha follow up", "conversation_id": "tab-alpha-1"},
    )
    assert follow.status_code == 200
    assert [entry["user"] for entry in bot.calls[2]["history"]] == ["alpha question"]

    # A client that omits the id still uses the original session bucket.
    legacy = client.post("/api/chat", json={"message": "legacy question"})
    assert legacy.status_code == 200
    assert bot.calls[3]["history"] == []

    alpha_history = client.get("/api/history?conversation_id=tab-alpha-1").get_json()
    beta_history = client.get("/api/history?conversation_id=tab-beta-22").get_json()
    legacy_history = client.get("/api/history").get_json()
    assert [entry["user"] for entry in alpha_history["history"]] == [
        "alpha question",
        "alpha follow up",
    ]
    assert [entry["user"] for entry in beta_history["history"]] == ["beta question"]
    assert [entry["user"] for entry in legacy_history["history"]] == ["legacy question"]

    # The same tab id in another browser session does not see this history.
    other_view = other.get("/api/history?conversation_id=tab-alpha-1")
    assert other_view.status_code == 200
    assert other_view.get_json()["history"] == []

    cleared = client.post("/api/clear-history", json={"conversation_id": "tab-alpha-1"})
    assert cleared.status_code == 200
    assert (
        client.get("/api/history?conversation_id=tab-alpha-1").get_json()["history"]
        == []
    )
    assert client.get("/api/history?conversation_id=tab-beta-22").get_json()["history"]
    assert client.get("/api/history").get_json()["history"]

    rejected = client.post(
        "/api/chat",
        json={"message": "nope", "conversation_id": "bad id"},
    )
    assert rejected.status_code == 400
    untouched = client.post(
        "/api/clear-history",
        json={"conversation_id": "../secrets"},
    )
    assert untouched.status_code == 400
    assert [
        entry["user"] for entry in client.get("/api/history").get_json()["history"]
    ] == ["legacy question"]


def _point_at_store(monkeypatch, tmp_path, commit, receipt):
    # Local import so collection does not bind the root config shim before
    # tests that compare it with src.durable_storage.
    from config import Config

    store = tmp_path / "chroma"
    store.mkdir()
    (tmp_path / "deployment_version.txt").write_text(f"{commit}\n", encoding="utf-8")
    if receipt is not None:
        (store / "index_build.json").write_text(receipt, encoding="utf-8")
    monkeypatch.setattr(app_module, "_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(store))
    monkeypatch.setattr(
        app_module, "_rate_limiter", app_module._FixedWindowRateLimiter()
    )
    monkeypatch.setattr(app_module, "_DOCS_INDEX_START_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(app_module, "_docs_job_state", {"status": "idle"})
    monkeypatch.setenv("DOCS_LOAD_TOKEN", "test-docs-token")
    monkeypatch.setattr(app_module, "chatbot", None)
    return store


def _wait_for_job(client, headers, expected, deadline_seconds=2):
    status = None
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        status = client.get("/api/load-docs/status", headers=headers).get_json()
        if status["status"] == expected:
            return status
        time.sleep(0.01)
    return status


def test_stale_or_missing_receipt_refuses_chat_without_a_model_call(
    monkeypatch, tmp_path
):
    store = _point_at_store(
        monkeypatch,
        tmp_path,
        "new-sha",
        json.dumps({"commit": "old-sha"}),
    )
    model_calls = {"n": 0}

    def fail_if_loaded():
        model_calls["n"] += 1
        raise AssertionError("model must not load while the receipt is not ready")

    monkeypatch.setattr(app_module, "get_chatbot", fail_if_loaded)
    client = app_module.app.test_client()

    empty = client.post("/api/chat", json={"message": "   "})
    assert empty.status_code == 400
    assert model_calls["n"] == 0

    refused = client.post("/api/chat", json={"message": "What courses are required?"})
    assert refused.status_code == 503
    body = refused.get_json()
    assert body == {"error": app_module._INDEX_NOT_READY_MESSAGE}
    assert "commit" not in body["error"]
    assert "index_build" not in body["error"]
    assert model_calls["n"] == 0

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.get_json() == {"status": "ok", "version": "new-sha"}
    assert app_module.chatbot is None

    store.joinpath("index_build.json").unlink()
    missing = client.post("/api/chat", json={"message": "hello"})
    assert missing.status_code == 503
    assert missing.get_json()["error"] == app_module._INDEX_NOT_READY_MESSAGE

    store.joinpath("index_build.json").write_text("{", encoding="utf-8")
    unreadable = client.post("/api/chat", json={"message": "hello"})
    assert unreadable.status_code == 503
    assert model_calls["n"] == 0

    readyz = client.get("/readyz")
    assert readyz.status_code == 503
    assert client.get("/healthz").status_code == 200


class _FakeProcessor:
    docs_path = "/tmp/advising_docs"

    @staticmethod
    def scan_source_files():
        return {"total_files": 20, "txt_files": 20, "pdf_files": 0, "roots": []}

    @staticmethod
    def vector_store_count():
        return 189


class _FakeIndexingChatbot:
    doc_processor = _FakeProcessor()

    @staticmethod
    def process_documents():
        return None


def test_admin_rebuild_unblocks_chat_without_using_the_chat_route(
    monkeypatch, tmp_path
):
    """The deploy waits for /healthz, then rebuilds through load-docs."""
    _point_at_store(monkeypatch, tmp_path, "new-sha", None)
    monkeypatch.setattr(app_module, "get_chatbot", lambda: _FakeIndexingChatbot())
    client = app_module.app.test_client()
    headers = {"X-Admin-Token": "test-docs-token"}

    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").get_json()["version"] == "new-sha"
    blocked = client.post("/api/chat", json={"message": "hello"})
    assert blocked.status_code == 503

    started = client.post("/api/load-docs?async=true", headers=headers)
    assert started.status_code == 202
    status = _wait_for_job(client, headers, "succeeded")
    assert status["status"] == "succeeded"
    assert status["commit"] == "new-sha"

    # The indexing chatbot must not be what answers the student. Swap in a
    # recorder only after the receipt exists, the same order as a deploy.
    bot = RecordingChatbot()
    monkeypatch.setattr(app_module, "chatbot", bot)
    monkeypatch.setattr(app_module, "get_chatbot", lambda: bot)

    assert client.get("/healthz").status_code == 200
    answered = client.post("/api/chat", json={"message": "What courses are required?"})
    assert answered.status_code == 200
    assert answered.get_json()["response"] == "Reply to What courses are required?"
    assert bot.calls[0]["history"] == []
    assert client.get("/readyz").status_code == 200


def test_deploy_workflow_rebuilds_after_healthz_and_not_through_chat():
    workflow = (ROOT / ".github" / "workflows" / "deploy-azure.yml").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "verify_deployed_index.py").read_text(
        encoding="utf-8"
    )
    health_at = workflow.index('"$APP_URL/healthz"')
    rebuild_at = workflow.index("scripts/verify_deployed_index.py")
    assert health_at < rebuild_at
    assert "/readyz" not in workflow
    assert "/api/chat" not in workflow
    assert "/api/chat" not in verifier
    assert "/api/load-docs?async=true" in verifier
