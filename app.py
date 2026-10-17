"""Flask web app for the iSchool Advising Chatbot."""

import csv
import hmac
import io
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from collections import OrderedDict

try:
    import fcntl
except ImportError:  # pragma: no cover - Azure and CI are Linux
    fcntl = None

# Add src to path so imports work consistently on Azure App Service.
# Oryx runs a release from an extracted directory under /tmp. Prefer the src
# directory beside this app.py so Python code, templates, and static assets all
# come from the same release. Only fall back to /home/site/wwwroot when the
# release-local directory is unavailable.
_src_candidates = [
    os.path.join(os.path.dirname(__file__), "src"),
    "/home/site/wwwroot/src",
]


def _prepend_first_existing_path(candidates: list[str]) -> str | None:
    """Put the first existing candidate at the front of ``sys.path``."""
    for candidate in candidates:
        if not os.path.isdir(candidate):
            continue
        while candidate in sys.path:
            sys.path.remove(candidate)
        sys.path.insert(0, candidate)
        return candidate
    return None


_prepend_first_existing_path(_src_candidates)

from flask import Flask, Response, jsonify, render_template, request, send_file, session
from flask.sessions import SecureCookieSessionInterface
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from evaluation_store import (
    EvaluationDataError,
    EvaluationStore,
    EvaluationValidationError,
)
from issue_reports import (
    REASON_LABELS,
    IssueReportStore,
    IssueReportValidationError,
    build_issue_report,
)
from voice_config import get_voice_config
from program_scope import normalize_program

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _first_existing_dir(candidates: list[str], fallback: str) -> str:
    for d in candidates:
        try:
            if os.path.isdir(d):
                return d
        except Exception:
            pass
    return fallback


_TEMPLATES_DIR = _first_existing_dir(
    [os.path.join(_BASE_DIR, "templates"), "/home/site/wwwroot/templates"],
    os.path.join(_BASE_DIR, "templates"),
)
_STATIC_DIR = _first_existing_dir(
    [os.path.join(_BASE_DIR, "static"), "/home/site/wwwroot/static"],
    os.path.join(_BASE_DIR, "static"),
)

app = Flask(__name__, template_folder=_TEMPLATES_DIR, static_folder=_STATIC_DIR)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an integer setting safely and clamp it to an operational range."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using %s", name, default)
        value = default
    return max(minimum, min(value, maximum))


def _looks_like_placeholder(value: str | None) -> bool:
    """Identify the template values that must never be used as secrets."""
    normalized = (value or "").strip().lower()
    return not normalized or normalized.startswith(
        ("replace_with_", "your_", "change_me", "changeme")
    )


def _on_app_service() -> bool:
    """True when Azure App Service is hosting this process.

    The platform sets these variables and places the worker behind its
    front end. That front end is the only proxy whose forwarded headers
    identify the real client.
    """
    return bool(
        os.getenv("WEBSITE_SITE_NAME", "").strip()
        or os.getenv("WEBSITE_INSTANCE_ID", "").strip()
    )


def _is_production() -> bool:
    if _on_app_service():
        return True
    explicit = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "")).strip().lower()
    return explicit in {"production", "prod"}


def _resolve_session_secret() -> str:
    """Return the configured session key, or a one-process development key."""
    configured = os.getenv("SESSION_SECRET_KEY")
    if isinstance(configured, str) and not _looks_like_placeholder(configured):
        return configured
    if _is_production():
        logger.warning(
            "SESSION_SECRET_KEY is unset in production; using an ephemeral "
            "session key. Set a stable SESSION_SECRET_KEY in the App Service "
            "configuration before students use the site. Without it, reviewer "
            "sessions reset on every restart and are rejected by other workers."
        )
    else:
        # An ephemeral development key prevents accidentally running with a
        # known default, while the warning makes the restart trade-off visible.
        logger.warning("SESSION_SECRET_KEY is not set; using an ephemeral session key")
    return secrets.token_hex(32)


def _trusted_proxy_hops() -> int:
    """How many reverse proxies may append ``X-Forwarded-*`` values.

    App Service's front end is one hop: the rightmost value is the one it
    added, and a client-supplied prefix is ignored. Zero hops anywhere else
    means the header is not trusted at all. ``TRUSTED_PROXY_COUNT`` overrides
    the platform default when another trusted proxy, such as Front Door, sits
    in front. Values outside 0–5 are clamped.
    """
    raw = os.getenv("TRUSTED_PROXY_COUNT")
    if raw is not None and raw.strip():
        try:
            hops = int(raw)
        except ValueError:
            _warn_once(
                "trusted-proxy-count",
                "Invalid TRUSTED_PROXY_COUNT %r; using the platform default",
                raw,
            )
        else:
            clamped = max(0, min(hops, 5))
            if clamped != hops:
                _warn_once(
                    "trusted-proxy-count-range",
                    "TRUSTED_PROXY_COUNT %s is outside 0-5; clamping",
                    hops,
                )
            return clamped
    return 1 if _on_app_service() else 0


_once_warnings: set[str] = set()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _once_warnings:
        return
    _once_warnings.add(key)
    logger.warning(message, *args)


