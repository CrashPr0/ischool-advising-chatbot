"""Shared fixtures for API tests that post to /api/chat."""

import json

import pytest


@pytest.fixture(autouse=True)
def pin_committed_catalog_snapshots(monkeypatch):
    """Keep snapshot tests on the committed 2026-2027 catalog.

    The catalog check can later change the default year in
    ``data/catalog_identity.json``. These tests describe the snapshots already
    in ``data/advising_docs``, which use catoid 23. A test that wants the file
    default calls ``monkeypatch.delenv`` for these two names.
    """
    monkeypatch.setenv("CURRENT_CATALOG_CATOID", "23")
    monkeypatch.setenv("CURRENT_CATALOG_YEAR", "2026-2027")


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
