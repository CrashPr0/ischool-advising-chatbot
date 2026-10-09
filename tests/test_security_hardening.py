"""Auth, session, and client-IP hardening that does not call a model."""

import csv
import io
import json
import logging
import re

import pytest

import app as app_module


@pytest.fixture(autouse=True)
def _fresh_rate_limiter(monkeypatch):
    monkeypatch.setattr(app_module, "_rate_limiter", app_module._FixedWindowRateLimiter())


class FakeChatbot:
    def chat(self, message, history=None):
        return {
            "user_message": message,
            "response": "ok",
            "source": "test",
            "confidence": 1.0,
            "retrieved_docs": [],
        }


def _cookie_parts(response):
    return [
        part.strip()
        for header in response.headers.getlist("Set-Cookie")
        for part in header.split(";")
    ]


def _cookie_is_secure(response) -> bool:
    return any(part.lower() == "secure" for part in _cookie_parts(response))


def _spoofed_identity_headers():
    return {
        "X-MS-CLIENT-PRINCIPAL-NAME": "reviewer@sjsu.edu",
        "X-MS-CLIENT-PRINCIPAL": "eyJhbGciOiJub25lIn0",
        "X-MS-CLIENT-PRINCIPAL-ID": "00000000-0000-0000-0000-000000000000",
        "X-MS-CLIENT-PRINCIPAL-IDP": "aad",
    }