class _TrustedProxyMiddleware:
    """Apply :class:`ProxyFix` only for the configured trusted hop count.

    The hop count is read on each request so tests and App Service settings
    agree without recycling the interpreter. A count of zero leaves
    ``REMOTE_ADDR`` and the URL scheme untouched, which is what local HTTP
    development needs.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        hops = _trusted_proxy_hops()
        if hops < 1:
            return self.wsgi_app(environ, start_response)
        # Trust the client address and the original scheme only. Host and
        # prefix stay untrusted so a forwarded host cannot retarget links.
        return ProxyFix(
            self.wsgi_app,
            x_for=hops,
            x_proto=hops,
            x_host=0,
            x_port=0,
            x_prefix=0,
        )(environ, start_response)


def _session_cookie_should_be_secure() -> bool:
    """Mark the session cookie Secure on HTTPS, including Azure's front end.

    TLS ends at that front end, so the worker often sees plain HTTP.
    ``_TrustedProxyMiddleware`` restores the visitor's scheme first. An
    explicit ``SESSION_COOKIE_SECURE=true`` forces the flag even on HTTP.
    An explicit false does not remove it from an HTTPS response. Plain HTTP
    on localhost stays usable.
    """
    if os.getenv("SESSION_COOKIE_SECURE", "").strip().lower() == "true":
        return True
    try:
        scheme = (request.scheme or "").lower()
    except RuntimeError:
        return False
    return scheme in {"https", "wss"}


class _HttpsAwareSessionInterface(SecureCookieSessionInterface):
    def get_cookie_secure(self, app: Flask) -> bool:
        return _session_cookie_should_be_secure()


_session_samesite = os.getenv("SESSION_COOKIE_SAMESITE", "Lax").strip().capitalize()
if _session_samesite not in {"Lax", "Strict", "None"}:
    logger.warning("Invalid SESSION_COOKIE_SAMESITE; using Lax")
    _session_samesite = "Lax"

# Flask's signed session cookie carries only an opaque identifier. The actual
# transcript stays server-side and is bounded by _SessionHistoryStore below.
# SESSION_COOKIE_SECURE in config is the HTTP fallback; the session interface
# turns the flag on when the request itself is HTTPS.
app.config.update(
    SECRET_KEY=_resolve_session_secret(),
    MAX_CONTENT_LENGTH=_env_int("MAX_REQUEST_BYTES", 64 * 1024, 1024, 2 * 1024 * 1024),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=_session_samesite,
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
)
app.session_interface = _HttpsAwareSessionInterface()

# Templates load same-origin JS plus Google Fonts on the student page and
# Chart.js from jsDelivr on the review page. No inline scripts.
_CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com",
    ]
)


@app.after_request
def _apply_security_headers(response):
    """Attach browser hardening headers without touching the response body."""
    response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


class _SessionHistoryStore:
    """Small bounded in-memory store for per-browser conversation history.

    This intentionally stores no transcript in the signed Flask cookie. It is
    suitable for a single worker and gives safe behavior on a restart (the
    session simply starts empty); deployments with multiple workers can swap
    this implementation for Redis without changing the route contract.
    """

    def __init__(self, max_sessions: int, max_messages: int, ttl_seconds: int):
        self.max_sessions = max_sessions
        self.max_messages = max_messages
        self.ttl_seconds = ttl_seconds
        self._sessions: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        expired = [
            session_id
            for session_id, (last_seen, _) in self._sessions.items()
            if now - last_seen > self.ttl_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)

    def snapshot(self, session_id: str, *, include_model: bool = False) -> list[dict]:
        """Return a copy of one session's turns.

        The public history payload stays ``user``, ``assistant``, and
        ``source``. ``include_model`` is only for an opted-in issue report
        that needs the provider and model recorded on that turn.
        """
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            record = self._sessions.get(session_id)
            if record is None:
                return []
            self._sessions.move_to_end(session_id)
            self._sessions[session_id] = (now, record[1])
            copied = []
            for entry in record[1]:
                if include_model:
                    copied.append(dict(entry))
                    continue
                copied.append(
                    {
                        "user": entry.get("user"),
                        "assistant": entry.get("assistant"),
                        "source": entry.get("source"),
                    }
                )
            return copied

    def append(self, session_id: str, result: dict) -> None:
        now = time.monotonic()
        entry = {
            "user": str(result.get("user_message", "")),
            "assistant": str(result.get("response", "")),
            "source": result.get("source"),
        }
        provider = result.get("provider")
        model = result.get("model")
        if isinstance(provider, str) and provider.strip():
            entry["provider"] = provider.strip()[:120]
        if isinstance(model, str) and model.strip():
            entry["model"] = model.strip()[:120]
        with self._lock:
            self._purge(now)
            history = self._sessions.setdefault(session_id, (now, []))[1]
            history.append(entry)
            del history[: -self.max_messages]
            self._sessions.move_to_end(session_id)
            self._sessions[session_id] = (now, history)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


class _FixedWindowRateLimiter:
    """Dependency-free, bounded rate limiter for expensive API operations."""

    def __init__(self, max_keys: int = 10_000):
        self._buckets: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= window_seconds:
                start, count = now, 0
            if count >= limit:
                retry_after = max(1, int(window_seconds - (now - start)))
                return False, retry_after
            self._buckets[key] = (start, count + 1)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
            return True, 0


_history_store = _SessionHistoryStore(
    max_sessions=_env_int("MAX_CHAT_SESSIONS", 10_000, 10, 100_000),
    max_messages=_env_int("MAX_HISTORY_MESSAGES", 50, 1, 500),
    ttl_seconds=_env_int("CHAT_SESSION_TTL_SECONDS", 3_600, 60, 86_400),
)
_rate_limiter = _FixedWindowRateLimiter()


def _session_id() -> str:
    """Get or create the opaque ID used to isolate a browser's history."""
    value = session.get("chat_session_id")
    if not isinstance(value, str) or len(value) < 20 or len(value) > 128:
        value = secrets.token_urlsafe(32)
        session["chat_session_id"] = value
    return value


# Client-chosen tab ids. Scoped under the server session id, so one browser
# cannot read another browser's conversation by guessing the id.
_CONVERSATION_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _optional_conversation_id(source: dict | None) -> str | None:
    """Return a validated tab id, or None when the client did not send one.

    An empty string is treated as omitted so older clients stay compatible.
    A present but malformed value raises ValueError (the route returns 400).
    """
    if not isinstance(source, dict) or "conversation_id" not in source:
        return None
    raw = source.get("conversation_id")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or _CONVERSATION_ID.fullmatch(raw) is None:
        raise ValueError("conversation_id is invalid")
    return raw


def _history_key(conversation_id: str | None) -> str:
    """Isolate each tab under the browser session without changing the default key."""
    session_id = _session_id()
    if conversation_id is None:
        return session_id
    return f"{session_id}:{conversation_id}"


def _configured_chat_model() -> tuple[str, str]:
    """Provider and model this process will call. Empty strings if unknown."""
    try:
        from config import Config

        provider = Config.normalize_provider(Config.DEFAULT_LLM_PROVIDER)
    except Exception:
        return "", ""
    model_attr = {
        "azure_openai": "AZURE_OPENAI_DEPLOYMENT",
        "openai": "OPENAI_MODEL",
        "anthropic": "ANTHROPIC_MODEL",
        "huggingface": "HUGGINGFACE_MODEL",
        "openai_compatible": "OPENAI_COMPATIBLE_MODEL",
        "deepseek": "DEEPSEEK_MODEL",
        "qwen": "QWEN_MODEL",
        "zhipu": "ZHIPU_MODEL",
    }.get(provider, "")
    model = ""
    if model_attr:
        model = str(getattr(Config, model_attr, "") or "").strip()
    return provider[:120], model[:120]


