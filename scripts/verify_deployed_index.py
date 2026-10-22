"""Prove the deployed advising index was rebuilt for this commit.

The production collection lives on persistent storage, so a non-empty chunk
count can belong to an older release. Success requires the build receipt
written at the end of this commit's rebuild. Gateway timeouts, 502s, and a
worker that is busy computing embeddings are retried. An authentication
failure, a rebuild that reports failed, or a deadline without a matching
receipt fails the process.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable


class RebuildFailed(RuntimeError):
    """The production index did not rebuild for the deployed commit."""


@dataclass
class HttpResult:
    status: int | None
    body: str
    transport_error: str | None = None
    retry_after: int | None = None

    def json_object(self) -> dict | None:
        try:
            payload = json.loads(self.body)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None


class UrllibTransport:
    """Small HTTP client for the two admin index endpoints."""

    def __init__(self, app_url: str, token: str, timeout: float):
        self.app_url = app_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def post_rebuild(self) -> HttpResult:
        return self._request("POST", "/api/load-docs?async=true")

    def get_stats(self) -> HttpResult:
        return self._request("GET", "/api/docs-stats")

    def get_job(self) -> HttpResult:
        return self._request("GET", "/api/load-docs/status")

    def _request(self, method: str, path: str) -> HttpResult:
        request = urllib.request.Request(
            f"{self.app_url}{path}",
            headers={"X-Admin-Token": self.token},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                return HttpResult(status=response.status, body=body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            retry_after = _parse_retry_after(
                exc.headers.get("Retry-After") if exc.headers else None
            )
            return HttpResult(status=exc.code, body=body, retry_after=retry_after)
        except (TimeoutError, socket.timeout) as exc:
            return HttpResult(status=None, body="", transport_error=f"timeout: {exc}")
        except urllib.error.URLError as exc:
            return HttpResult(status=None, body="", transport_error=str(exc.reason))


def _parse_retry_after(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(0, int(value.strip()))
    except ValueError:
        return None


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def response_kind(result: HttpResult) -> str:
    """Classify a response as success, retry, or a failure to surface now."""
    if result.transport_error:
        return "transient"
    status = result.status
    if status in (200, 202):
        return "ok"
    if status == 409:
        return "in_progress"
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "auth"
    if status == 503 and "DOCS_LOAD_TOKEN" in (result.body or ""):
        return "auth"
    if status in (500, 502, 503, 504) or status is None:
        return "transient"
    return "hard"


def readiness_failure(stats: dict | None, commit: str) -> str | None:
    """Return why this stats payload does not prove a rebuild for ``commit``."""
    if not isinstance(stats, dict):
        return "document stats response was not a JSON object"
    build = stats.get("index_build")
    if not isinstance(build, dict):
        return "index has no build receipt"
    built_commit = str(build.get("commit") or "")
    scan = stats.get("scan") if isinstance(stats.get("scan"), dict) else {}
    sources = _as_int(scan.get("total_files"))
    chunks = _as_int(stats.get("vector_store_chunks"))
    built_sources = _as_int(build.get("source_files"))
    built_chunks = _as_int(build.get("vector_store_chunks"))
    if built_commit != commit:
        described = built_commit or "an unknown commit"
        return f"index was built for {described}, not {commit}"
    if min(sources, chunks, built_sources, built_chunks) < 1:
        return f"index receipt is empty ({sources} source files, {chunks} chunks)"
    return None


def describe_index(stats: dict) -> str:
    scan = stats.get("scan") if isinstance(stats.get("scan"), dict) else {}
    build = (
        stats.get("index_build") if isinstance(stats.get("index_build"), dict) else {}
    )
    provider = str(build.get("embedding_provider") or "").strip()
    embedding = ""
    if provider:
        model = str(build.get("embedding_model") or "unknown")
        dimension = build.get("embedding_dimension")
        embedding = f", embeddings {provider}/{model}"
        if dimension:
            embedding += f" ({dimension}d)"
    return (
        "Azure advising corpus: "
        f"{_as_int(scan.get('total_files'))} source files, "
        f"{_as_int(stats.get('vector_store_chunks'))} chunks, "
        f"built for {build.get('commit')}"
        f"{embedding}"
    )


def rebuild_failure(payload: dict | None, commit: str) -> str | None:
    """Return the rebuild error when this commit's job has failed."""
    if not isinstance(payload, dict) or payload.get("status") != "failed":
        return None
    if str(payload.get("commit") or "") != commit:
        return None
    return str(payload.get("error") or "Document index rebuild failed")


def rebuild_in_progress(payload: dict | None, commit: str) -> bool:
    if not isinstance(payload, dict):
        return False
    return payload.get("status") in {"queued", "running"} and (
        str(payload.get("commit") or "") == commit
    )


def _raise_if_blocking(result: HttpResult, kind: str) -> None:
    if kind == "auth":
        raise RebuildFailed(
            "Admin authorization failed. DOCS_LOAD_TOKEN does not match the app."
        )
    if kind == "hard":
        detail = (result.body or "").strip().replace("\n", " ")[:300]
        raise RebuildFailed(f"Index request failed with HTTP {result.status}: {detail}")