def _clear_review_auth(monkeypatch):
    monkeypatch.delenv("WEBSITE_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("EVAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("EVAL_ALLOWED_REVIEWERS", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.delenv("WEBSITE_INSTANCE_ID", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)


@pytest.mark.parametrize("flag", [None, "False", "false", "0", "off"])
def test_spoofed_easy_auth_header_does_not_open_review_data(monkeypatch, flag):
    _clear_review_auth(monkeypatch)
    if flag is None:
        monkeypatch.delenv("WEBSITE_AUTH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("WEBSITE_AUTH_ENABLED", flag)
    client = app_module.app.test_client()
    public = {"REMOTE_ADDR": "203.0.113.50"}
    headers = _spoofed_identity_headers()

    denied = client.post(
        "/api/evaluation/session",
        json={"reviewer": "reviewer@sjsu.edu"},
        headers=headers,
        environ_base=public,
    )

    assert denied.status_code == 503
    for path in (
        "/api/evaluation/workspace",
        "/api/evaluation/summary",
        "/api/evaluation/export.csv",
    ):
        assert client.get(path, headers=headers, environ_base=public).status_code == 401
    status = client.get(
        "/api/evaluation/session", headers=headers, environ_base=public
    ).get_json()
    assert status["authenticated"] is False
    assert status["entra_identity_available"] is False
    assert status["reviewer"] is None


def test_spoofed_header_cannot_skip_the_access_token(monkeypatch):
    _clear_review_auth(monkeypatch)
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    monkeypatch.setenv("EVAL_ALLOWED_REVIEWERS", "reviewer@sjsu.edu")
    client = app_module.app.test_client()
    public = {"REMOTE_ADDR": "203.0.113.60"}

    rejected = client.post(
        "/api/evaluation/session",
        json={"reviewer": "reviewer@sjsu.edu", "access_token": "nope"},
        headers=_spoofed_identity_headers(),
        environ_base=public,
    )
    assert rejected.status_code == 401

    missing_name = client.post(
        "/api/evaluation/session",
        json={"access_token": "test-review-token"},
        headers=_spoofed_identity_headers(),
        environ_base=public,
    )
    assert missing_name.status_code == 400

    accepted = client.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer@SJSU.edu", "access_token": "test-review-token"},
        headers={"X-MS-CLIENT-PRINCIPAL-NAME": "attacker@evil.example"},
        environ_base=public,
    )
    assert accepted.status_code == 200
    assert accepted.get_json()["reviewer"] == "reviewer@sjsu.edu"


def test_easy_auth_uses_platform_identity_and_ignores_a_bad_token(monkeypatch):
    _clear_review_auth(monkeypatch)
    monkeypatch.setenv("WEBSITE_AUTH_ENABLED", "True")
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    client = app_module.app.test_client()

    signed_in = client.post(
        "/api/evaluation/session",
        json={"reviewer": "Ignored Name", "access_token": "wrong"},
        headers={"X-MS-CLIENT-PRINCIPAL-NAME": "reviewer@sjsu.edu"},
        environ_base={"REMOTE_ADDR": "203.0.113.70"},
    )

    assert signed_in.status_code == 200
    assert signed_in.get_json()["reviewer"] == "reviewer@sjsu.edu"
    # The reviewer session survives a later request. The identity flag tracks
    # the header on that request, which Easy Auth injects only while it is on.
    without_header = client.get("/api/evaluation/session").get_json()
    assert without_header["authenticated"] is True
    assert without_header["entra_identity_available"] is False
    with_header = client.get(
        "/api/evaluation/session",
        headers={"X-MS-CLIENT-PRINCIPAL-NAME": "reviewer@sjsu.edu"},
    ).get_json()
    assert with_header["authenticated"] is True
    assert with_header["entra_identity_available"] is True


def test_easy_auth_without_a_platform_identity_stays_disabled(monkeypatch):
    _clear_review_auth(monkeypatch)
    monkeypatch.setenv("WEBSITE_AUTH_ENABLED", "True")
    client = app_module.app.test_client()

    response = client.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer One"},
        headers={"X-MS-CLIENT-PRINCIPAL": "not-a-signed-in-user"},
        environ_base={"REMOTE_ADDR": "203.0.113.80"},
    )

    assert response.status_code == 503


def test_localhost_sign_in_still_works_and_ignores_the_header(monkeypatch):
    _clear_review_auth(monkeypatch)
    client = app_module.app.test_client()

    response = client.post(
        "/api/evaluation/session",
        json={"reviewer": "Local Reviewer"},
        headers=_spoofed_identity_headers(),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["authenticated"] is True
    assert body["reviewer"] == "Local Reviewer"


def test_forwarded_localhost_does_not_bypass_disabled_evaluation(monkeypatch):
    _clear_review_auth(monkeypatch)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "ischooladvisingchatbot")
    client = app_module.app.test_client()

    response = client.post(
        "/api/evaluation/session",
        json={"reviewer": "Local Reviewer"},
        headers={"X-Forwarded-For": "127.0.0.1, 203.0.113.10"},
        environ_base={"REMOTE_ADDR": "10.0.0.4"},
    )

    assert response.status_code == 503


def _benchmark_payload():
    return {
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


def _save_both(client, csrf, comment, accuracy):
    """Save both candidates using the four 1–5 dimension scores."""
    for candidate in ("A", "B"):
        response = client.put(
            f"/api/evaluation/reviews/q1/{candidate}",
            json={
                "accuracy": accuracy,
                "tone": 4,
                "succinctness": 4,
                "edge_cases": 3,
                "comment": comment,
            },
            headers={"X-Evaluation-CSRF": csrf},
        )
        assert response.status_code == 200, response.get_json()


def test_csv_export_omits_other_reviewers_comments(monkeypatch, tmp_path):
    _clear_review_auth(monkeypatch)
    results_path = tmp_path / "results.json"
    results_path.write_text(json.dumps(_benchmark_payload()), encoding="utf-8")
    monkeypatch.setenv("EVAL_RESULTS_PATH", str(results_path))
    monkeypatch.setenv("EVAL_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    monkeypatch.setattr(app_module, "_evaluation_store", None)
    monkeypatch.setattr(app_module, "_evaluation_store_key", None)
    alpha = app_module.app.test_client()
    beta = app_module.app.test_client()
    stranger = app_module.app.test_client()

    alpha_login = alpha.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer Alpha", "access_token": "test-review-token"},
    )
    beta_login = beta.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer Beta", "access_token": "test-review-token"},
    )
    assert alpha_login.status_code == 200
    assert beta_login.status_code == 200
    _save_both(alpha, alpha_login.get_json()["csrf"], "Alpha kept this comment private.", 5)
    _save_both(beta, beta_login.get_json()["csrf"], "Beta kept this comment private.", 3)

    assert stranger.get("/api/evaluation/export.csv").status_code == 401
    alpha_export = alpha.get("/api/evaluation/export.csv")
    beta_export = beta.get("/api/evaluation/export.csv")
    assert alpha_export.status_code == 200
    assert beta_export.status_code == 200
    alpha_text = alpha_export.get_data(as_text=True)
    beta_text = beta_export.get_data(as_text=True)
    assert "Alpha kept this comment private." in alpha_text
    assert "Beta kept this comment private." not in alpha_text
    assert "Beta kept this comment private." in beta_text
    assert "Alpha kept this comment private." not in beta_text
    alpha_rows = list(csv.DictReader(io.StringIO(alpha_text)))
    assert {row["reviewer"] for row in alpha_rows} == {"Reviewer Alpha"}


def test_security_headers_and_healthz_stay_lightweight(monkeypatch):
    monkeypatch.setattr(app_module, "chatbot", None)
    client = app_module.app.test_client()

    for path in ("/healthz", "/", "/evaluation", "/api/does-not-exist"):
        response = client.get(path)
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert (
            response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        )
        policy = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "object-src 'none'" in policy
        assert "script-src 'self' https://cdn.jsdelivr.net" in policy
        assert "style-src 'self' https://fonts.googleapis.com" in policy
        assert "font-src 'self' https://fonts.gstatic.com" in policy
        assert "upgrade-insecure-requests" not in policy

    assert app_module.chatbot is None
    assert client.get("/healthz").get_json()["status"] == "ok"


def test_pdf_vendor_is_served_locally_under_existing_csp():
    client = app_module.app.test_client()
    page = client.get("/")
    assert b'data-jspdf-src="/static/vendor/jspdf/jspdf.umd.min.js?v=4.2.1"' in page.data
    assert not re.search(rb"<script\b[^>]*jspdf", page.data, re.I)
    policy = page.headers["Content-Security-Policy"]
    script_policy = next(
        part.strip()
        for part in policy.split(";")
        if part.strip().startswith("script-src ")
    )
    assert "'self'" in script_policy
    assert "'unsafe-eval'" not in script_policy
    assert "'unsafe-inline'" not in script_policy
    vendor = client.get("/static/vendor/jspdf/jspdf.umd.min.js?v=4.2.1")
    assert vendor.status_code == 200
    assert b"Version 4.2.1" in vendor.data
    assert "javascript" in vendor.content_type
    assert client.get("/static/vendor/jspdf/LICENSE").status_code == 200


def test_http_localhost_session_is_not_secure_and_still_round_trips(
    monkeypatch, ready_chat_index
):
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    monkeypatch.setattr(app_module, "chatbot", FakeChatbot())
    client = app_module.app.test_client()

    first = client.post("/api/chat", json={"message": "hello from http"})

    assert first.status_code == 200
    assert not _cookie_is_secure(first)
    assert any(part.lower() == "httponly" for part in _cookie_parts(first))
    history = client.get("/api/history").get_json()["history"]
    assert [entry["user"] for entry in history] == ["hello from http"]


def test_https_and_azure_forwarded_proto_set_the_secure_cookie(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.delenv("WEBSITE_INSTANCE_ID", raising=False)
    # An explicit false must not strip Secure from a real HTTPS response.
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")

    direct = app_module.app.test_client().get(
        "/api/history", base_url="https://127.0.0.1"
    )
    assert _cookie_is_secure(direct)

    spoofed = app_module.app.test_client().get(
        "/api/history",
        base_url="http://127.0.0.1",
        headers={"X-Forwarded-Proto": "https"},
    )
    assert not _cookie_is_secure(spoofed)

    monkeypatch.setenv("WEBSITE_SITE_NAME", "ischooladvisingchatbot")
    forwarded = app_module.app.test_client().get(
        "/api/history",
        base_url="http://127.0.0.1",
        headers={"X-Forwarded-Proto": "https"},
    )
    assert _cookie_is_secure(forwarded)


def test_explicit_secure_cookie_setting_forces_the_flag_on_http(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    response = app_module.app.test_client().get("/api/history")
    assert _cookie_is_secure(response)


def test_unset_session_secret_warns_clearly_in_production(monkeypatch, caplog):
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "ischooladvisingchatbot")
    with caplog.at_level(logging.WARNING):
        secret = app_module._resolve_session_secret()
    assert secret
    messages = [record.getMessage() for record in caplog.records]
    assert any("SESSION_SECRET_KEY is unset in production" in message for message in messages)
    assert all(secret not in message for message in messages)

    monkeypatch.setenv("SESSION_SECRET_KEY", "replace_with_a_long_random_secret")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        replacement = app_module._resolve_session_secret()
    assert replacement != "replace_with_a_long_random_secret"
    assert any(
        "SESSION_SECRET_KEY is unset in production" in record.getMessage()
        for record in caplog.records
    )


def test_development_session_secret_warning_is_not_the_production_warning(
    monkeypatch, caplog
):
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.delenv("WEBSITE_INSTANCE_ID", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    with caplog.at_level(logging.WARNING):
        app_module._resolve_session_secret()
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "SESSION_SECRET_KEY is not set; using an ephemeral session key" in message
        for message in messages
    )
    assert all("unset in production" not in message for message in messages)


def test_configured_session_secret_is_kept(monkeypatch, caplog):
    monkeypatch.setenv("SESSION_SECRET_KEY", "a-real-secret-value-that-is-long")
    monkeypatch.setenv("WEBSITE_SITE_NAME", "ischooladvisingchatbot")
    with caplog.at_level(logging.WARNING):
        resolved = app_module._resolve_session_secret()
    assert resolved == "a-real-secret-value-that-is-long"
    assert not any("SESSION_SECRET_KEY" in record.getMessage() for record in caplog.records)


def test_trusted_proxy_hop_count(monkeypatch):
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.delenv("WEBSITE_INSTANCE_ID", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    assert app_module._trusted_proxy_hops() == 0

    monkeypatch.setenv("WEBSITE_INSTANCE_ID", "instance")
    assert app_module._trusted_proxy_hops() == 1

    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "2")
    assert app_module._trusted_proxy_hops() == 2
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "0")
    assert app_module._trusted_proxy_hops() == 0
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "9")
    assert app_module._trusted_proxy_hops() == 5
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "nope")
    assert app_module._trusted_proxy_hops() == 1