def _stamp_chat_model(result: dict) -> tuple[str, str]:
    """Record the configured model on a chat result so a later report can cite it."""
    provider, model = _configured_chat_model()
    if provider:
        result["provider"] = provider
    if model:
        result["model"] = model
    return provider, model


def _client_key() -> str:
    """Return the address used as a rate-limit bucket.

    Behind App Service, the proxy middleware has already replaced
    ``remote_addr`` with the address the front end appended. Otherwise the
    forwarded header is ignored, so a caller cannot mint a new bucket.
    """
    return request.remote_addr or "unknown"


def _admin_error():
    """Validate the admin token for document-index rebuilds."""
    expected = os.getenv("DOCS_LOAD_TOKEN", "").strip()
    if _looks_like_placeholder(expected):
        return (
            jsonify(
                {
                    "error": (
                        "Document loading is disabled until DOCS_LOAD_TOKEN is "
                        "configured"
                    )
                }
            ),
            503,
        )

    provided = request.headers.get("X-Admin-Token", "")
    authorization = request.headers.get("Authorization", "")
    if not provided and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        return jsonify({"error": "Admin authorization required"}), 401
    return None


def _get_allowed_origins():
    """Read allowed CORS origins from environment."""
    raw = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5000,http://127.0.0.1:5000,"
        "https://ischooladvisingchatbot-dx4ynl.azurewebsites.net",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


allowed_origins = _get_allowed_origins()
CORS(
    app, resources={r"/api/*": {"origins": allowed_origins}}, supports_credentials=True
)
logger.info("CORS allowed origins: %s", allowed_origins)
# Outermost wrapper so the client address is corrected before Flask or CORS
# see the request. Local processes keep hop count 0 and ignore the headers.
app.wsgi_app = _TrustedProxyMiddleware(app.wsgi_app)

# Initialize chatbot (singleton)
chatbot = None
_chatbot_lock = threading.Lock()
_rag_chatbot_class = None

_docs_job_lock = threading.Lock()
_docs_job_state = {"status": "idle"}
# Flush the 202 before the rebuild thread competes for the GIL. Tests shorten this.
_DOCS_INDEX_START_DELAY_SECONDS = 1.0

_evaluation_store = None
_evaluation_store_key = None
_evaluation_store_lock = threading.Lock()
_issue_report_store = None
_issue_report_store_key = None
_issue_report_store_lock = threading.Lock()


def get_chatbot():
    """Get or create chatbot instance."""
    global chatbot, _rag_chatbot_class
    if chatbot is None:
        with _chatbot_lock:
            if chatbot is None:
                try:
                    # Import the RAG stack lazily. Chroma, LangChain, and local
                    # embedding dependencies are intentionally substantial;
                    # importing them while Gunicorn boots can exceed Azure App
                    # Service's startup-probe deadline and make lightweight
                    # routes such as /healthz and /evaluation unavailable.
                    # gunicorn.conf post_worker_init builds this singleton in
                    # a background thread only after the worker can already
                    # serve /healthz. Do not call get_chatbot during boot.
                    if _rag_chatbot_class is None:
                        from rag_chatbot import RAGAdvisingChatbot

                        _rag_chatbot_class = RAGAdvisingChatbot
                    chatbot = _rag_chatbot_class()
                    logger.info("Chatbot initialized successfully")
                except Exception:
                    logger.exception("Failed to initialize chatbot")
                    raise
    return chatbot


def get_evaluation_store():
    """Get the review store for the currently configured artifact paths."""
    global _evaluation_store, _evaluation_store_key
    from durable_storage import prepare_eval_db_path

    results_path = os.getenv(
        "EVAL_RESULTS_PATH", os.path.join(_BASE_DIR, "evals", "results", "latest.json")
    )
    # Copy a legacy reviews file onto /home/data before opening the store.
    # The copy never moves or deletes the original. An explicit EVAL_DB_PATH
    # that is not the durable default is used as-is.
    database_path = prepare_eval_db_path(os.getenv("EVAL_DB_PATH"), app_dir=_BASE_DIR)
    store_key = (os.path.abspath(results_path), os.path.abspath(database_path))
    if _evaluation_store is None or _evaluation_store_key != store_key:
        with _evaluation_store_lock:
            if _evaluation_store is None or _evaluation_store_key != store_key:
                _evaluation_store = EvaluationStore(*store_key)
                _evaluation_store_key = store_key
    return _evaluation_store


def get_issue_report_store() -> IssueReportStore:
    """Open the answer-report table in the same database as reviewer scores."""
    global _issue_report_store, _issue_report_store_key
    from durable_storage import prepare_eval_db_path

    database_path = prepare_eval_db_path(os.getenv("EVAL_DB_PATH"), app_dir=_BASE_DIR)
    store_key = os.path.abspath(database_path)
    if _issue_report_store is None or _issue_report_store_key != store_key:
        with _issue_report_store_lock:
            if _issue_report_store is None or _issue_report_store_key != store_key:
                _issue_report_store = IssueReportStore(store_key)
                _issue_report_store_key = store_key
    return _issue_report_store


def _evaluation_token() -> str:
    token = os.getenv("EVAL_ACCESS_TOKEN", "").strip()
    return "" if _looks_like_placeholder(token) else token


def _evaluation_allowed_reviewers() -> set[str]:
    """Return the configured case-insensitive reviewer email allowlist."""
    return {
        value.strip().lower()
        for value in os.getenv("EVAL_ALLOWED_REVIEWERS", "").split(",")
        if value.strip()
    }


def _is_local_request() -> bool:
    return (request.remote_addr or "") in {"127.0.0.1", "::1"}


