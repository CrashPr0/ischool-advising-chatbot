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
    assert b'id="program-select" name="program" hidden' in embed.data
    assert b'id="change-program-button"' in embed.data


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


def test_canvas_flags_render_compact_shell_and_versioned_assets():
    from bs4 import BeautifulSoup

    for flag in ("embed=canvas", "mode=canvas"):
        response = app_module.app.test_client().get(f"/?{flag}")
        soup = BeautifulSoup(response.data, "html.parser")
        assert "canvas-mode" in soup.body["class"]
        assert soup.select_one(".site-header").has_attr("hidden")
        assert soup.select_one('link[href$="canvas-mode.css?v=20261023-canvas-toolbar"]')
        assert soup.select_one('script[src$="canvas-mode.js?v=20261023-canvas-toolbar"]')
        assert not soup.select_one('script[src*="mobile-layout.js"]')
        for control in ("program-select", "change-program-button", "clear-button",
                        "export-chat-button", "advisor-handoff-button"):
            assert soup.select_one(f"#{control}")


def test_canvas_mode_persists_on_navigation_reload_and_new_chat():
    client = app_module.app.test_client()
    client.get("/?embed=canvas")
    assert client.post("/api/clear-history").status_code == 200
    for path in ("/", "/?unrelated=1"):
        page = client.get(path)
        assert b'class="is-embed canvas-mode"' in page.data
        assert b'<header class="site-header" hidden>' in page.data
    # A separate browser session keeps the standalone page.
    assert b"canvas-mode" not in app_module.app.test_client().get("/").data


def test_canvas_https_cookie_retains_existing_cross_site_policy():
    client = app_module.app.test_client()
    for path in ("/?mode=canvas", "/"):
        response = client.get(path, base_url="https://chat.example")
        cookie = _set_cookie(response).lower()
        assert "samesite=none" in cookie
        assert "secure" in cookie
        assert "partitioned" in cookie


def test_flag_off_matches_original_template_bytes():
    """Keep Canvas hooks conditional, including whitespace and mobile assets."""
    from pathlib import Path

    original = (Path(__file__).resolve().parents[1] / "templates/index.html").read_text()
    # Remove only our conditional hooks to reconstruct the original template.
    # This fixture stays useful after the feature is committed too.
    import re
    original = re.sub(r"{% if canvas_mode %}<link.*?{% endif %}", "", original, flags=re.S)
    original = re.sub(
        r'{% if canvas_mode %}<button id="canvas-topics-toggle".*?{% else %}(.*?){% endif %}',
        r"\1", original, flags=re.S,
    )
    original = original.replace('{% if canvas_mode %} hidden{% endif %}', '')
    original = re.sub(
        r'{% if canvas_mode %} *<button id="canvas-report-issue-button".*?{% endif %}',
        '', original, flags=re.S,
    )
    original = original.replace('{% if canvas_mode %} canvas-mode{% endif %}', '')
    original = re.sub(
        r"{% if canvas_mode %}<script.*?{% else %}(.*?){% endif %}",
        r"\1", original, flags=re.S,
    )
    with app_module.app.test_request_context("/"):
        from flask import render_template_string
        baseline = render_template_string(original, embed=False)
    assert app_module.app.test_client().get("/").data == baseline.encode()


def test_canvas_toolbar_report_and_closed_topics_are_conditional():
    from bs4 import BeautifulSoup

    for flag in ("embed=canvas", "mode=canvas"):
        soup = BeautifulSoup(app_module.app.test_client().get(f"/?{flag}").data, "html.parser")
        report = soup.select_one(".chat-header .header-actions #canvas-report-issue-button")
        assert report and report.get_text(strip=True) == "Report issue"
        assert report["type"] == "button"
        assert not report.has_attr("hidden")
        assert not report.has_attr("disabled")
        toggle = soup.select_one("button#canvas-topics-toggle")
        assert toggle["type"] == "button"
        assert toggle["aria-expanded"] == "false"
        topics = soup.find(id=toggle["aria-controls"])
        assert topics["id"] == "topic-list"
        assert topics.has_attr("hidden")
        assert topics.select(".topic-btn[data-topic]")

    for path in ("/", "/?embed=1"):
        soup = BeautifulSoup(app_module.app.test_client().get(path).data, "html.parser")
        assert not soup.select_one("#canvas-report-issue-button, #canvas-topics-toggle")
        assert not soup.select_one("#topic-list").has_attr("hidden")
        assert soup.select_one(".topics-header h2").get_text() == "Advising Topics"
