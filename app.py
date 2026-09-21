"""Flask web app for the iSchool Advising Chatbot."""

import hmac
import logging
import os
import re
import secrets
import sys
import threading
import time
from collections import OrderedDict

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
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge
from evaluation_store import (
    EvaluationDataError,
    EvaluationStore,
    EvaluationValidationError,
)
from voice_config import get_voice_config

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


_session_secret = os.getenv("SESSION_SECRET_KEY")
if _looks_like_placeholder(_session_secret):
    # An ephemeral development key prevents accidentally running with a known
    # default, while the warning makes the restart/session trade-off visible.
    _session_secret = secrets.token_hex(32)
    logger.warning("SESSION_SECRET_KEY is not set; using an ephemeral session key")

_session_samesite = os.getenv("SESSION_COOKIE_SAMESITE", "Lax").strip().capitalize()
if _session_samesite not in {"Lax", "Strict", "None"}:
    logger.warning("Invalid SESSION_COOKIE_SAMESITE; using Lax")
    _session_samesite = "Lax"

# Flask's signed session cookie carries only an opaque identifier. The actual
# transcript stays server-side and is bounded by _SessionHistoryStore below.
app.config.update(
    SECRET_KEY=_session_secret,
    MAX_CONTENT_LENGTH=_env_int("MAX_REQUEST_BYTES", 64 * 1024, 1024, 2 * 1024 * 1024),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=_session_samesite,
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
)


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

    def snapshot(self, session_id: str) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            record = self._sessions.get(session_id)
            if record is None:
                return []
            self._sessions.move_to_end(session_id)
            self._sessions[session_id] = (now, record[1])
            return [dict(entry) for entry in record[1]]

    def append(self, session_id: str, result: dict) -> None:
        now = time.monotonic()
        entry = {
            "user": str(result.get("user_message", "")),
            "assistant": str(result.get("response", "")),
            "source": result.get("source"),
        }
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


def _client_key() -> str:
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

# Initialize chatbot (singleton)
chatbot = None
_chatbot_lock = threading.Lock()
_rag_chatbot_class = None

_docs_job_lock = threading.Lock()
_docs_job_state = {"status": "idle"}

_evaluation_store = None
_evaluation_store_key = None
_evaluation_store_lock = threading.Lock()


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
    results_path = os.getenv(
        "EVAL_RESULTS_PATH", os.path.join(_BASE_DIR, "evals", "results", "latest.json")
    )
    database_path = os.getenv(
        "EVAL_DB_PATH", os.path.join(_BASE_DIR, "evals", "reviews.sqlite3")
    )
    store_key = (os.path.abspath(results_path), os.path.abspath(database_path))
    if _evaluation_store is None or _evaluation_store_key != store_key:
        with _evaluation_store_lock:
            if _evaluation_store is None or _evaluation_store_key != store_key:
                _evaluation_store = EvaluationStore(*store_key)
                _evaluation_store_key = store_key
    return _evaluation_store


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
                "entra_identity_available": bool(
                    request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME")
                ),
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

    entra_reviewer = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME", "").strip()
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
    """Persist one 1–7 response score with a short reviewer rationale."""
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
            rating=data.get("rating"),
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
    """Export the current run's complete review audit trail."""
    auth_error = _evaluation_auth_error()
    if auth_error:
        return auth_error
    try:
        store = get_evaluation_store()
        summary = store.summary(_evaluation_reviewer())
        if not summary.get("available"):
            return jsonify({"error": summary["message"]}), 409
        csv_text = store.export_csv()
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


@app.route("/api/chat", methods=["POST"])
def chat():
    """Handle chat requests."""
    session_id = _session_id()
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

        # Get response from chatbot
        chatbot_instance = get_chatbot()
        result = chatbot_instance.chat(user_message)
        _history_store.append(session_id, result)

        return jsonify(
            {
                "response": result["response"],
                "source": result["source"],
                "confidence": result["confidence"],
                "retrieved_docs": len(result["retrieved_docs"]),
            }
        )

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