def _easy_auth_enabled() -> bool:
    """True only when App Service Authentication is actually turned on.

    The platform sets ``WEBSITE_AUTH_ENABLED`` and, only in that mode,
    strips client-supplied ``X-MS-CLIENT-PRINCIPAL*`` headers before
    replacing them with the signed-in user. Without that switch the headers
    are ordinary request data.
    """
    return os.getenv("WEBSITE_AUTH_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _easy_auth_principal_name() -> str:
    """Return the platform identity, never a client-supplied header.

    Related Easy Auth headers (``X-MS-CLIENT-PRINCIPAL``,
    ``X-MS-CLIENT-PRINCIPAL-ID``, and ``X-MS-CLIENT-PRINCIPAL-IDP``) are not
    consulted. Sign-in falls back to ``EVAL_ACCESS_TOKEN`` or the disabled
    response when this returns an empty string.
    """
    if not _easy_auth_enabled():
        return ""
    return request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME", "").strip()


def _evaluation_reviewer() -> str | None:
    reviewer = session.get("evaluation_reviewer")
    return reviewer if isinstance(reviewer, str) and reviewer else None


def _evaluation_auth_error(*, require_csrf: bool = False):
    """Require a reviewer session and CSRF proof for review writes."""
    if not _evaluation_reviewer():
        return jsonify({"error": "Evaluation sign-in required"}), 401
    if require_csrf:
        expected = session.get("evaluation_csrf", "")
        provided = request.headers.get("X-Evaluation-CSRF", "")
        if not expected or not provided or not hmac.compare_digest(provided, expected):
            return jsonify({"error": "Invalid evaluation session token"}), 403
    return None


def _csv_rows_for_reviewer(csv_text: str, reviewer: str | None) -> str:
    """Drop every review row that belongs to someone else.

    The store export is an audit trail of the whole run. A signed-in
    reviewer may download their own comments only.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = list(reader.fieldnames or [])
    if "reviewer" not in fieldnames or not reviewer:
        raise ValueError("Review export is missing a reviewer column")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in reader:
        if row.get("reviewer") == reviewer:
            writer.writerow(row)
    return output.getvalue()


def _normalize_reviewer(value: object) -> str:
    reviewer = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(reviewer) < 2 or len(reviewer) > 100:
        raise ValueError("Reviewer name must contain between 2 and 100 characters")
    return reviewer


# Routes


@app.route("/")
def index():
    """Serve main page."""
    try:
        # Prefer Jinja rendering so `url_for('static', ...)` resolves correctly.
        return render_template("index.html")
    except Exception as e:
        logger.warning(
            "Failed to render templates/index.html, falling back to docs HTML: %s", e
        )

    # Absolute-URL fallback (works even if templates aren't discoverable).
    candidates = [
        os.path.join(_BASE_DIR, "docs", "index.html"),
        "/home/site/wwwroot/docs/index.html",
    ]
    for p in candidates:
        try:
            if os.path.isfile(p):
                return send_file(p)
        except Exception:
            pass

    return (
        "Chatbot backend is running, but the web UI HTML file was not found "
        "on the server. "
        "Try the API at /api/status or redeploy including the templates/docs folders."
    ), 500


@app.route("/evaluation")
def evaluation_dashboard():
    """Serve the human model-review workspace."""
    return render_template("evaluation.html")


@app.route("/api/evaluation/session", methods=["GET", "POST", "DELETE"])
def evaluation_session():
    """Create or inspect a reviewer session without storing the shared token."""
    if request.method == "DELETE":
        session.pop("evaluation_reviewer", None)
        session.pop("evaluation_csrf", None)
        return jsonify({"success": True})

    reviewer = _evaluation_reviewer()
    if request.method == "GET":
        return jsonify(
            {
                "authenticated": bool(reviewer),
                "reviewer": reviewer,
                "csrf": session.get("evaluation_csrf") if reviewer else None,
                "token_required": bool(_evaluation_token()),
                "email_allowlist_enabled": bool(_evaluation_allowed_reviewers()),
                "entra_identity_available": bool(_easy_auth_principal_name()),
            }
        )

    allowed, retry_after = _rate_limiter.allow(
        f"evaluation-login:{_client_key()}", 10, 300
    )
    if not allowed:
        response = jsonify({"error": "Too many sign-in attempts; try again later"})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    entra_reviewer = _easy_auth_principal_name()
    try:
        reviewer = _normalize_reviewer(entra_reviewer or data.get("reviewer"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    allowed_reviewers = _evaluation_allowed_reviewers()
    if allowed_reviewers:
        reviewer = reviewer.lower()
        if reviewer not in allowed_reviewers:
            return (
                jsonify({"error": "This email is not approved for this workspace"}),
                403,
            )

    expected = _evaluation_token()
    if not entra_reviewer:
        if expected:
            provided = str(data.get("access_token", ""))
            if not provided or not hmac.compare_digest(provided, expected):
                return jsonify({"error": "Invalid evaluation access token"}), 401
        elif not allowed_reviewers and not _is_local_request():
            return (
                jsonify(
                    {
                        "error": (
                            "Evaluation access is disabled until "
                            "EVAL_ACCESS_TOKEN, EVAL_ALLOWED_REVIEWERS, or Azure "
                            "App Service authentication is configured"
                        )
                    }
                ),
                503,
            )

    session["evaluation_reviewer"] = reviewer
    session["evaluation_csrf"] = secrets.token_urlsafe(32)
    return jsonify(
        {
            "authenticated": True,
            "reviewer": reviewer,
            "csrf": session["evaluation_csrf"],
        }
    )


@app.route("/api/evaluation/workspace", methods=["GET"])
def evaluation_workspace():
    """Return the current blind comparison and saved reviewer progress."""
    auth_error = _evaluation_auth_error()
    if auth_error:
        return auth_error
    try:
        return jsonify(get_evaluation_store().workspace(_evaluation_reviewer()))
    except EvaluationDataError as exc:
        return jsonify({"error": str(exc), "code": "benchmark_not_ready"}), 404
    except Exception:
        logger.exception("Unable to load evaluation workspace")
        return jsonify({"error": "Unable to load evaluation workspace"}), 500


@app.route("/api/evaluation/reviews/<question_id>/<candidate>", methods=["PUT"])
def save_evaluation_review(question_id: str, candidate: str):
    """Persist four 1–5 dimension scores and a short reviewer rationale."""
    auth_error = _evaluation_auth_error(require_csrf=True)
    if auth_error:
        return auth_error
    allowed, retry_after = _rate_limiter.allow(
        f"evaluation-review:{_client_key()}", 120, 60
    )
    if not allowed:
        response = jsonify({"error": "Review updates are temporarily rate limited"})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    try:
        review = get_evaluation_store().save_review(
            reviewer=_evaluation_reviewer(),
            question_id=question_id,
            candidate=candidate,
            accuracy=data.get("accuracy"),
            tone=data.get("tone"),
            succinctness=data.get("succinctness"),
            edge_cases=data.get("edge_cases"),
            comment=data.get("comment"),
        )
        return jsonify({"success": True, "review": review})
    except (EvaluationDataError, EvaluationValidationError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("Unable to save evaluation review")
        return jsonify({"error": "Unable to save evaluation review"}), 500


@app.route("/api/evaluation/summary", methods=["GET"])
def evaluation_summary():
    """Reveal aggregate model results after the reviewer finishes the run."""
    auth_error = _evaluation_auth_error()
    if auth_error:
        return auth_error
    try:
        return jsonify(get_evaluation_store().summary(_evaluation_reviewer()))
    except EvaluationDataError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        logger.exception("Unable to summarize evaluation reviews")
        return jsonify({"error": "Unable to summarize evaluation reviews"}), 500


@app.route("/api/evaluation/export.csv", methods=["GET"])
def export_evaluation_reviews():
    """Export the signed-in reviewer's rows from the current run."""
    auth_error = _evaluation_auth_error()
    if auth_error:
        return auth_error
    try:
        store = get_evaluation_store()
        summary = store.summary(_evaluation_reviewer())
        if not summary.get("available"):
            return jsonify({"error": summary["message"]}), 409
        csv_text = _csv_rows_for_reviewer(store.export_csv(), _evaluation_reviewer())
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=advising-model-reviews.csv"
            },
        )
    except EvaluationDataError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        logger.exception("Unable to export evaluation reviews")
        return jsonify({"error": "Unable to export evaluation reviews"}), 500


