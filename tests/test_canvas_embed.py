"""Canvas framing headers, compact embed mode, and embed session cookies."""

import logging

import app as app_module


def _policy(response) -> str:
    return response.headers["Content-Security-Policy"]


def _set_cookie(response) -> str:
    return "; ".join(response.headers.getlist("Set-Cookie"))


def test_chat_page_allows_canvas_ancestors_and_api_routes_stay_denied():
    client = app_module.app.test_client()
    page = client.get("/")
    policy = _policy(page)

    assert "X-Frame-Options" not in page.headers
    assert "DENY" not in page.headers.get("X-Frame-Options", "")
    assert "SAMEORIGIN" not in page.headers.get("X-Frame-Options", "")
    assert (
        "frame-ancestors 'self' https://sjsu.instructure.com https://*.instructure.com"
        in policy
    )

    for path in ("/api/history", "/api/status", "/healthz", "/readyz", "/evaluation"):
        locked = client.get(path)
        assert locked.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in _policy(locked)
        assert "sjsu.instructure.com" not in _policy(locked)


def test_embed_allowed_origins_override_and_invalid_values_keep_the_default(
    monkeypatch, caplog
):
    monkeypatch.setenv(
        "EMBED_ALLOWED_ORIGINS",
        "https://sjsu.instructure.com, https://sjsu.beta.instructure.com",
    )
    client = app_module.app.test_client()
    custom = client.get("/")
    assert "X-Frame-Options" not in custom.headers
    assert (
        "frame-ancestors https://sjsu.instructure.com https://sjsu.beta.instructure.com"
        in _policy(custom)
    )
    assert client.get("/api/history").headers["X-Frame-Options"] == "DENY"

    monkeypatch.setenv("EMBED_ALLOWED_ORIGINS", "'none'")
    refused = client.get("/")
    assert refused.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in _policy(refused)

    monkeypatch.setenv("EMBED_ALLOWED_ORIGINS", "https://evil.example; script-src *")
    with caplog.at_level(logging.WARNING):
        fallback = client.get("/")
    assert "Invalid EMBED_ALLOWED_ORIGINS" in caplog.text
    assert (
        "frame-ancestors 'self' https://sjsu.instructure.com https://*.instructure.com"
        in _policy(fallback)
    )
    assert "X-Frame-Options" not in fallback.headers


def test_embed_query_hides_the_header_and_leaves_the_full_page_alone():
    client = app_module.app.test_client()
    full = client.get("/")
    embed = client.get("/?embed=1")

    assert b'class="is-embed"' not in full.data
    assert b"<header class=\"site-header\">" in full.data
    assert b'class="is-embed"' in embed.data
    assert b'<header class="site-header" hidden>' in embed.data
    assert b"BS ISDA and MLIS advising assistant." in embed.data


def test_embed_session_cookie_is_cross_site_only_on_https(monkeypatch, ready_chat_index):
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.setattr(app_module, "chatbot", type("Bot", (), {
        "chat": lambda self, message, history=None, program=None: {
            "user_message": message,
            "response": "ok",
            "source": "test",
            "confidence": 1.0,
            "retrieved_docs": [],
        }
    })())

    http_client = app_module.app.test_client()
    http_page = http_client.get("/?embed=1")
    http_cookie = _set_cookie(http_page).lower()
    assert "samesite=none" not in http_cookie
    assert "samesite=lax" in http_cookie

    https = "https://ischooladvisingchatbot-dx4ynl.azurewebsites.net"
    embed_client = app_module.app.test_client()
    embed_page = embed_client.get("/?embed=1", base_url=https)
    embed_cookie = _set_cookie(embed_page)
    lowered = embed_cookie.lower()
    assert "samesite=none" in lowered
    assert "secure" in lowered
    assert "httponly" in lowered
    assert "partitioned" in lowered

    chat = embed_client.post(
        "/api/chat", json={"message": "What are the ISDA core courses?"}, base_url=https
    )
    assert chat.status_code == 200
    assert chat.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in _policy(chat)
    refreshed = _set_cookie(chat).lower()
    assert "samesite=none" in refreshed
    assert "secure" in refreshed
    assert "partitioned" in refreshed

    plain = app_module.app.test_client()
    plain_chat = plain.post(
        "/api/chat", json={"message": "What are the ISDA core courses?"}, base_url=https
    )
    assert plain_chat.status_code == 200
    plain_cookie = _set_cookie(plain_chat).lower()
    assert "samesite=lax" in plain_cookie
    assert "samesite=none" not in plain_cookie
    assert "partitioned" not in plain_cookie
