"""Deploy-time index checks must not treat a stale collection as success."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_deployed_index as verify  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedTransport:
    def __init__(self, posts, stats, jobs):
        self._posts = list(posts)
        self._stats = list(stats)
        self._jobs = list(jobs)
        self.posts = 0
        self.stats_reads = 0
        self.job_reads = 0

    def post_rebuild(self):
        self.posts += 1
        if not self._posts:
            raise AssertionError("unexpected rebuild POST")
        result = self._posts.pop(0)
        if not self._posts:
            self._posts.append(result)
        return result

    def get_stats(self):
        self.stats_reads += 1
        return self._next(self._stats)

    def get_job(self):
        self.job_reads += 1
        return self._next(self._jobs)

    @staticmethod
    def _next(items):
        if not items:
            raise AssertionError("scripted response missing")
        result = items.pop(0)
        if not items:
            items.append(result)
        return result


def _result(status, payload=None, *, transport_error=None, retry_after=None, body=None):
    if body is None:
        body = "" if payload is None else json.dumps(payload)
    return verify.HttpResult(
        status=status,
        body=body,
        transport_error=transport_error,
        retry_after=retry_after,
    )


def _stats(commit):
    build = None
    if commit is not None:
        build = {
            "commit": commit,
            "source_files": 20,
            "vector_store_chunks": 189,
        }
    return _result(
        200,
        {
            "scan": {"total_files": 20},
            "vector_store_chunks": 189,
            "index_build": build,
        },
    )


def _job(status, commit="abc123", error=None):
    payload = {"status": status, "commit": commit}
    if error:
        payload["error"] = error
    return _result(200, payload)


def _run(transport, commit="abc123", **kwargs):
    clock = kwargs.pop("clock", None) or Clock()
    logs = []
    verify.run_verification(
        transport,
        commit,
        timeout_seconds=kwargs.pop("timeout_seconds", 45),
        poll_seconds=kwargs.pop("poll_seconds", 15),
        post_limit=kwargs.pop("post_limit", 3),
        verify_only=kwargs.pop("verify_only", False),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        log=logs.append,
    )
    return clock, logs


def test_response_classification_retries_gateway_errors_and_stops_on_auth():
    assert verify.response_kind(_result(502, body="bad gateway")) == "transient"
    assert (
        verify.response_kind(_result(503, body="<html>unavailable</html>"))
        == "transient"
    )
    assert (
        verify.response_kind(
            _result(503, {"error": "DOCS_LOAD_TOKEN is not configured"})
        )
        == "auth"
    )
    assert verify.response_kind(_result(401, {"error": "nope"})) == "auth"
    assert verify.response_kind(_result(429, {}, retry_after=30)) == "rate_limited"
    assert verify.response_kind(_result(409, {"status": "running"})) == "in_progress"
    assert (
        verify.response_kind(_result(None, transport_error="timed out")) == "transient"
    )
    assert verify.response_kind(_result(400, body="no")) == "hard"


def test_nonempty_stale_index_is_not_ready():
    stale = {
        "scan": {"total_files": 20},
        "vector_store_chunks": 189,
        "index_build": {
            "commit": "old-sha",
            "source_files": 20,
            "vector_store_chunks": 189,
        },
    }
    missing = {
        "scan": {"total_files": 20},
        "vector_store_chunks": 189,
        "index_build": None,
    }

    empty = {
        "scan": {"total_files": 20},
        "vector_store_chunks": 0,
        "index_build": {
            "commit": "new-sha",
            "source_files": 20,
            "vector_store_chunks": 0,
        },
    }

    assert "old-sha" in verify.readiness_failure(stale, "new-sha")
    assert verify.readiness_failure(missing, "new-sha") == "index has no build receipt"
    assert verify.readiness_failure(stale, "old-sha") is None
    assert "empty" in verify.readiness_failure(empty, "new-sha")


def test_stale_collection_does_not_pass_the_deploy_check():
    transport = ScriptedTransport(
        posts=[_result(202, {"status": "queued"})],
        stats=[_stats("old-sha")],
        jobs=[_job("running")],
    )

    with pytest.raises(verify.RebuildFailed, match="did not complete in time"):
        _run(transport, commit="new-sha")


def test_timeout_and_busy_stats_retry_until_the_commit_matches():
    transport = ScriptedTransport(
        posts=[_result(202, {"status": "queued"})],
        stats=[
            _result(None, transport_error="timed out"),
            _result(502, body="bad gateway"),
            _stats("abc123"),
        ],
        jobs=[_job("running")],
    )

    clock, logs = _run(transport)

    assert transport.posts == 1
    assert any("189 chunks" in line and "abc123" in line for line in logs)
    assert clock.now < 45


def test_failed_rebuild_stops_without_waiting_out_the_deadline():
    transport = ScriptedTransport(
        posts=[_result(202, {"status": "queued"})],
        stats=[_stats("old-sha")],
        jobs=[_job("failed", error="Document index rebuild failed")],
    )
    clock = Clock()

    with pytest.raises(verify.RebuildFailed, match="Document index rebuild failed"):
        _run(transport, clock=clock)

    assert clock.sleeps == []


def test_auth_failure_is_not_retried():
    transport = ScriptedTransport(
        posts=[_result(401, {"error": "Admin authorization required"})],
        stats=[_stats("abc123")],
        jobs=[_job("idle")],
    )

    with pytest.raises(verify.RebuildFailed, match="Admin authorization failed"):
        _run(transport)

    assert transport.stats_reads == 0
    assert transport.posts == 1


def test_idle_after_a_dropped_request_posts_again_then_succeeds():
    transport = ScriptedTransport(
        posts=[
            _result(None, transport_error="timed out"),
            _result(202, {"status": "queued"}),
        ],
        stats=[_stats(None), _stats(None), _stats("abc123")],
        jobs=[_job("idle", commit=""), _job("idle", commit=""), _job("running")],
    )

    _clock, logs = _run(transport)

    assert transport.posts == 2
    assert any("abc123" in line for line in logs)


def test_rate_limit_waits_and_does_not_count_as_success():
    transport = ScriptedTransport(
        posts=[
            _result(429, {"error": "rate limited"}, retry_after=40),
            _result(202, {"status": "queued"}),
        ],
        stats=[_stats("old-sha"), _stats("old-sha"), _stats("abc123")],
        jobs=[_job("idle", commit="")],
    )
    clock = Clock()

    _run(transport, clock=clock, timeout_seconds=200)

    assert clock.sleeps[0] == 40
    assert transport.posts == 2


def test_verify_only_rejects_a_stale_receipt_without_posting():
    transport = ScriptedTransport(
        posts=[],
        stats=[_stats("old-sha")],
        jobs=[],
    )

    with pytest.raises(verify.RebuildFailed, match="old-sha"):
        _run(transport, commit="new-sha", verify_only=True)

    assert transport.posts == 0


def test_verify_only_retries_a_timeout_then_reads_the_receipt():
    transport = ScriptedTransport(
        posts=[],
        stats=[_result(None, transport_error="timed out"), _stats("abc123")],
        jobs=[],
    )

    _clock, logs = _run(transport, verify_only=True)

    assert transport.posts == 0
    assert any("built for abc123" in line for line in logs)


def test_rebuild_that_never_starts_fails_instead_of_passing():
    transport = ScriptedTransport(
        posts=[_result(202, {"status": "queued"})],
        stats=[_stats(None)],
        jobs=[_job("idle", commit="")],
    )

    with pytest.raises(verify.RebuildFailed, match="did not start"):
        _run(transport, post_limit=2, timeout_seconds=90)


def test_main_requires_the_admin_token(monkeypatch):
    monkeypatch.delenv("DOCS_LOAD_TOKEN", raising=False)

    assert verify.main(["--app-url", "https://example.test", "--commit", "abc"]) == 1


def test_workflow_serializes_deploys_and_checks_the_commit():
    workflow = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath(".github", "workflows", "deploy-azure.yml")
        .read_text(encoding="utf-8")
    )

    assert "actions/checkout@v7" in workflow
    assert "actions/setup-python@v7" in workflow
    assert "group: azure-app-service-production" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "docs/**" in workflow
    assert "deliverables/**" in workflow
    assert "**/*.md" in workflow
    assert "python scripts/verify_deployed_index.py" in workflow
    assert "python scripts/verify_deployed_index.py --verify-only" in workflow
    assert "chunk_count" not in workflow