# Shown to students when this release has no matching index receipt.
# Internal status (missing, unreadable, mismatch) stays in the log.
_INDEX_NOT_READY_MESSAGE = (
    "The advising materials are still updating after a new release. "
    "Please try again in a few minutes."
)


def _index_readiness_report() -> dict:
    """Compare the on-disk index receipt with this release.

    Same inputs as ``/readyz``. The check only reads ``index_build.json``.
    It does not open Chroma or call a model, so ``/healthz`` can stay up
    while a deploy is still rebuilding the index.
    """
    from config import Config
    from durable_storage import assess_index_readiness

    return assess_index_readiness(
        Config.VECTOR_STORE_PATH,
        version_file=os.path.join(_BASE_DIR, "deployment_version.txt"),
        expected_embedding=Config.resolved_embedding(),
    )


def _chat_debug_timings_enabled() -> bool:
    """Include a timings object on /api/chat only when explicitly enabled."""
    return os.getenv("CHAT_DEBUG_TIMINGS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _record_anonymized_student_question(question: str) -> None:
    """Save a scrubbed question for popular-question analysis.

    The row is question text and a UTC timestamp only. A storage failure is
    logged and does not change the chat response.
    """
    try:
        from anonymized_questions import record_anonymized_question
        from durable_storage import prepare_eval_db_path

        database_path = prepare_eval_db_path(
            os.getenv("EVAL_DB_PATH"), app_dir=_BASE_DIR
        )
        record_anonymized_question(question, database_path)
    except Exception:
        logger.exception("Could not store an anonymized student question")


_POPULAR_QUESTION_WINDOWS = {7, 30, 90}


def _popular_questions_public_enabled() -> bool:
    """True only when staff explicitly publish the aggregated list."""
    return os.getenv("POPULAR_QUESTIONS_PUBLIC", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _popular_questions_days() -> int | None:
    """Return 7, 30, or 90. Missing or blank means 30. Anything else is invalid."""
    raw = request.args.get("days")
    if raw is None:
        return 30
    text = str(raw).strip()
    if not text:
        return 30
    if not text.isdigit() or int(text) not in _POPULAR_QUESTION_WINDOWS:
        return None
    return int(text)


def _popular_questions_payload(days: int) -> dict:
    from anonymized_questions import popular_questions
    from durable_storage import prepare_eval_db_path

    database_path = prepare_eval_db_path(os.getenv("EVAL_DB_PATH"), app_dir=_BASE_DIR)
    return {
        "days": days,
        "questions": popular_questions(database_path, days=days),
    }


def _advising_index_refusal():
    """Return a 503 response when chat must not call the model yet."""
    report = _index_readiness_report()
    if report.get("ready"):
        return None
    logger.info(
        "Chat refused because the advising index is %s (expected %s, receipt %s): %s",
        report.get("status"),
        report.get("expected_commit"),
        report.get("receipt_commit"),
        report.get("detail"),
    )
    return jsonify({"error": _INDEX_NOT_READY_MESSAGE}), 503


@app.route("/api/chat", methods=["POST"])
def chat():
    """Answer one student message for the current browser session.

    Earlier turns from the session store are passed into ``chat`` so a
    follow-up can use them. An optional ``conversation_id`` keeps each
    browser tab in its own history bucket under the same session. Omitting
    it preserves the single-conversation key. The new turn is stored only
    when the reply is not marked ``safety_tier`` ``crisis`` (that field
    arrives with the safety layer). A missing or stale index receipt
    returns 503 and does not load the model. ``/healthz`` does not use
    this check: the deploy workflow waits for liveness, then rebuilds the
    index through the admin load-docs route rather than through this one.
    """
    allowed, retry_after = _rate_limiter.allow(
        f"chat:{_client_key()}",
        _env_int("CHAT_RATE_LIMIT", 60, 1, 1_000),
        _env_int("CHAT_RATE_WINDOW_SECONDS", 60, 1, 3_600),
    )
    if not allowed:
        response = jsonify({"error": "Too many chat requests; please try again later"})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        user_message = data.get("message")
        if not isinstance(user_message, str):
            return jsonify({"error": "message must be a string"}), 400
        user_message = user_message.strip()
        max_length = _env_int("CHAT_MESSAGE_MAX_LENGTH", 4000, 1, 16_000)
        if not user_message:
            return jsonify({"error": "Empty message"}), 400
        if len(user_message) > max_length:
            return (
                jsonify({"error": f"Message exceeds the {max_length}-character limit"}),
                413,
            )
        try:
            conversation_id = _optional_conversation_id(data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        _record_anonymized_student_question(user_message)
        session_id = _history_key(conversation_id)

        # Validate the message before the index check so a bad request stays
        # a 400. The index check itself must run before get_chatbot(): that
        # call constructs the model client.
        refusal = _advising_index_refusal()
        if refusal is not None:
            return refusal

        # Snapshot is a copy. chat() may append onto that copy; the session
        # store is updated here so a crisis reply can be left out of history.
        chatbot_instance = get_chatbot()
        prior_turns = _history_store.snapshot(session_id)
        program = normalize_program(data.get("program"))
        result = chatbot_instance.chat(
            user_message,
            history=prior_turns,
            **({"program": program} if program else {}),
        )
        provider, model = _stamp_chat_model(result)
        if result.get("safety_tier") != "crisis":
            _history_store.append(session_id, result)

        payload = {
            "response": result["response"],
            "source": result["source"],
            "confidence": result["confidence"],
            "retrieved_docs": len(result["retrieved_docs"]),
        }
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        # Absent until the safety layer adds it. Include it only then so
        # older chatbot builds keep the previous JSON shape.
        if "safety_tier" in result:
            payload["safety_tier"] = result["safety_tier"]
        # Off unless CHAT_DEBUG_TIMINGS=true. The server still logs timings.
        if _chat_debug_timings_enabled() and isinstance(result.get("timings"), dict):
            payload["timings"] = result["timings"]
        return jsonify(payload)

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        logger.exception("Error in chat endpoint")
        return jsonify({"error": "Unable to process chat request"}), 500


@app.route("/api/status", methods=["GET"])
def status():
    """Get chatbot status."""
    try:
        chatbot_instance = get_chatbot()
        if _rag_chatbot_class is None:
            # get_chatbot() initializes this alongside the singleton.
            raise RuntimeError("RAG chatbot class was not initialized")
        providers = _rag_chatbot_class.get_available_providers()
        questions = chatbot_instance.get_knowledge_base_questions()

        return jsonify(
            {
                "status": "ready",
                "available_providers": providers,
                "hardcoded_questions": len(questions),
            }
        )
    except Exception:
        logger.exception("Failed to initialize chatbot status")
        return jsonify({"status": "error", "error": "Chatbot is unavailable"}), 500


def _deployment_version() -> str:
    """Return the commit SHA baked into this release, or a local placeholder."""
    version = "development"
    try:
        with open(
            os.path.join(_BASE_DIR, "deployment_version.txt"), encoding="utf-8"
        ) as handle:
            version = handle.read().strip() or version
    except OSError:
        pass
    return version


def _index_state_dir() -> str:
    """Directory shared by every Gunicorn worker for index job state."""
    from config import Config

    return Config.VECTOR_STORE_PATH


def _job_path() -> str:
    return os.path.join(_index_state_dir(), "index_job.json")


def _index_build_path() -> str:
    return os.path.join(_index_state_dir(), "index_build.json")


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_write_json(path: str, payload: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _pid_alive(pid: object) -> bool:
    """Return whether a worker recorded in the shared job file still exists."""
    try:
        pid_int = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    if os.path.isdir("/proc"):
        return os.path.isdir(f"/proc/{pid_int}")
    try:
        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


def _job_is_current(job: dict) -> bool:
    """A job blocks a new rebuild only while its worker is still alive."""
    if job.get("status") not in {"queued", "running"}:
        return False
    if job.get("commit") != _deployment_version():
        return False
    return _pid_alive(job.get("pid"))


def _remember_job(job: dict) -> None:
    with _docs_job_lock:
        _docs_job_state.clear()
        _docs_job_state.update(job)


def _mutate_job(mutator) -> dict:
    """Update the shared job file under an exclusive lock.

    ``mutator`` returns the replacement document, or None to leave the file
    unchanged. Disk is the source of truth so every Gunicorn worker agrees.
    """
    path = _job_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(f"{path}.lock", "a+", encoding="utf-8") as lock_handle:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            current = _read_json(path) or {"status": "idle"}
            updated = mutator(dict(current))
            if updated is not None:
                _atomic_write_json(path, updated)
                current = updated
            _remember_job(current)
            return dict(current)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _effective_job_state() -> dict:
    """Return the job workers should agree on, ignoring a dead worker's memory."""
    job = _read_json(_job_path()) or {"status": "idle"}
    if job.get("status") in {"queued", "running"} and not _job_is_current(job):
        return {"status": "idle", "stale": True}
    return job


def _embedding_receipt_fields(processor) -> dict:
    """Provider, model, and dimension for the embedder that just built the index."""
    from config import Config

    identity = dict(Config.resolved_embedding())
    embeddings = getattr(processor, "embeddings", None)
    if embeddings is not None:
        provider = getattr(embeddings, "embedding_provider", None)
        model = getattr(embeddings, "embedding_model", None)
        if provider:
            identity["embedding_provider"] = str(provider)
        if model:
            identity["embedding_model"] = str(model)
        measured = getattr(embeddings, "measured_dimension", None)
        if measured:
            identity["embedding_dimension"] = int(measured)
    if not identity.get("embedding_dimension"):
        identity.pop("embedding_dimension", None)
    return identity


def _write_index_receipt(processor, commit: str, job_id: str | None) -> dict:
    """Persist proof that this commit finished building the collection."""
    scan = processor.scan_source_files()
    chunk_count = int(processor.vector_store_count() or 0)
    source_files = int(scan.get("total_files") or 0)
    if source_files < 1 or chunk_count < 1:
        raise RuntimeError("Document index rebuild produced an empty collection")
    receipt = {
        "commit": commit,
        "source_files": source_files,
        "vector_store_chunks": chunk_count,
        "completed_at": time.time(),
    }
    receipt.update(_embedding_receipt_fields(processor))
    collection_id = getattr(processor, "active_collection_id", None)
    if collection_id:
        receipt["collection_id"] = str(collection_id)
    if job_id:
        receipt["job_id"] = job_id
    _atomic_write_json(_index_build_path(), receipt)
    logger.info(
        "Document index rebuilt for %s (%s files, %s chunks)",
        commit,
        source_files,
        chunk_count,
    )
    return receipt


@app.route("/healthz", methods=["GET"])
def healthz():
    """Liveness probe that does not require an LLM key or vector index."""
    return jsonify({"status": "ok", "version": _deployment_version()})


@app.route("/readyz", methods=["GET"])
def readyz():
    """Readiness signal for the advising index receipt.

    This does not import Chroma or call a model. The deploy workflow and any
    platform health probe must keep using /healthz. This route stays not-ready
    until the post-deploy rebuild writes an index_build.json for this commit
    and for the embedding provider, model, and dimension now configured.
    """
    from catalog_recency import catalog_staleness_report

    report = _index_readiness_report()
    staleness = catalog_staleness_report()
    report.update(staleness)
    if staleness["catalog_may_be_stale"]:
        logger.warning(
            "Catalog may be stale: configured %s (catoid %s); expected %s "
            "after the fall cutoff",
            staleness["catalog_year"],
            staleness["catalog_catoid"],
            staleness["expected_catalog_year"],
        )
    return jsonify(report), (200 if report["ready"] else 503)


@app.route("/api/docs-stats", methods=["GET"])
def docs_stats():
    """Debug endpoint to verify which docs are being indexed on the server."""
    auth_error = _admin_error()
    if auth_error:
        return auth_error
    try:
        bot = get_chatbot()
        dp = bot.doc_processor
        scan = dp.scan_source_files()
        return jsonify(
            {
                "docs_path": dp.docs_path,
                "scan": scan,
                "vector_store_chunks": dp.vector_store_count(),
                "index_build": _read_json(_index_build_path()),
            }
        )
    except Exception:
        logger.exception("Unable to read document statistics")
        return jsonify({"error": "Unable to read document statistics"}), 500


def _docs_job_snapshot() -> dict:
    return _effective_job_state()


def _run_docs_index_job(job_id: str) -> None:
    """Build the index outside the request lifecycle to avoid proxy timeouts."""
    started = _mutate_job(
        lambda current: (
            {**current, "status": "running", "started_at": time.time()}
            if current.get("job_id") == job_id
            else None
        )
    )
    if started.get("job_id") != job_id or started.get("status") != "running":
        return
    commit = str(started.get("commit") or _deployment_version())
    try:
        chatbot_instance = get_chatbot()
        chatbot_instance.process_documents()
        if (_read_json(_job_path()) or {}).get("job_id") != job_id:
            return
        receipt = _write_index_receipt(
            chatbot_instance.doc_processor, commit=commit, job_id=job_id
        )
    except Exception:
        logger.exception("Background document-index rebuild failed")
        _mutate_job(
            lambda current: (
                {
                    **current,
                    "status": "failed",
                    "completed_at": time.time(),
                    "error": "Document index rebuild failed",
                }
                if current.get("job_id") == job_id
                else None
            )
        )
        return
    _mutate_job(
        lambda current: (
            {
                **current,
                "status": "succeeded",
                "completed_at": receipt["completed_at"],
                "source_files": receipt["source_files"],
                "vector_store_chunks": receipt["vector_store_chunks"],
            }
            if current.get("job_id") == job_id
            else None
        )
    )


def _begin_docs_job() -> tuple[dict, int]:
    """Queue one rebuild for this release, or report the one already running."""
    decision: dict = {}

    def mutate(current: dict) -> dict | None:
        if _job_is_current(current):
            decision["existing"] = True
            return None
        job = {
            "job_id": secrets.token_urlsafe(12),
            "status": "queued",
            "commit": _deployment_version(),
            "pid": os.getpid(),
            "created_at": time.time(),
        }
        decision["job"] = job
        return job

    current = _mutate_job(mutate)
    if decision.get("existing"):
        return current, 409
    job = decision["job"]
    timer = threading.Timer(
        _DOCS_INDEX_START_DELAY_SECONDS,
        _run_docs_index_job,
        args=(job["job_id"],),
    )
    timer.name = f"docs-index-{job['job_id']}"
    timer.daemon = True
    timer.start()
    return job, 202


@app.route("/api/load-docs/status", methods=["GET"])
def load_docs_status():
    """Return the state of the current or most recent index rebuild."""
    auth_error = _admin_error()
    if auth_error:
        return auth_error
    return jsonify(_docs_job_snapshot())


@app.route("/api/history", methods=["GET"])
def history():
    """Get conversation history for the session, or for one tab id."""
    try:
        try:
            conversation_id = _optional_conversation_id(request.args)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"history": _history_store.snapshot(_history_key(conversation_id))})
    except Exception:
        logger.exception("Unable to read conversation history")
        return jsonify({"error": "Unable to read conversation history"}), 500


@app.route("/api/clear-history", methods=["POST"])
def clear_history():
    """Clear one tab's history, or the session default when no tab id is sent."""
    try:
        payload = request.get_json(silent=True)
        try:
            conversation_id = _optional_conversation_id(
                payload if isinstance(payload, dict) else None
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        _history_store.clear(_history_key(conversation_id))
        return jsonify({"success": True})
    except Exception:
        logger.exception("Unable to clear conversation history")
        return jsonify({"error": "Unable to clear conversation history"}), 500


def _issue_report_csrf_token() -> str:
    """Mint the session token a student must send back when filing a report."""
    token = session.get("issue_report_csrf")
    if not isinstance(token, str) or len(token) < 20:
        token = secrets.token_urlsafe(32)
        session["issue_report_csrf"] = token
    return token


def _issue_report_csrf_error():
    """Require the cookie session and a matching CSRF header on report writes."""
    expected = session.get("issue_report_csrf", "")
    provided = request.headers.get("X-Issue-Report-CSRF", "")
    if (
        not isinstance(expected, str)
        or not expected
        or not provided
        or not hmac.compare_digest(provided, expected)
    ):
        return jsonify({"error": "Invalid report session token"}), 403
    return None


def _matched_turn_model(
    conversation_id: str | None, question: str, answer: str
) -> tuple[str, str]:
    """Return the model this server stored for the flagged turn, if it is still in memory."""
    try:
        history = _history_store.snapshot(
            _history_key(conversation_id), include_model=True
        )
    except Exception:
        logger.exception("Unable to read history while matching an issue report")
        return "", ""
    for entry in reversed(history):
        if str(entry.get("user", "")).strip() != question:
            continue
        if str(entry.get("assistant", "")).strip() != answer:
            continue
        return (
            str(entry.get("provider") or "").strip(),
            str(entry.get("model") or "").strip(),
        )
    return "", ""


@app.route("/api/issue-reports/csrf", methods=["GET"])
def issue_report_csrf():
    """Issue a same-session token for POST /api/issue-reports."""
    allowed, retry_after = _rate_limiter.allow(
        f"issue-report-csrf:{_client_key()}", 30, 60
    )
    if not allowed:
        response = jsonify({"error": "Too many report requests; please try again later"})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429
    return jsonify({"csrf": _issue_report_csrf_token()})


@app.route("/api/issue-reports", methods=["POST"])
def create_issue_report():
    """Store one student report. Transcript fields are kept only when opted in."""
    csrf_error = _issue_report_csrf_error()
    if csrf_error:
        return csrf_error
    allowed, retry_after = _rate_limiter.allow(
        f"issue-report:{_client_key()}",
        _env_int("ISSUE_REPORT_RATE_LIMIT", 10, 1, 100),
        _env_int("ISSUE_REPORT_RATE_WINDOW_SECONDS", 600, 10, 86_400),
    )
    if not allowed:
        response = jsonify({"error": "Too many report requests; please try again later"})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    data = request.get_json(silent=True)
    fallback_provider, fallback_model = _configured_chat_model()
    try:
        record = build_issue_report(
            data,
            history_lookup=_matched_turn_model,
            fallback_provider=fallback_provider,
            fallback_model=fallback_model,
        )
        report_id = get_issue_report_store().save(record)
    except IssueReportValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("Unable to store an answer issue report")
        return jsonify({"error": "Unable to store this report"}), 500

    logger.info(
        "Stored answer issue report id=%s reason=%s include_conversation=%s",
        report_id,
        record["reason"],
        record["include_conversation"],
    )
    return jsonify({"ok": True, "id": report_id})


@app.route("/api/evaluation/issue-reports", methods=["GET"])
def list_issue_reports():
    """List stored reports for a signed-in reviewer. Students cannot call this."""
    auth_error = _evaluation_auth_error()
    if auth_error:
        return auth_error
    reason = request.args.get("reason")
    if reason is not None and reason not in REASON_LABELS:
        return jsonify({"error": "Unknown report reason"}), 400
    try:
        store = get_issue_report_store()
        reports = store.list_reports(reason=reason)
        summary = store.reason_counts()
    except Exception:
        logger.exception("Unable to list answer issue reports")
        return jsonify({"error": "Unable to list issue reports"}), 500
    return jsonify({"reports": reports, "summary": summary})


@app.route("/api/evaluation/popular-questions", methods=["GET"])
def evaluation_popular_questions():
    """Most-asked anonymized questions for a signed-in reviewer."""
    auth_error = _evaluation_auth_error()
    if auth_error:
        return auth_error
    days = _popular_questions_days()
    if days is None:
        return jsonify({"error": "days must be 7, 30, or 90"}), 400
    try:
        return jsonify(_popular_questions_payload(days))
    except Exception:
        logger.exception("Unable to list popular questions")
        return jsonify({"error": "Unable to list popular questions"}), 500


@app.route("/api/popular-questions", methods=["GET"])
def public_popular_questions():
    """Same aggregate as the reviewer list. Off unless the publish flag is set.

    Staff should leave ``POPULAR_QUESTIONS_PUBLIC`` unset until they have
    decided these questions are appropriate to show. There is no separate
    curated subset: turning the flag on publishes these counts.
    """
    if not _popular_questions_public_enabled():
        return jsonify({"error": "Not found"}), 404
    days = _popular_questions_days()
    if days is None:
        return jsonify({"error": "days must be 7, 30, or 90"}), 400
    try:
        return jsonify(_popular_questions_payload(days))
    except Exception:
        logger.exception("Unable to list popular questions")
        return jsonify({"error": "Unable to list popular questions"}), 500


@app.route("/api/load-docs", methods=["POST"])
def load_docs():
    """Queue a document-index rebuild. Synchronous rebuilds are rejected."""
    auth_error = _admin_error()
    if auth_error:
        return auth_error

    # A synchronous rebuild holds the only Gunicorn worker until Chroma
    # finishes. The deploy workflow already calls this route with
    # ``?async=true`` and polls ``/api/load-docs/status``.
    if request.args.get("async", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return (
            jsonify(
                {
                    "error": (
                        "Synchronous document loading is disabled because a "
                        "full rebuild blocks the web worker. Retry with "
                        "?async=true and poll /api/load-docs/status."
                    )
                }
            ),
            400,
        )

    allowed, retry_after = _rate_limiter.allow(
        f"load-docs:{_client_key()}",
        _env_int("DOCS_LOAD_RATE_LIMIT", 2, 1, 20),
        _env_int("DOCS_LOAD_RATE_WINDOW_SECONDS", 600, 10, 86_400),
    )
    if not allowed:
        response = jsonify({"error": "Document loading is temporarily rate limited"})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    # Give Gunicorn time to flush the 202 before the CPU-intensive rebuild
    # starts. On the smallest App Service tier an immediately-started
    # thread can win the GIL and make an accepted job look like a request
    # timeout to the deploy workflow. Job state is written to the shared
    # index directory first, so a different worker does not report idle.
    job, status_code = _begin_docs_job()
    return jsonify(job), status_code


@app.route("/api/questions", methods=["GET"])
def get_questions():
    """Get all hardcoded questions."""
    try:
        chatbot_instance = get_chatbot()
        questions = chatbot_instance.get_knowledge_base_questions()
        return jsonify({"questions": questions})
    except Exception:
        logger.exception("Unable to read hardcoded questions")
        return jsonify({"error": "Unable to read questions"}), 500


@app.route("/api/voice-config", methods=["GET"])
def voice_config():
    """Expose non-secret browser voice settings to the web client."""
    return jsonify(get_voice_config().as_dict())


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    return jsonify({"error": "Server error"}), 500


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(error):
    """Return a JSON error when a client exceeds MAX_CONTENT_LENGTH."""
    return jsonify({"error": "Request body is too large"}), 413


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
