"""Shared fixtures for API tests that post to /api/chat."""

import json

import pytest


@pytest.fixture
def ready_chat_index(monkeypatch, tmp_path):
    """Point /api/chat at an index receipt that matches this test release.

    Chat refuses when the receipt is missing or names another commit, and
    that refusal must not call the model. Tests that expect a normal answer
    opt in here. The receipt lives in a temp directory, not the repo.
    """
    import app as app_module
    from config import Config

    store = tmp_path / "chroma"
    store.mkdir()
    commit = "test-release"
    (tmp_path / "deployment_version.txt").write_text(f"{commit}\n", encoding="utf-8")
    (store / "index_build.json").write_text(
        json.dumps({"commit": commit}),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(store))
    monkeypatch.setattr(
        app_module, "_rate_limiter", app_module._FixedWindowRateLimiter()
    )
    return {"store": store, "commit": commit}
