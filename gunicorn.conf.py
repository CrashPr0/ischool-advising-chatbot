"""Gunicorn settings shared by the Procfile and the container image.

Basic (B1) App Service is one small core with 1.75 GB of RAM. Chat calls block
on Azure OpenAI, and the first question also opens the embedded Chroma index.
A single sync worker turns either of those into a site-wide stall, then the
default 30-second timeout kills the worker and Azure returns 502.

gthread keeps the accept loop free to heartbeat and to serve /healthz while a
chat thread waits on the model. Two processes means a cold index load that
holds the GIL in one worker does not freeze the other. Four threads cover a
small advising burst without stampeding the student quota.

The chat client does not retry inside the SDK by default (max_retries 0), so
a 429 is not spent waiting on Retry-After. An optional fallback deployment is
one extra attempt, plus one short retry when that deployment rejects
temperature. The worker timeout is 180 seconds so that budget still finishes
inside the worker and under App Service's roughly 230-second front-end cutoff.
The graceful timeout is longer than the worker timeout so a recycle can finish
an answer that already started.

Operators can override the numbers without a code change. Bad values fall
back to these defaults, and the ranges stay inside what B1 and the front end
can survive.
"""

import logging
import os
import sys
import threading


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    return max(low, min(value, high))


bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
worker_class = "gthread"
workers = _int_env("GUNICORN_WORKERS", 2, 1, 4)
threads = _int_env("GUNICORN_THREADS", 4, 1, 8)
timeout = _int_env("GUNICORN_TIMEOUT", 180, 30, 230)
graceful_timeout = _int_env("GUNICORN_GRACEFUL_TIMEOUT", 210, timeout, 300)


def _should_warm_start() -> bool:
    """Skip the warm start when a unit test has imported pytest.

    Gunicorn workers do not import pytest. Loading this config from a test
    must not construct the chatbot or open Chroma.
    """
    return "pytest" not in sys.modules


def _warm_chatbot() -> None:
    """Construct the chatbot singleton. Failures stay off the accept loop."""
    try:
        from app import get_chatbot

        get_chatbot()
    except Exception:
        logging.getLogger("gunicorn.error").exception("Chatbot warm start failed")


def _start_chatbot_warm_start() -> None:
    threading.Thread(
        target=_warm_chatbot,
        name="chatbot-warm-start",
        daemon=True,
    ).start()


def post_worker_init(worker):
    """Warm the chatbot after this worker can serve /healthz.

    Building it here, in a daemon thread, keeps the startup probe on
    /healthz from waiting for Chroma. See the comment on ``app.get_chatbot``.
    The hook returns immediately. It is a no-op under pytest.
    """
    if not _should_warm_start():
        return
    _start_chatbot_warm_start()
