"""Date-based catalog staleness, with no catalog network calls."""

import json
import logging
from datetime import date

import app as app_module
from src.catalog_recency import (
    catalog_prompt_note,
    catalog_staleness_report,
    expected_catalog_year,
    load_catalog_identity,
    year_for_catoid,
    apply_catalog_metadata,
)
from src.rag_chatbot import RAGAdvisingChatbot


def test_expected_catalog_year_flips_on_the_fall_cutoff():
    assert expected_catalog_year(date(2027, 2, 1)) == "2026-2027"
    assert expected_catalog_year(date(2027, 8, 19)) == "2026-2027"
    assert expected_catalog_year(date(2027, 8, 20)) == "2027-2028"
    assert expected_catalog_year(date(2026, 9, 27)) == "2026-2027"


def test_cutoff_is_configurable_and_invalid_values_use_august_20(monkeypatch):
    monkeypatch.setenv("CATALOG_STALE_CUTOFF", "09-01")
    assert expected_catalog_year(date(2027, 8, 31)) == "2026-2027"
    assert expected_catalog_year(date(2027, 9, 1)) == "2027-2028"

    for raw in ("", "August", "13-01", "02-31", "8-20"):
        monkeypatch.setenv("CATALOG_STALE_CUTOFF", raw)
        assert expected_catalog_year(date(2027, 8, 20)) == "2027-2028"
        assert expected_catalog_year(date(2027, 8, 19)) == "2026-2027"


def test_catalog_is_stale_only_when_the_configured_year_is_older(monkeypatch):
    monkeypatch.setenv("CURRENT_CATALOG_CATOID", "23")
    monkeypatch.setenv("CURRENT_CATALOG_YEAR", "2026-2027")
    current = catalog_staleness_report(date(2026, 9, 27))
    assert current["catalog_may_be_stale"] is False
    assert current["catalog_year"] == "2026-2027"
    assert current["catalog_catoid"] == "23"
    assert current["expected_catalog_year"] == "2026-2027"

    stale = catalog_staleness_report(date(2027, 9, 1))
    assert stale["catalog_may_be_stale"] is True
    assert stale["expected_catalog_year"] == "2027-2028"

    monkeypatch.setenv("CURRENT_CATALOG_YEAR", "2027-2028")
    monkeypatch.setenv("CURRENT_CATALOG_CATOID", "24")
    ahead = catalog_staleness_report(date(2027, 9, 1))
    assert ahead["catalog_may_be_stale"] is False

    monkeypatch.setenv("CURRENT_CATALOG_YEAR", "not-a-year")
    assert catalog_staleness_report(date(2027, 9, 1))["catalog_may_be_stale"] is False


def test_prompt_note_names_the_year_and_leaves_rules_3_4_and_8(monkeypatch):
    monkeypatch.setenv("CURRENT_CATALOG_YEAR", "2026-2027")
    monkeypatch.setenv("CURRENT_CATALOG_CATOID", "23")
    note = catalog_prompt_note(date(2026, 9, 27))
    assert "2026-2027" in note
    assert "newer catalog may have been published" not in note

    stale = catalog_prompt_note(date(2027, 9, 1))
    assert "2026-2027" in stale
    assert "2027-2028" in stale
    assert "newer catalog may have been published" in stale

    prompt = RAGAdvisingChatbot.SYSTEM_PROMPT
    rules_3_and_4 = prompt.split("5. Keep answers", 1)[0]
    assert "3. Answer from whatever the documents do contain" in rules_3_and_4
    assert "4. Never invent course numbers" in rules_3_and_4
    assert (
        "When sources from different catalog years disagree, use the most recent "
        in prompt
    )
    assert "the current official catalog is authoritative over iSchool" in prompt


def test_unknown_catoid_falls_back_to_printed_year_or_stays_blank():
    assert year_for_catoid("10") == ""
    printed = apply_catalog_metadata(
        {
            ("source_url"): (
                "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=40&coid=1"
            )
        },
        "2030-2031 Academic Catalog\nISDA 80 - Example\n",
    )
    assert printed["catoid"] == "40"
    assert printed["catalog_year"] == "2030-2031"
    blank = apply_catalog_metadata(
        {
            ("source_url"): (
                "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=41&coid=1"
            )
        },
        "ISDA 80 - Example\n",
    )
    assert blank["catoid"] == "41"
    assert "catalog_year" not in blank
    assert blank["is_current"] is False


def test_missing_identity_file_uses_the_builtin_map(tmp_path):
    loaded = load_catalog_identity(tmp_path / "missing.json")
    assert loaded["current_catoid"] == "23"
    assert loaded["current_year"] == "2026-2027"
    assert loaded["years_by_catoid"]["17"] == "2025-2026"
    assert loaded["years_by_catoid"]["10"] == "MyRoadmaps"


def test_unreadable_identity_file_uses_the_builtin_map(tmp_path):
    path = tmp_path / "catalog_identity.json"
    path.write_text("{", encoding="utf-8")
    loaded = load_catalog_identity(path)
    assert loaded["current_year"] == "2026-2027"


def _ready_client(monkeypatch, tmp_path, commit="deploy-sha"):
    from config import Config

    store = tmp_path / "chroma"
    store.mkdir()
    (tmp_path / "deployment_version.txt").write_text(f"{commit}\n", encoding="utf-8")
    (store / "index_build.json").write_text(
        json.dumps({"commit": commit}), encoding="utf-8"
    )
    monkeypatch.setattr(app_module, "chatbot", None)
    monkeypatch.setattr(app_module, "_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(store))
    return app_module.app.test_client()


def test_readyz_reports_catalog_staleness_without_changing_readiness(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setenv("CURRENT_CATALOG_YEAR", "2026-2027")
    monkeypatch.setenv("CURRENT_CATALOG_CATOID", "23")
    client = _ready_client(monkeypatch, tmp_path)

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 27)

    monkeypatch.setattr("catalog_recency.date", FrozenDate)
    with caplog.at_level(logging.WARNING):
        fresh = client.get("/readyz")
    body = fresh.get_json()
    assert fresh.status_code == 200
    assert body["ready"] is True
    assert body["catalog_may_be_stale"] is False
    assert body["catalog_year"] == "2026-2027"
    assert body["catalog_catoid"] == "23"
    assert body["expected_catalog_year"] == "2026-2027"
    assert "Catalog may be stale" not in caplog.text

    class LaterDate(date):
        @classmethod
        def today(cls):
            return cls(2027, 9, 1)

    monkeypatch.setattr("catalog_recency.date", LaterDate)
    with caplog.at_level(logging.WARNING):
        stale = client.get("/readyz")
    stale_body = stale.get_json()
    assert stale.status_code == 200
    assert stale_body["ready"] is True
    assert stale_body["status"] == "ready"
    assert stale_body["catalog_may_be_stale"] is True
    assert stale_body["expected_catalog_year"] == "2027-2028"
    assert "Catalog may be stale" in caplog.text
    assert app_module.chatbot is None