def _post_chat(client, forwarded_for, remote_addr):
    headers = {}
    if forwarded_for is not None:
        headers["X-Forwarded-For"] = forwarded_for
    return client.post(
        "/api/chat",
        json={"message": "hello"},
        headers=headers,
        environ_base={"REMOTE_ADDR": remote_addr},
    )


def test_rate_limit_ignores_a_spoofed_forwarded_for(monkeypatch, ready_chat_index):
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.delenv("WEBSITE_INSTANCE_ID", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    monkeypatch.setenv("CHAT_RATE_LIMIT", "1")
    monkeypatch.setattr(app_module, "chatbot", FakeChatbot())
    client = app_module.app.test_client()

    assert _post_chat(client, "203.0.113.10", "198.51.100.4").status_code == 200
    assert _post_chat(client, "203.0.113.11", "198.51.100.4").status_code == 429
    assert _post_chat(client, "203.0.113.11", "198.51.100.5").status_code == 200


def test_rate_limit_keys_on_the_proxy_appended_client_ip(
    monkeypatch, ready_chat_index
):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "ischooladvisingchatbot")
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    monkeypatch.setenv("CHAT_RATE_LIMIT", "1")
    monkeypatch.setattr(app_module, "chatbot", FakeChatbot())
    client = app_module.app.test_client()

    assert _post_chat(client, "9.9.9.9, 203.0.113.10", "10.0.0.4").status_code == 200
    assert _post_chat(client, "8.8.8.8, 203.0.113.10", "10.0.0.4").status_code == 429
    assert _post_chat(client, "9.9.9.9, 203.0.113.11", "10.0.0.4").status_code == 200
    assert _post_chat(client, "127.0.0.1, 203.0.113.10", "10.0.0.4").status_code == 429


def test_sync_document_rebuild_cannot_block_the_worker(monkeypatch):
    monkeypatch.setenv("DOCS_LOAD_TOKEN", "test-docs-token")
    called = {"count": 0}

    class SpyChatbot:
        def process_documents(self):
            called["count"] += 1

    monkeypatch.setattr(app_module, "get_chatbot", lambda: SpyChatbot())
    client = app_module.app.test_client()
    headers = {"X-Admin-Token": "test-docs-token"}

    missing = client.post("/api/load-docs", headers=headers)
    explicit = client.post("/api/load-docs?async=false", headers=headers)

    assert missing.status_code == 400
    assert explicit.status_code == 400
    assert "async=true" in missing.get_json()["error"]
    assert called["count"] == 0
