"""Small API contract tests that do not call an external model."""

import json

import app as app_module


class FakeChatbot:
    @staticmethod
    def get_available_providers():
        return ["fake"]

    def chat(self, message):
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
    assert response.get_json() == {"status": "ok"}
    assert app_module.chatbot is None


def test_chat_history_isolated_per_session(monkeypatch):
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
        json={"rating": 9, "comment": "This is a valid-length comment."},
        headers={"X-Evaluation-CSRF": csrf},
    )
    assert invalid.status_code == 400
    assert client.get("/api/evaluation/export.csv").status_code == 409

    for candidate, rating in (("A", 6), ("B", 5)):
        response = client.put(
            f"/api/evaluation/reviews/q1/{candidate}",
            json={
                "rating": rating,
                "comment": "This is a grounded and actionable advising response.",
            },
            headers={"X-Evaluation-CSRF": csrf},
        )
        assert response.status_code == 200

    revealed = client.get("/api/evaluation/workspace").get_data(as_text=True)
    assert "Azure GPT-4o mini" in revealed
    assert "Azure GPT-5.6 Luna" in revealed
    assert client.get("/api/evaluation/summary").get_json()["available"] is True

    export = client.get("/api/evaluation/export.csv")
    assert export.status_code == 200
    assert "Azure GPT-4o mini" in export.get_data(as_text=True)