def run_verification(
    transport,
    commit: str,
    *,
    timeout_seconds: float = 1200,
    poll_seconds: float = 15,
    post_limit: int = 6,
    verify_only: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> None:
    """Poll until the index receipt matches ``commit``, or raise."""
    if not commit:
        raise RebuildFailed("The deployed commit SHA is required")

    def post_rebuild() -> str:
        nonlocal posts
        if posts >= post_limit:
            return "exhausted"
        posts += 1
        result = transport.post_rebuild()
        kind = response_kind(result)
        _raise_if_blocking(result, kind)
        if kind == "rate_limited":
            delay = (
                result.retry_after if result.retry_after is not None else poll_seconds
            )
            delay = max(1, min(delay, 600))
            remaining = deadline - monotonic()
            log(
                "Document rebuild is rate limited; "
                f"waiting {min(delay, max(remaining, 0)):.0f}s before retrying"
            )
            if remaining > 0:
                sleep(min(delay, remaining))
            return "rate_limited"
        if kind == "transient":
            log(
                "Rebuild request hit a transient error "
                f"({result.transport_error or result.status}); "
                "checking whether the job started"
            )
            return "transient"
        log(f"Rebuild request accepted ({result.status})")
        return "accepted"

    posts = 0
    deadline = monotonic() + timeout_seconds

    if verify_only:
        _confirm_existing_receipt(
            transport,
            commit,
            attempts=5,
            poll_seconds=poll_seconds,
            sleep=sleep,
            log=log,
        )
        return

    post_rebuild()
    idle_observations = 0
    while monotonic() < deadline:
        stats_result = transport.get_stats()
        stats_kind = response_kind(stats_result)
        _raise_if_blocking(stats_result, stats_kind)
        if stats_kind == "ok":
            stats = stats_result.json_object()
            reason = readiness_failure(stats, commit)
            if reason is None:
                log(describe_index(stats or {}))
                return
            log(f"Index is not ready: {reason}")
        else:
            log(
                "Index stats are temporarily unavailable "
                f"({stats_result.transport_error or stats_result.status})"
            )

        job_result = transport.get_job()
        job_kind = response_kind(job_result)
        _raise_if_blocking(job_result, job_kind)
        if job_kind == "ok":
            job = job_result.json_object()
            failure = rebuild_failure(job, commit)
            if failure:
                raise RebuildFailed(failure)
            if rebuild_in_progress(job, commit):
                idle_observations = 0
                log(f"Index rebuild is {job.get('status') if job else 'running'}")
            else:
                idle_observations += 1
                status = job.get("status") if job else "missing"
                log(f"No in-progress rebuild for this commit (status {status})")
                if idle_observations >= 2:
                    outcome = post_rebuild()
                    idle_observations = 0
                    if outcome == "exhausted":
                        raise RebuildFailed(
                            "Index rebuild did not start for this commit"
                        )
        elif job_kind == "in_progress":
            idle_observations = 0
            log("Index rebuild is already running")
        else:
            log(
                "Index job status is temporarily unavailable "
                f"({job_result.transport_error or job_result.status})"
            )

        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(poll_seconds, remaining))

    raise RebuildFailed(f"Document index rebuild for {commit} did not complete in time")


def _confirm_existing_receipt(
    transport,
    commit: str,
    *,
    attempts: int,
    poll_seconds: float,
    sleep: Callable[[float], None],
    log: Callable[[str], None],
) -> None:
    """Re-read a receipt the rebuild step already proved, retrying only transport."""
    last_error = "production index stats were unavailable"
    for attempt in range(1, attempts + 1):
        result = transport.get_stats()
        kind = response_kind(result)
        _raise_if_blocking(result, kind)
        if kind == "ok":
            stats = result.json_object()
            reason = readiness_failure(stats, commit)
            if reason is None:
                log(describe_index(stats or {}))
                return
            raise RebuildFailed(reason)
        last_error = result.transport_error or f"HTTP {result.status}"
        log(
            "Retrying index verification after a transient error "
            f"({attempt}/{attempts})"
        )
        if attempt < attempts:
            sleep(poll_seconds)
    raise RebuildFailed(
        f"Could not read the production index after the rebuild: {last_error}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Read the index receipt and do not start another rebuild",
    )
    parser.add_argument("--app-url", default=os.environ.get("APP_URL", ""))
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--timeout-seconds", type=float, default=1200)
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--http-timeout", type=float, default=30)
    args = parser.parse_args(argv)

    token = os.environ.get("DOCS_LOAD_TOKEN", "").strip()
    if not token:
        print("Repository secret DOCS_LOAD_TOKEN is required", file=sys.stderr)
        return 1
    if not args.app_url or not args.commit:
        print("APP_URL and the deployed commit SHA are required", file=sys.stderr)
        return 1

    transport = UrllibTransport(args.app_url, token, args.http_timeout)
    try:
        run_verification(
            transport,
            args.commit,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            verify_only=args.verify_only,
        )
    except RebuildFailed as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
