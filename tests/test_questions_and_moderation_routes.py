"""Error-path contracts for /api/questions and suggestion moderation routes."""

import pytest

import app as app_module
from src.question_suggestions import load_suggestion_catalog


class _QuestionsBot:
    def get_knowledge_base_questions(self):
        return ["How do I apply?", "What is the MLIS core?"]


class _BrokenBot:
    def get_knowledge_base_questions(self):
        raise RuntimeError("secret internal path /srv/kb.sqlite")


def test_questions_route_returns_bot_questions(monkeypatch):
    monkeypatch.setattr(app_module, "chatbot", _QuestionsBot())
    response = app_module.app.test_client().get("/api/questions")
    assert response.status_code == 200
    assert response.get_json() == {
        "questions": ["How do I apply?", "What is the MLIS core?"]
    }


def test_questions_route_hides_internal_errors(monkeypatch):
    monkeypatch.setattr(app_module, "chatbot", _BrokenBot())
    response = app_module.app.test_client().get("/api/questions")
    assert response.status_code == 500
    assert response.get_json() == {"error": "Unable to read questions"}
    assert "secret" not in response.get_data(as_text=True)


@pytest.fixture
def reviewer(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    monkeypatch.setenv("DYNAMIC_SUGGESTIONS_ENABLED", "true")
    monkeypatch.setattr(app_module, "_suggestion_usage_store", None)
    monkeypatch.setattr(app_module, "_suggestion_usage_store_key", None)
    monkeypatch.setattr(
        app_module, "_rate_limiter", app_module._FixedWindowRateLimiter()
    )
    client = app_module.app.test_client()
    response = client.post(
        "/api/evaluation/session",
        json={"reviewer": "reviewer@sjsu.edu", "access_token": "test-review-token"},
    )
    assert response.status_code == 200, response.get_json()
    return client, {"X-Evaluation-CSRF": response.get_json()["csrf"]}


def _suggestion_id():
    return load_suggestion_catalog()["questions"][0]["id"]


def test_moderation_rejects_unknown_action_with_400(reviewer):
    client, headers = reviewer
    response = client.post(
        f"/api/evaluation/suggestion-ranking/{_suggestion_id()}/delete-everything",
        json={"reason": "nope"},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_moderation_rejects_unknown_suggestion_id_with_400(reviewer):
    client, headers = reviewer
    response = client.post(
        "/api/evaluation/suggestion-ranking/not-a-real-id/hide",
        json={"reason": "nope"},
        headers=headers,
    )
    assert response.status_code == 400


@pytest.mark.parametrize("body", [["hide"], "hide", 3])
def test_moderation_requires_json_object_body(reviewer, body):
    client, headers = reviewer
    response = client.post(
        f"/api/evaluation/suggestion-ranking/{_suggestion_id()}/hide",
        json=body,
        headers=headers,
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be a JSON object"}


def test_moderation_requires_session_before_anything_else(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    response = app_module.app.test_client().post(
        f"/api/evaluation/suggestion-ranking/{_suggestion_id()}/hide", json={}
    )
    assert response.status_code in (401, 403)


def test_moderation_is_rate_limited_with_retry_after(reviewer):
    client, headers = reviewer
    url = f"/api/evaluation/suggestion-ranking/{_suggestion_id()}/hide"
    statuses = [
        client.post(url, json={"reason": "r"}, headers=headers).status_code
        for _ in range(60)
    ]
    assert 429 not in statuses
    blocked = client.post(url, json={"reason": "r"}, headers=headers)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_moderation_store_failure_does_not_leak_details(reviewer, monkeypatch):
    client, headers = reviewer

    class Boom:
        def moderate(self, *args, **kwargs):
            raise RuntimeError("disk /var/secret exploded")

    monkeypatch.setattr(app_module, "get_suggestion_usage_store", lambda: Boom())
    response = client.post(
        f"/api/evaluation/suggestion-ranking/{_suggestion_id()}/hide",
        json={},
        headers=headers,
    )
    assert response.status_code == 500
    assert "secret" not in response.get_data(as_text=True)


def test_suggestion_events_export_failure_is_generic(reviewer, monkeypatch):
    client, _ = reviewer

    class Boom:
        def events_csv(self):
            raise RuntimeError("db path /var/secret")

    monkeypatch.setattr(app_module, "get_suggestion_usage_store", lambda: Boom())
    response = client.get("/api/evaluation/suggestion-events.csv")
    assert response.status_code == 500
    assert response.get_json() == {"error": "Unable to export suggestion events"}
