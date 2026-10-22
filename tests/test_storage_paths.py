"""Path defaults, reviews-database migration, and index receipt readiness."""

import json
import logging
import os
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import durable_storage
from durable_storage import (
    AZURE_EVAL_DB_PATH,
    AZURE_STRIPPED_LINK_LOG_PATH,
    AZURE_VECTOR_STORE_PATH,
    LEGACY_VECTOR_STORE_PATH,
    LOCAL_VECTOR_STORE_PATH,
    assess_index_readiness,
    copy_reviews_database_if_needed,
    prepare_eval_db_path,
    resolve_eval_db_path,
    resolve_stripped_link_log_path,
    resolve_vector_store_path,
)
from config import resolve_vector_store_path as config_resolve_vector_store_path


def _create_reviews_db(path, comment: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE evaluation_reviews (id INTEGER PRIMARY KEY, comment TEXT)"
        )
        connection.execute(
            "INSERT INTO evaluation_reviews (comment) VALUES (?)", (comment,)
        )
        connection.commit()
    finally:
        connection.close()


def _comments(path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [
            row[0]
            for row in connection.execute("SELECT comment FROM evaluation_reviews")
        ]
    finally:
        connection.close()


class TestPathResolution:
    def test_config_reexports_the_resolver(self):
        assert config_resolve_vector_store_path is resolve_vector_store_path

    def test_explicit_vector_path_wins_on_azure(self):
        assert (
            resolve_vector_store_path(
                "/tmp/chroma",
                environ={"WEBSITE_SITE_NAME": "ischooladvisingchatbot-dx4ynl"},
            )
            == "/tmp/chroma"
        )

    def test_azure_stripped_link_log_uses_the_same_persistent_directory(self):
        assert (
            resolve_stripped_link_log_path(
                environ={"WEBSITE_SITE_NAME": "ischooladvisingchatbot-dx4ynl"}
            )
            == AZURE_STRIPPED_LINK_LOG_PATH
        )
        assert AZURE_STRIPPED_LINK_LOG_PATH == (
            "/home/data/evaluation/stripped_links.sqlite3"
        )

    def test_local_stripped_link_log_sits_beside_the_reviews_database(self):
        assert resolve_stripped_link_log_path(environ={}, app_dir="/app") == (
            os.path.join("/app", "evals", "stripped_links.sqlite3")
        )

    def test_explicit_stripped_link_log_path_wins(self):
        assert (
            resolve_stripped_link_log_path(
                "/tmp/custom-links.sqlite3",
                environ={
                    "WEBSITE_SITE_NAME": "ischooladvisingchatbot-dx4ynl",
                    "STRIPPED_LINK_LOG_PATH": "/other.sqlite3",
                },
            )
            == "/tmp/custom-links.sqlite3"
        )

    def test_azure_defaults_to_persistent_home_even_if_missing(self, monkeypatch):
        monkeypatch.setattr(
            os.path,
            "isdir",
            lambda path: path == LEGACY_VECTOR_STORE_PATH,
        )
        assert (
            resolve_vector_store_path(
                environ={
                    "WEBSITE_INSTANCE_ID": "instance-1",
                    "WEBSITE_SITE_NAME": "ischooladvisingchatbot-dx4ynl",
                }
            )
            == AZURE_VECTOR_STORE_PATH
        )

    def test_local_default_stays_repo_relative(self, monkeypatch):
        monkeypatch.setattr(os.path, "isdir", lambda path: False)
        assert resolve_vector_store_path(environ={}) == LOCAL_VECTOR_STORE_PATH

    def test_legacy_wwwroot_index_is_only_a_non_azure_fallback(self, monkeypatch):
        monkeypatch.setattr(
            os.path,
            "isdir",
            lambda path: path == LEGACY_VECTOR_STORE_PATH,
        )
        assert resolve_vector_store_path(environ={}) == LEGACY_VECTOR_STORE_PATH

    def test_blank_vector_path_is_unset(self, monkeypatch):
        monkeypatch.setattr(os.path, "isdir", lambda path: False)
        assert resolve_vector_store_path("  ", environ={}) == LOCAL_VECTOR_STORE_PATH

    def test_explicit_eval_db_wins_on_azure(self, tmp_path):
        chosen = str(tmp_path / "custom.sqlite3")
        assert (
            resolve_eval_db_path(
                chosen,
                app_dir=str(tmp_path),
                environ={"WEBSITE_SITE_NAME": "ischooladvisingchatbot-dx4ynl"},
            )
            == chosen
        )

    def test_azure_eval_db_defaults_under_home(self, tmp_path):
        assert (
            resolve_eval_db_path(
                None,
                app_dir=str(tmp_path),
                environ={"WEBSITE_SITE_NAME": "ischooladvisingchatbot-dx4ynl"},
            )
            == AZURE_EVAL_DB_PATH
        )

    def test_local_eval_db_stays_beside_the_app(self, tmp_path):
        assert resolve_eval_db_path(None, app_dir=str(tmp_path), environ={}) == str(
            tmp_path / "evals" / "reviews.sqlite3"
        )


class TestReviewsMigration:
    def test_copies_committed_rows_still_in_the_wal(self, tmp_path):
        source = tmp_path / "legacy" / "reviews.sqlite3"
        destination = tmp_path / "durable" / "reviews.sqlite3"
        _create_reviews_db(source, "checkpointed")
        connection = sqlite3.connect(source)
        try:
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute(
                "INSERT INTO evaluation_reviews (comment) VALUES (?)",
                ("committed in WAL",),
            )
            connection.commit()

            # Prove a plain file copy would miss the newly committed row.
            main_file_copy = tmp_path / "main-file-only.sqlite3"
            shutil.copyfile(source, main_file_copy)
            assert _comments(main_file_copy) == ["checkpointed"]

            copied = copy_reviews_database_if_needed(
                str(destination), [str(source)]
            )

            assert copied == str(source.resolve())
            assert _comments(destination) == ["checkpointed", "committed in WAL"]
            assert _comments(source) == ["checkpointed", "committed in WAL"]
            assert not destination.with_suffix(".sqlite3.migrating").exists()
        finally:
            connection.close()

    def test_copies_legacy_database_and_leaves_the_original(self, tmp_path, caplog):
        source = tmp_path / "release" / "evals" / "reviews.sqlite3"
        destination = tmp_path / "home" / "data" / "evaluation" / "reviews.sqlite3"
        _create_reviews_db(source, "keep me")
        caplog.set_level(logging.INFO)

        copied = copy_reviews_database_if_needed(str(destination), [str(source)])

        assert copied == str(source.resolve())
        assert source.exists()
        assert _comments(source) == ["keep me"]
        assert _comments(destination) == ["keep me"]
        assert "left the original file in place" in caplog.text
        assert str(source.resolve()) in caplog.text

    def test_does_not_overwrite_an_existing_database(self, tmp_path):
        source = tmp_path / "old" / "reviews.sqlite3"
        destination = tmp_path / "new" / "reviews.sqlite3"
        _create_reviews_db(source, "legacy")
        _create_reviews_db(destination, "already here")

        assert copy_reviews_database_if_needed(str(destination), [str(source)]) is None
        assert _comments(source) == ["legacy"]
        assert _comments(destination) == ["already here"]

    def test_does_not_copy_a_database_onto_itself(self, tmp_path):
        database = tmp_path / "reviews.sqlite3"
        _create_reviews_db(database, "stay")

        assert copy_reviews_database_if_needed(str(database), [str(database)]) is None
        assert _comments(database) == ["stay"]

    def test_skips_a_non_sqlite_legacy_file(self, tmp_path):
        fake = tmp_path / "reviews.sqlite3"
        fake.write_text("not a database", encoding="utf-8")
        destination = tmp_path / "new" / "reviews.sqlite3"

        assert copy_reviews_database_if_needed(str(destination), [str(fake)]) is None
        assert not destination.exists()
        assert fake.read_text(encoding="utf-8") == "not a database"

    def test_uses_the_next_candidate_when_the_first_is_not_sqlite(self, tmp_path):
        fake = tmp_path / "empty-dir" / "reviews.sqlite3"
        fake.parent.mkdir()
        fake.write_bytes(b"")
        source = tmp_path / "wwwroot" / "reviews.sqlite3"
        destination = tmp_path / "durable" / "reviews.sqlite3"
        _create_reviews_db(source, "from wwwroot")

        copied = copy_reviews_database_if_needed(
            str(destination), [str(fake), str(source)]
        )

        assert copied == str(source.resolve())
        assert _comments(destination) == ["from wwwroot"]
        assert fake.read_bytes() == b""

    def test_failed_copy_does_not_create_the_destination_or_delete_the_source(
        self, tmp_path, monkeypatch
    ):
        source = tmp_path / "old" / "reviews.sqlite3"
        destination = tmp_path / "new" / "reviews.sqlite3"
        _create_reviews_db(source, "still here")

        def explode(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(durable_storage, "_copy_sqlite_database", explode)

        with pytest.raises(OSError, match="disk full"):
            copy_reviews_database_if_needed(str(destination), [str(source)])

        assert source.exists()
        assert _comments(source) == ["still here"]
        assert not destination.exists()

    def test_prepare_on_azure_copies_before_a_new_file_is_created(
        self, tmp_path, monkeypatch, caplog
    ):
        durable = tmp_path / "home" / "data" / "evaluation" / "reviews.sqlite3"
        legacy = tmp_path / "release" / "evals" / "reviews.sqlite3"
        _create_reviews_db(legacy, "migrated")
        monkeypatch.setattr(durable_storage, "AZURE_EVAL_DB_PATH", str(durable))
        caplog.set_level(logging.INFO)

        resolved = prepare_eval_db_path(
            None,
            app_dir=str(tmp_path / "release"),
            environ={"WEBSITE_SITE_NAME": "ischooladvisingchatbot-dx4ynl"},
            candidates=[str(legacy)],
        )

        assert resolved == str(durable)
        assert _comments(legacy) == ["migrated"]
        assert _comments(durable) == ["migrated"]
        assert "Copied reviews database" in caplog.text

    def test_prepare_copies_when_the_explicit_path_is_the_durable_default(
        self, tmp_path, monkeypatch
    ):
        durable = tmp_path / "home" / "data" / "evaluation" / "reviews.sqlite3"
        legacy = tmp_path / "release" / "evals" / "reviews.sqlite3"
        _create_reviews_db(legacy, "explicit durable")
        monkeypatch.setattr(durable_storage, "AZURE_EVAL_DB_PATH", str(durable))

        resolved = prepare_eval_db_path(
            str(durable),
            app_dir=str(tmp_path / "release"),
            environ={},
            candidates=[str(legacy)],
        )

        assert resolved == str(durable)
        assert _comments(durable) == ["explicit durable"]
        assert _comments(legacy) == ["explicit durable"]

    def test_prepare_does_not_replace_an_existing_durable_database(
        self, tmp_path, monkeypatch
    ):
        durable = tmp_path / "home" / "data" / "evaluation" / "reviews.sqlite3"
        legacy = tmp_path / "release" / "evals" / "reviews.sqlite3"
        _create_reviews_db(durable, "current")
        _create_reviews_db(legacy, "older")
        monkeypatch.setattr(durable_storage, "AZURE_EVAL_DB_PATH", str(durable))

        resolved = prepare_eval_db_path(
            str(durable),
            app_dir=str(tmp_path / "release"),
            environ={"WEBSITE_SITE_NAME": "ischooladvisingchatbot-dx4ynl"},
            candidates=[str(legacy)],
        )

        assert resolved == str(durable)
        assert _comments(durable) == ["current"]
        assert _comments(legacy) == ["older"]

    def test_prepare_does_not_migrate_an_explicit_non_durable_path(
        self, tmp_path, monkeypatch
    ):
        durable = tmp_path / "home" / "data" / "evaluation" / "reviews.sqlite3"
        legacy = tmp_path / "release" / "evals" / "reviews.sqlite3"
        custom = tmp_path / "custom.sqlite3"
        _create_reviews_db(legacy, "do not pull me")
        monkeypatch.setattr(durable_storage, "AZURE_EVAL_DB_PATH", str(durable))

        resolved = prepare_eval_db_path(
            str(custom),
            app_dir=str(tmp_path / "release"),
            environ={"WEBSITE_SITE_NAME": "ischooladvisingchatbot-dx4ynl"},
            candidates=[str(legacy)],
        )

        assert resolved == str(custom)
        assert not custom.exists()
        assert not durable.exists()
        assert _comments(legacy) == ["do not pull me"]

    def test_prepare_warns_when_azure_has_nothing_to_copy(
        self, tmp_path, monkeypatch, caplog
    ):
        durable = tmp_path / "home" / "data" / "evaluation" / "reviews.sqlite3"
        monkeypatch.setattr(durable_storage, "AZURE_EVAL_DB_PATH", str(durable))
        durable_storage._warned_missing_reviews.discard(str(durable.resolve()))
        caplog.set_level(logging.WARNING)

        resolved = prepare_eval_db_path(
            None,
            app_dir=str(tmp_path / "release"),
            environ={"WEBSITE_INSTANCE_ID": "instance-1"},
            candidates=[str(tmp_path / "missing.sqlite3")],
        )

        assert resolved == str(durable)
        assert not durable.exists()
        assert "no legacy database was found" in caplog.text

    def test_local_prepare_does_not_create_or_copy(self, tmp_path):
        legacy = tmp_path / "elsewhere" / "reviews.sqlite3"
        _create_reviews_db(legacy, "local only")

        resolved = prepare_eval_db_path(
            None,
            app_dir=str(tmp_path / "app"),
            environ={},
            candidates=[str(legacy)],
        )

        assert resolved == str(tmp_path / "app" / "evals" / "reviews.sqlite3")
        assert not os.path.exists(resolved)
        assert _comments(legacy) == ["local only"]


class TestIndexReadiness:
    def test_missing_receipt_is_not_ready(self, tmp_path):
        report = assess_index_readiness(
            str(tmp_path),
            version_file=str(tmp_path / "deployment_version.txt"),
        )

        assert report["ready"] is False
        assert report["status"] == "missing"
        assert report["expected_commit"] == "development"
        assert report["receipt_commit"] is None
        assert report["receipt_path"].endswith("index_build.json")

    def test_matching_receipt_is_ready(self, tmp_path):
        version = tmp_path / "deployment_version.txt"
        version.write_text("abc123\n", encoding="utf-8")
        (tmp_path / "index_build.json").write_text(
            json.dumps({"commit": "abc123", "source_files": 20}),
            encoding="utf-8",
        )

        report = assess_index_readiness(str(tmp_path), version_file=str(version))

        assert report["ready"] is True
        assert report["status"] == "ready"
        assert report["expected_commit"] == "abc123"
        assert report["receipt_commit"] == "abc123"

    def test_receipt_for_another_commit_is_not_ready(self, tmp_path):
        (tmp_path / "deployment_version.txt").write_text("new-sha", encoding="utf-8")
        (tmp_path / "index_build.json").write_text(
            json.dumps({"commit": "old-sha", "vector_store_chunks": 189}),
            encoding="utf-8",
        )

        report = assess_index_readiness(
            str(tmp_path),
            version_file=str(tmp_path / "deployment_version.txt"),
        )

        assert report["ready"] is False
        assert report["status"] == "mismatch"
        assert report["expected_commit"] == "new-sha"
        assert report["receipt_commit"] == "old-sha"
        assert "old-sha" in report["detail"]
        assert "new-sha" in report["detail"]

    def test_unreadable_receipt_is_not_ready(self, tmp_path):
        (tmp_path / "index_build.json").write_text("{", encoding="utf-8")

        report = assess_index_readiness(str(tmp_path), expected_commit="deploy-sha")

        assert report["ready"] is False
        assert report["status"] == "unreadable"
        assert report["expected_commit"] == "deploy-sha"

    def test_non_object_receipt_is_not_ready(self, tmp_path):
        (tmp_path / "index_build.json").write_text("[]", encoding="utf-8")

        report = assess_index_readiness(str(tmp_path), expected_commit="deploy-sha")

        assert report["status"] == "unreadable"
        assert report["ready"] is False

    def test_receipt_without_a_commit_is_a_mismatch(self, tmp_path):
        (tmp_path / "index_build.json").write_text(
            json.dumps({"source_files": 1}), encoding="utf-8"
        )

        report = assess_index_readiness(str(tmp_path), expected_commit="deploy-sha")

        assert report["status"] == "mismatch"
        assert report["receipt_commit"] is None
        assert report["ready"] is False


def test_readyz_does_not_initialize_the_chatbot(monkeypatch, tmp_path):
    import app as app_module
    from config import Config

    store = tmp_path / "chroma"
    store.mkdir()
    (tmp_path / "deployment_version.txt").write_text("deploy-sha\n", encoding="utf-8")
    (store / "index_build.json").write_text(
        json.dumps({"commit": "deploy-sha"}), encoding="utf-8"
    )
    monkeypatch.setattr(app_module, "chatbot", None)
    monkeypatch.setattr(app_module, "_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(store))

    response = app_module.app.test_client().get("/readyz")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ready"] is True
    assert body["receipt_commit"] == "deploy-sha"
    assert app_module.chatbot is None

    (store / "index_build.json").write_text(
        json.dumps({"commit": "old-sha"}), encoding="utf-8"
    )
    stale = app_module.app.test_client().get("/readyz")
    assert stale.status_code == 503
    assert stale.get_json()["status"] == "mismatch"
    assert app_module.chatbot is None

    health = app_module.app.test_client().get("/healthz")
    assert health.status_code == 200
    assert health.get_json()["status"] == "ok"