@app.route("/healthz", methods=["GET"])
def healthz():
    """Liveness probe that does not require an LLM key or vector index."""
    return jsonify({"status": "ok"})


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
            }
        )
    except Exception:
        logger.exception("Unable to read document statistics")
        return jsonify({"error": "Unable to read document statistics"}), 500


def _docs_job_snapshot() -> dict:
    with _docs_job_lock:
        return dict(_docs_job_state)


def _run_docs_index_job(job_id: str) -> None:
    """Build the index outside the request lifecycle to avoid proxy timeouts."""
    with _docs_job_lock:
        _docs_job_state.update(
            {"job_id": job_id, "status": "running", "started_at": time.time()}
        )
    try:
        chatbot_instance = get_chatbot()
        chatbot_instance.process_documents()
        processor = chatbot_instance.doc_processor
        scan = processor.scan_source_files()
        chunk_count = processor.vector_store_count() or 0
        if scan.get("total_files", 0) < 1 or chunk_count < 1:
            raise RuntimeError("Document index rebuild produced an empty collection")
        with _docs_job_lock:
            _docs_job_state.update(
                {
                    "status": "succeeded",
                    "completed_at": time.time(),
                    "source_files": scan["total_files"],
                    "vector_store_chunks": chunk_count,
                }
            )
    except Exception:
        logger.exception("Background document-index rebuild failed")
        with _docs_job_lock:
            _docs_job_state.update(
                {
                    "status": "failed",
                    "completed_at": time.time(),
                    "error": "Document index rebuild failed",
                }
            )


@app.route("/api/load-docs/status", methods=["GET"])
def load_docs_status():
    """Return the state of the current or most recent index rebuild."""
    auth_error = _admin_error()
    if auth_error:
        return auth_error
    return jsonify(_docs_job_snapshot())


@app.route("/api/history", methods=["GET"])
def history():
    """Get conversation history."""
    try:
        return jsonify({"history": _history_store.snapshot(_session_id())})
    except Exception:
        logger.exception("Unable to read conversation history")
        return jsonify({"error": "Unable to read conversation history"}), 500


@app.route("/api/clear-history", methods=["POST"])
def clear_history():
    """Clear conversation history."""
    try:
        _history_store.clear(_session_id())
        return jsonify({"success": True})
    except Exception:
        logger.exception("Unable to clear conversation history")
        return jsonify({"error": "Unable to clear conversation history"}), 500


@app.route("/api/load-docs", methods=["POST"])
def load_docs():
    """Load and process documents."""
    auth_error = _admin_error()
    if auth_error:
        return auth_error

    allowed, retry_after = _rate_limiter.allow(
        f"load-docs:{_client_key()}",
        _env_int("DOCS_LOAD_RATE_LIMIT", 2, 1, 20),
        _env_int("DOCS_LOAD_RATE_WINDOW_SECONDS", 600, 10, 86_400),
    )
    if not allowed:
        response = jsonify({"error": "Document loading is temporarily rate limited"})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    async_requested = request.args.get("async", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if async_requested:
        with _docs_job_lock:
            if _docs_job_state.get("status") in {"queued", "running"}:
                return jsonify(dict(_docs_job_state)), 409
            job_id = secrets.token_urlsafe(12)
            _docs_job_state.clear()
            _docs_job_state.update(
                {"job_id": job_id, "status": "queued", "created_at": time.time()}
            )
        # Give Gunicorn time to flush the 202 before the CPU-intensive rebuild
        # starts. On the smallest App Service tier an immediately-started
        # thread can win the GIL and make an accepted job look like a request
        # timeout to the deploy workflow.
        timer = threading.Timer(1.0, _run_docs_index_job, args=(job_id,))
        timer.name = f"docs-index-{job_id}"
        timer.daemon = True
        timer.start()
        return jsonify(_docs_job_snapshot()), 202

    try:
        chatbot_instance = get_chatbot()
        chatbot_instance.process_documents()

        return jsonify({"success": True, "message": "Documents loaded successfully"})
    except Exception:
        logger.exception("Error loading documents")
        return jsonify({"error": "Unable to load documents"}), 500


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
