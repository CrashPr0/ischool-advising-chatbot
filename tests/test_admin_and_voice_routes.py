"""Admin-token handling for index rebuild routes and the voice-config route."""

import pytest

import app as app_module


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCS_LOAD_TOKEN", "test-docs-token")
    monkeypatch.setattr(app_module, "_rate_limiter", app_module._FixedWindowRateLimiter())
    monkeypatch.setattr(app_module, "_index_state_dir", lambda: str(tmp_path))
    monkeypatch.setattr(app_module, "_docs_job_state", {"status": "idle"})
    return app_module.app.test_client()


@pytest.mark.parametrize(
    "placeholder", ["replace_with_a_long_secret", "your_token", "ChangeMe", "  ", "change_me"]
)
def test_placeholder_token_disables_admin_routes(monkeypatch, client, placeholder):
    monkeypatch.setenv("DOCS_LOAD_TOKEN", placeholder)
    # Even presenting the placeholder itself must not authenticate.
    headers = {"X-Admin-Token": placeholder}
    assert client.get("/api/load-docs/status", headers=headers).status_code == 503
    assert client.post("/api/load-docs?async=true", headers=headers).status_code == 503


def test_missing_and_wrong_tokens_are_rejected(client):
    assert client.get("/api/load-docs/status").status_code == 401
    wrong = {"X-Admin-Token": "test-docs-tokeX"}
    assert client.get("/api/load-docs/status", headers=wrong).status_code == 401
    short = {"X-Admin-Token": "test"}
    assert client.get("/api/docs-stats", headers=short).status_code == 401


def test_bearer_token_is_accepted_case_insensitively(client):
    ok = client.get(
        "/api/load-docs/status", headers={"Authorization": "bearer  test-docs-token "}
    )
    assert ok.status_code == 200
    assert ok.get_json()["status"] == "idle"


def test_bearer_with_wrong_token_or_other_scheme_is_rejected(client):
    bad = client.get("/api/load-docs/status", headers={"Authorization": "Bearer nope"})
    basic = client.get(
        "/api/load-docs/status", headers={"Authorization": "Basic test-docs-token"}
    )
    assert bad.status_code == 401
    assert basic.status_code == 401


def test_sync_rebuild_is_rejected_before_rate_limit_is_consumed(client):
    headers = {"X-Admin-Token": "test-docs-token"}
    for _ in range(5):
        response = client.post("/api/load-docs", headers=headers)
        assert response.status_code == 400
        assert "async=true" in response.get_json()["error"]


def test_load_docs_rate_limit_returns_429_with_retry_after(monkeypatch, client):
    monkeypatch.setenv("DOCS_LOAD_RATE_LIMIT", "1")
    monkeypatch.setattr(
        app_module, "_begin_docs_job", lambda: ({"status": "queued"}, 202)
    )
    headers = {"X-Admin-Token": "test-docs-token"}

    first = client.post("/api/load-docs?async=yes", headers=headers)
    second = client.post("/api/load-docs?async=yes", headers=headers)

    assert first.status_code == 202
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert "rate limited" in second.get_json()["error"]


def test_voice_config_reflects_environment_without_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value")
    response = app_module.app.test_client().get("/api/voice-config")
    assert response.status_code == 200
    body = response.get_json()
    assert {"enabled", "mode", "languages", "default_language"} <= set(body)
    assert all({"code", "label"} <= set(lang) for lang in body["languages"])
    assert "sk-super-secret-value" not in response.get_data(as_text=True)
