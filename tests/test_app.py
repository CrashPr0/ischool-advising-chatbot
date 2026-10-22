"""Small API contract tests that do not call an external model."""

import json
import os
import sys
import time

import app as app_module


def test_release_local_src_wins_over_persistent_azure_fallback(monkeypatch, tmp_path):
    release_src = tmp_path / "release" / "src"
    persistent_src = tmp_path / "home" / "site" / "wwwroot" / "src"
    release_src.mkdir(parents=True)
    persistent_src.mkdir(parents=True)
    monkeypatch.setattr(sys, "path", [str(persistent_src), "existing-entry"])

    selected = app_module._prepend_first_existing_path(
        [str(release_src), str(persistent_src)]
    )

    assert selected == str(release_src)
    assert sys.path[0] == str(release_src)
    assert sys.path[1] == str(persistent_src)


class FakeChatbot:
    @staticmethod
    def get_available_providers():
        return ["fake"]

    def chat(self, message, history=None):
        return {
            "user_message": message,
            "response": f"Echo: {message}",
            "source": "test",
            "confidence": 1.0,
            "retrieved_docs": [],
        }

    def get_knowledge_base_questions(self):
        return ["hello"]


def test_health_probe_does_not_initialize_chatbot(monkeypatch):
    monkeypatch.setattr(app_module, "chatbot", None)

    response = app_module.app.test_client().get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "version": "development"}
    assert app_module.chatbot is None


def test_chat_history_isolated_per_session(monkeypatch, ready_chat_index):
    monkeypatch.setattr(app_module, "chatbot", FakeChatbot())
    first_client = app_module.app.test_client()
    second_client = app_module.app.test_client()

    assert first_client.post("/api/chat", json={"message": "alpha"}).status_code == 200
    assert second_client.post("/api/chat", json={"message": "beta"}).status_code == 200

    first_history = first_client.get("/api/history").get_json()["history"]
    second_history = second_client.get("/api/history").get_json()["history"]

    assert [entry["user"] for entry in first_history] == ["alpha"]
    assert [entry["user"] for entry in second_history] == ["beta"]

    first_client.post("/api/clear-history")
    assert first_client.get("/api/history").get_json()["history"] == []
    assert [
        entry["user"]
        for entry in second_client.get("/api/history").get_json()["history"]
    ] == ["beta"]


def test_document_admin_routes_fail_closed_without_token(monkeypatch):
    monkeypatch.delenv("DOCS_LOAD_TOKEN", raising=False)
    client = app_module.app.test_client()

    response = client.post("/api/load-docs")

    assert response.status_code == 503
    assert "DOCS_LOAD_TOKEN" in response.get_json()["error"]

    status_response = client.get("/api/load-docs/status")
    assert status_response.status_code == 503


