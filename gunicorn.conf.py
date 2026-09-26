"""Gunicorn settings shared by the Procfile and the container image.

Basic (B1) App Service is one small core with 1.75 GB of RAM. Chat calls block
on Azure OpenAI, and the first question also opens the embedded Chroma index.
A single sync worker turns either of those into a site-wide stall, then the
default 30-second timeout kills the worker and Azure returns 502.

gthread keeps the accept loop free to heartbeat and to serve /healthz while a
chat thread waits on the model. Two processes means a cold index load that
holds the GIL in one worker does not freeze the other. Four threads cover a
small advising burst without stampeding the student quota.

The chat client budget is 45 seconds times one try and two retries (about 135
seconds). The worker timeout is 180 seconds so a heartbeat stall during that
budget does not kill the worker before Flask can return the apology. It stays
under App Service's roughly 230-second front-end cutoff. The graceful timeout
is longer than the worker timeout so a recycle can finish an answer that
already started.

Operators can override the numbers without a code change. Bad values fall
back to these defaults, and the ranges stay inside what B1 and the front end
can survive.
"""

import os


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
