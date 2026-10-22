"""Admin-token, rate-limit, and history edge contracts for app.py routes."""

import pytest

import app as app_module

TOKEN = "unit-test-admin-token-value"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DOCS_LOAD_TOKEN", TOKEN)
    monkeypatch.setattr(
        app_module, "_rate_limiter", app_module._FixedWindowRateLimiter()
    )
    return app_module.app.test_client()


def test_admin_routes_disabled_without_configured_token(monkeypatch):
    monkeypatch.delenv("DOCS_LOAD_TOKEN", raising=False)
    client = app_module.app.test_client()
    for path in ("/api/load-docs/status", "/api/docs-stats"):
        response = client.get(path, headers={"X-Admin-Token": "anything"})
        assert response.status_code == 503, path
        assert "DOCS_LOAD_TOKEN" in response.get_json()["error"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Admin-Token": "wrong-token"},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Basic " + TOKEN},
        {"Authorization": "Bearer "},
    ],
)
def test_admin_routes_reject_missing_or_wrong_credentials(client, headers):
    response = client.get("/api/load-docs/status", headers=headers)
    assert response.status_code == 401
    assert response.get_json() == {"error": "Admin authorization required"}


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Admin-Token": TOKEN},
        {"Authorization": f"Bearer {TOKEN}"},
        {"Authorization": f"bEaReR {TOKEN}"},
    ],
)
def test_admin_routes_accept_header_or_bearer_token(client, headers):
    response = client.get("/api/load-docs/status", headers=headers)
    assert response.status_code == 200


def test_docs_stats_hides_internal_errors(client, monkeypatch):
    def boom():
        raise RuntimeError("chroma exploded at /srv/secret/path")

    monkeypatch.setattr(app_module, "get_chatbot", boom)
    response = client.get("/api/docs-stats", headers={"X-Admin-Token": TOKEN})
    assert response.status_code == 500
    assert response.get_json() == {"error": "Unable to read document statistics"}
    assert "secret" not in response.get_data(as_text=True)


def test_load_docs_rate_limits_after_configured_attempts(client, monkeypatch):
    monkeypatch.setenv("DOCS_LOAD_RATE_LIMIT", "2")
    monkeypatch.setenv("DOCS_LOAD_RATE_WINDOW_SECONDS", "600")
    started = []

    def fake_job():
        started.append(1)
        return {"state": "queued"}, 202

    monkeypatch.setattr(app_module, "_begin_docs_job", fake_job)
    headers = {"X-Admin-Token": TOKEN}
    url = "/api/load-docs?async=true"

    assert client.post(url, headers=headers).status_code == 202
    assert client.post(url, headers=headers).status_code == 202
    limited = client.post(url, headers=headers)

    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 600
    assert len(started) == 2  # the blocked attempt never queued a rebuild


def test_load_docs_auth_failure_does_not_consume_rate_limit(client, monkeypatch):
    monkeypatch.setenv("DOCS_LOAD_RATE_LIMIT", "1")
    monkeypatch.setattr(
        app_module, "_begin_docs_job", lambda: ({"state": "queued"}, 202)
    )
    for _ in range(3):
        assert client.post("/api/load-docs?async=true").status_code == 401
    ok = client.post("/api/load-docs?async=true", headers={"X-Admin-Token": TOKEN})
    assert ok.status_code == 202


def test_rate_limiter_windows_and_keys_are_independent(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(app_module.time, "monotonic", lambda: now[0])
    limiter = app_module._FixedWindowRateLimiter()

    assert limiter.allow("a", 1, 60) == (True, 0)
    allowed, retry = limiter.allow("a", 1, 60)
    assert allowed is False and retry == 60
    assert limiter.allow("b", 1, 60) == (True, 0)

    now[0] += 45
    allowed, retry = limiter.allow("a", 1, 60)
    assert allowed is False and retry == 15

    now[0] += 15
    assert limiter.allow("a", 1, 60) == (True, 0)


def test_rate_limiter_evicts_oldest_key_when_full():
    limiter = app_module._FixedWindowRateLimiter(max_keys=2)
    for key in ("a", "b", "c"):
        assert limiter.allow(key, 1, 3600)[0] is True
    # "a" was evicted, so it gets a fresh allowance; "c" is still limited.
    assert limiter.allow("a", 1, 3600)[0] is True
    assert limiter.allow("c", 1, 3600)[0] is False


@pytest.mark.parametrize("bad", ["bad id", "../secrets", "x" * 500])
def test_history_get_rejects_malformed_conversation_id(bad):
    response = app_module.app.test_client().get(
        "/api/history", query_string={"conversation_id": bad}
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "conversation_id is invalid"}


@pytest.mark.parametrize("payload", [["tab-1"], "tab-1", 42, None])
def test_clear_history_treats_non_object_body_as_session_default(payload):
    client = app_module.app.test_client()
    response = client.post("/api/clear-history", json=payload)
    assert response.status_code == 200
    assert response.get_json() == {"success": True}


def test_clear_history_rejects_non_string_conversation_id():
    response = app_module.app.test_client().post(
        "/api/clear-history", json={"conversation_id": 123}
    )
    assert response.status_code == 400


def test_history_hides_store_failures(monkeypatch):
    class Broken:
        def snapshot(self, *_a, **_k):
            raise RuntimeError("redis password=hunter2")

    monkeypatch.setattr(app_module, "_history_store", Broken())
    response = app_module.app.test_client().get("/api/history")
    assert response.status_code == 500
    assert response.get_json() == {"error": "Unable to read conversation history"}
    assert "hunter2" not in response.get_data(as_text=True)