def _isolate_index_job(monkeypatch, tmp_path, commit="deploy-sha"):
    """Point index job files at a temp dir and reset the in-process rate limit."""
    monkeypatch.setenv("DOCS_LOAD_TOKEN", "test-docs-token")
    monkeypatch.setattr(app_module, "_rate_limiter", app_module._FixedWindowRateLimiter())
    monkeypatch.setattr(app_module, "_index_state_dir", lambda: str(tmp_path))
    monkeypatch.setattr(app_module, "_deployment_version", lambda: commit)
    monkeypatch.setattr(app_module, "_DOCS_INDEX_START_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(app_module, "_docs_job_state", {"status": "idle"})


class FakeProcessor:
    docs_path = "/tmp/advising_docs"

    @staticmethod
    def scan_source_files():
        return {"total_files": 20, "txt_files": 20, "pdf_files": 0, "roots": []}

    @staticmethod
    def vector_store_count():
        return 189


class FakeIndexingChatbot:
    doc_processor = FakeProcessor()

    @staticmethod
    def process_documents():
        return None


def _wait_for_job(client, headers, expected, deadline_seconds=2):
    status = None
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        status = client.get("/api/load-docs/status", headers=headers).get_json()
        if status["status"] == expected:
            return status
        time.sleep(0.01)
    return status


def test_document_rebuild_can_run_outside_request_lifecycle(monkeypatch, tmp_path):
    _isolate_index_job(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "get_chatbot", lambda: FakeIndexingChatbot())
    client = app_module.app.test_client()
    headers = {"X-Admin-Token": "test-docs-token"}

    started = client.post("/api/load-docs?async=true", headers=headers)

    assert started.status_code == 202
    assert started.get_json()["status"] in {"queued", "running", "succeeded"}
    assert started.get_json()["commit"] == "deploy-sha"
    status = _wait_for_job(client, headers, "succeeded")
    assert status["status"] == "succeeded"
    assert status["source_files"] == 20
    assert status["vector_store_chunks"] == 189

    stats = client.get("/api/docs-stats", headers=headers)
    assert stats.status_code == 200
    body = stats.get_json()
    assert body["vector_store_chunks"] == 189
    assert body["index_build"]["commit"] == "deploy-sha"
    assert body["index_build"]["vector_store_chunks"] == 189
    assert body["index_build"]["source_files"] == 20


def test_docs_stats_exposes_a_stale_index_receipt(monkeypatch, tmp_path):
    """A non-empty collection is not proof unless its receipt names the commit."""
    _isolate_index_job(monkeypatch, tmp_path, commit="new-sha")
    monkeypatch.setattr(app_module, "get_chatbot", lambda: FakeIndexingChatbot())
    (tmp_path / "index_build.json").write_text(
        json.dumps(
            {
                "commit": "old-sha",
                "source_files": 20,
                "vector_store_chunks": 189,
            }
        ),
        encoding="utf-8",
    )
    client = app_module.app.test_client()
    headers = {"X-Admin-Token": "test-docs-token"}

    body = client.get("/api/docs-stats", headers=headers).get_json()

    assert body["vector_store_chunks"] == 189
    assert body["scan"]["total_files"] == 20
    assert body["index_build"]["commit"] == "old-sha"


def test_index_job_status_is_shared_across_worker_memory(monkeypatch, tmp_path):
    _isolate_index_job(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "get_chatbot", lambda: FakeIndexingChatbot())
    client = app_module.app.test_client()
    headers = {"X-Admin-Token": "test-docs-token"}

    assert client.post("/api/load-docs?async=true", headers=headers).status_code == 202
    assert _wait_for_job(client, headers, "succeeded")["status"] == "succeeded"

    app_module._docs_job_state.clear()
    app_module._docs_job_state.update({"status": "idle"})

    status = client.get("/api/load-docs/status", headers=headers).get_json()
    assert status["status"] == "succeeded"
    assert status["commit"] == "deploy-sha"
    assert status["vector_store_chunks"] == 189


def test_failed_rebuild_keeps_the_previous_receipt(monkeypatch, tmp_path):
    class FailingChatbot(FakeIndexingChatbot):
        @staticmethod
        def process_documents():
            raise RuntimeError("disk full")

    _isolate_index_job(monkeypatch, tmp_path, commit="new-sha")
    monkeypatch.setattr(app_module, "get_chatbot", lambda: FailingChatbot())
    (tmp_path / "index_build.json").write_text(
        json.dumps({"commit": "old-sha", "source_files": 20, "vector_store_chunks": 189}),
        encoding="utf-8",
    )
    client = app_module.app.test_client()
    headers = {"X-Admin-Token": "test-docs-token"}

    assert client.post("/api/load-docs?async=true", headers=headers).status_code == 202
    status = _wait_for_job(client, headers, "failed")

    assert status["status"] == "failed"
    assert status["commit"] == "new-sha"
    assert "error" in status
    receipt = client.get("/api/docs-stats", headers=headers).get_json()["index_build"]
    assert receipt["commit"] == "old-sha"


def test_dead_worker_does_not_block_a_new_rebuild(monkeypatch, tmp_path):
    _isolate_index_job(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "get_chatbot", lambda: FakeIndexingChatbot())
    (tmp_path / "index_job.json").write_text(
        json.dumps(
            {
                "job_id": "stale-job",
                "status": "running",
                "commit": "deploy-sha",
                "pid": 2147483646,
                "created_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    client = app_module.app.test_client()
    headers = {"X-Admin-Token": "test-docs-token"}

    assert app_module._pid_alive(2147483646) is False
    started = client.post("/api/load-docs?async=true", headers=headers)

    assert started.status_code == 202
    assert started.get_json()["job_id"] != "stale-job"
    assert _wait_for_job(client, headers, "succeeded")["status"] == "succeeded"


def test_live_worker_rejects_a_second_rebuild(monkeypatch, tmp_path):
    _isolate_index_job(monkeypatch, tmp_path)
    (tmp_path / "index_job.json").write_text(
        json.dumps(
            {
                "job_id": "live-job",
                "status": "running",
                "commit": "deploy-sha",
                "pid": os.getpid(),
                "created_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    client = app_module.app.test_client()
    headers = {"X-Admin-Token": "test-docs-token"}

    rejected = client.post("/api/load-docs?async=true", headers=headers)

    assert rejected.status_code == 409
    assert rejected.get_json()["job_id"] == "live-job"
    assert rejected.get_json()["status"] == "running"


def test_evaluation_email_allowlist_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(
        "EVAL_ALLOWED_REVIEWERS",
        "christopher.velez@sjsu.edu,joshua.rieta@sjsu.edu",
    )
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    client = app_module.app.test_client()

    rejected = client.post(
        "/api/evaluation/session",
        json={"reviewer": "someone@example.com", "access_token": "test-review-token"},
    )
    assert rejected.status_code == 403

    approved = client.post(
        "/api/evaluation/session",
        json={
            "reviewer": "Christopher.Velez@SJSU.EDU",
            "access_token": "test-review-token",
        },
    )
    assert approved.status_code == 200
    assert approved.get_json()["reviewer"] == "christopher.velez@sjsu.edu"


def test_evaluation_api_blinds_models_and_requires_complete_review_for_export(
    monkeypatch, tmp_path
):
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "created_at": "2026-09-19T12:00:00+00:00",
                "method": "Same context for both models.",
                "left_model": {
                    "provider": "azure_openai",
                    "model": "gpt-4o-mini",
                    "label": "Azure GPT-4o mini",
                },
                "right_model": {
                    "provider": "azure_openai",
                    "model": "gpt-5.6-luna",
                    "label": "Azure GPT-5.6 Luna",
                },
                "results": [
                    {
                        "id": "q1",
                        "question": "What should I register for?",
                        "review_focus": "Actionable guidance.",
                        "sources": ["catalog.md"],
                        "left": {"answer": "Choose course A.", "error": None},
                        "right": {"answer": "Choose course B.", "error": None},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVAL_RESULTS_PATH", str(results_path))
    monkeypatch.setenv("EVAL_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    monkeypatch.setattr(app_module, "_evaluation_store", None)
    monkeypatch.setattr(app_module, "_evaluation_store_key", None)
    client = app_module.app.test_client()

    assert client.get("/evaluation").status_code == 200
    assert (
        client.post(
            "/api/evaluation/session",
            json={"reviewer": "Reviewer One", "access_token": "wrong"},
        ).status_code
        == 401
    )

    login = client.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer One", "access_token": "test-review-token"},
    )
    assert login.status_code == 200
    csrf = login.get_json()["csrf"]

    workspace = client.get("/api/evaluation/workspace")
    serialized = workspace.get_data(as_text=True)
    assert workspace.status_code == 200
    assert "Azure GPT-4o mini" not in serialized
    assert "Azure GPT-5.6 Luna" not in serialized

    invalid = client.put(
        "/api/evaluation/reviews/q1/A",
        json={
            "accuracy": 9,
            "tone": 3,
            "succinctness": 3,
            "edge_cases": 3,
            "comment": "This is a valid-length comment.",
        },
        headers={"X-Evaluation-CSRF": csrf},
    )
    assert invalid.status_code == 400
    assert "1 and 5" in invalid.get_json()["error"]
    assert client.get("/api/evaluation/export.csv").status_code == 409

    for candidate, accuracy in (("A", 5), ("B", 3)):
        response = client.put(
            f"/api/evaluation/reviews/q1/{candidate}",
            json={
                "accuracy": accuracy,
                "tone": 4,
                "succinctness": 4,
                "edge_cases": 3,
                "comment": "This is a grounded and actionable advising response.",
            },
            headers={"X-Evaluation-CSRF": csrf},
        )
        assert response.status_code == 200
        assert response.get_json()["review"]["rubric"] == "dimensions"

    revealed = client.get("/api/evaluation/workspace").get_data(as_text=True)
    assert "Azure GPT-4o mini" in revealed
    assert "Azure GPT-5.6 Luna" in revealed
    assert client.get("/api/evaluation/summary").get_json()["available"] is True

    export = client.get("/api/evaluation/export.csv")
    assert export.status_code == 200
    assert "Azure GPT-4o mini" in export.get_data(as_text=True)


def test_chat_program_allowlist(monkeypatch, ready_chat_index):
    class ProgramChatbot(FakeChatbot):
        def __init__(self):
            self.programs = []

        def chat(self, message, history=None, program=None):
            self.programs.append(program)
            return super().chat(message, history)

    bot = ProgramChatbot()
    monkeypatch.setattr(app_module, "get_chatbot", lambda: bot)
    client = app_module.app.test_client()
    for program in ("isda", "mlis", "both", "invalid", "MLIS", [], {}, 42, None):
        response = client.post(
            "/api/chat", json={"message": "core courses", "program": program}
        )
        assert response.status_code == 200
        assert bot.programs[-1] == (
            program
            if isinstance(program, str) and program in {"isda", "mlis", "both"}
            else None
        )
    assert client.post("/api/chat", json={"message": "core courses"}).status_code == 200
    assert bot.programs[-1] is None


def test_chat_rejects_malformed_bodies_without_loading_the_model(
    monkeypatch, ready_chat_index
):
    def _fail():
        raise AssertionError("model must not load for an invalid request")

    monkeypatch.setattr(app_module, "get_chatbot", _fail)
    client = app_module.app.test_client()

    for body in ("[]", '"hi"', "null", "not json"):
        response = client.post(
            "/api/chat", data=body, content_type="application/json"
        )
        assert response.status_code == 400
        assert response.get_json()["error"] == "Request body must be a JSON object"

    for message in (None, 42, ["hi"], {"a": 1}):
        response = client.post("/api/chat", json={"message": message})
        assert response.status_code == 400
        assert response.get_json()["error"] == "message must be a string"
    assert client.post("/api/chat", json={}).status_code == 400

    for message in ("", "   \n\t "):
        response = client.post("/api/chat", json={"message": message})
        assert response.status_code == 400
        assert response.get_json()["error"] == "Empty message"


def test_chat_message_length_limit_applies_after_stripping(
    monkeypatch, ready_chat_index
):
    monkeypatch.setenv("CHAT_MESSAGE_MAX_LENGTH", "10")
    bot = FakeChatbot()
    monkeypatch.setattr(app_module, "get_chatbot", lambda: bot)
    client = app_module.app.test_client()

    padded = "  " + "a" * 10 + "  "
    assert client.post("/api/chat", json={"message": padded}).status_code == 200

    too_long = client.post("/api/chat", json={"message": "a" * 11})
    assert too_long.status_code == 413
    assert "10-character" in too_long.get_json()["error"]
