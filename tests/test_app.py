"""Small API contract tests that do not call an external model."""

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
    assert [entry["user"] for entry in second_client.get("/api/history").get_json()["history"]] == [
        "beta"
    ]


def test_document_admin_routes_fail_closed_without_token(monkeypatch):
    monkeypatch.delenv("DOCS_LOAD_TOKEN", raising=False)
    client = app_module.app.test_client()

    response = client.post("/api/load-docs")

    assert response.status_code == 503
    assert "DOCS_LOAD_TOKEN" in response.get_json()["error"]
