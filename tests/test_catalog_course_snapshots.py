"""Checks for the official SJSU catalog ISDA course snapshots."""

import json
from hashlib import sha256
from pathlib import Path

import yaml

DOCS = Path(__file__).resolve().parents[1] / "data" / "advising_docs"
ISDA_150_URL = (
    "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=203201"
)
INFO_210_URL = (
    "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=200282"
)
ISDA_LISTING_URL = (
    "https://catalog.sjsu.edu/content.php?catoid=23&navoid=8469&"
    "filter%5B27%5D=ISDA&filter%5Bcpage%5D=1&cur_cat_oid=23&"
    "search_database=Filter"
)
ISDA_CODES = {
    "ISDA 20A", "ISDA 20B", "ISDA 20C", "ISDA 20D", "ISDA 80", "ISDA 81",
    "ISDA 100", "ISDA 101", "ISDA 110", "ISDA 111", "ISDA 120", "ISDA 121",
    "ISDA 122", "ISDA 123", "ISDA 130", "ISDA 131", "ISDA 140", "ISDA 141",
    "ISDA 150", "ISDA 160", "ISDA 170", "ISDA 180", "ISDA 181", "ISDA 194",
}


def _catalog_snapshots():
    snapshots = []
    for path in sorted((DOCS / "txt").glob("web-*.txt")):
        text = path.read_text(encoding="utf-8")
        if "catalog.sjsu.edu/preview_course_nopop.php?catoid=23" not in text:
            continue
        meta = yaml.safe_load(
            (DOCS / "meta" / f"{path.stem}.yml").read_text(encoding="utf-8")
        )
        snapshots.append((path, text, meta))
    return snapshots


def _snapshot_for_url(url):
    matches = [item for item in _catalog_snapshots() if item[2].get("source_url") == url]
    assert len(matches) == 1
    return matches[0]


def test_isda_150_catalog_snapshot_records_the_published_prerequisite():
    _path, text, meta = _snapshot_for_url(ISDA_150_URL)

    assert "ISDA 150 - Topics in Information Science and Data Analytics" in text
    assert "1-3 unit(s)" in text
    assert "Prerequisite(s): ISDA 110." in text
    assert "Upper Division Standing" not in text
    assert f"Source URL: {ISDA_150_URL}" in text
    assert text.splitlines()[-1] == "Retrieved: 2026-09-26"
    assert meta["source_url"] == ISDA_150_URL
    assert meta["doc_id"] == _path.name.removesuffix(".txt")
    assert meta["programs"] == ["ISDA"]
    assert meta["content_sha256"] == sha256(text.encode("utf-8")).hexdigest()


def test_catalog_course_snapshots_cover_core_isda_courses_and_the_manifest():
    manifest = json.loads((DOCS / "scrape" / "manifest.json").read_text(encoding="utf-8"))
    listed = {item["source_url"] for item in manifest["documents"]}
    seeds = (DOCS / "scrape" / "seed_urls.txt").read_text(encoding="utf-8")
    codes = set()

    for path, text, meta in _catalog_snapshots():
        if meta.get("programs") != ["ISDA"]:
            continue
        url = meta["source_url"]
        assert url in listed
        assert url in seeds
        assert url in text
        assert text.startswith("ISDA ")
        assert "unit(s)" in text
        assert meta["content_sha256"] == sha256(text.encode("utf-8")).hexdigest()
        codes.add(text.split(" - ", 1)[0])

    assert {"ISDA 111", "ISDA 120", "ISDA 140", "ISDA 150"} <= codes


def test_isda_20a_catalog_snapshot_does_not_invent_a_prerequisite():
    url = "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=202921"
    _path, text, _meta = _snapshot_for_url(url)

    assert text.startswith("ISDA 20A - Query Languages")
    assert "1 unit(s)" in text
    assert not any(line.startswith("Prerequisite") for line in text.splitlines())


def test_info_210_catalog_snapshot_records_the_published_prerequisite():
    path, text, meta = _snapshot_for_url(INFO_210_URL)

    assert text.startswith("INFO 210 - Reference and Information Services\n")
    assert "1-3 unit(s)" in text
    assert "Prerequisite(s): INFO 202." in text
    assert "Grading: Letter Graded" in text
    assert text.splitlines()[-1] == "Retrieved: 2026-10-15"
    assert meta["doc_id"] == path.stem
    assert meta["programs"] == ["MLIS"]
    assert meta["content_sha256"] == sha256(text.encode("utf-8")).hexdigest()


def test_isda_listing_snapshot_has_all_24_published_courses_and_no_105():
    manifest = json.loads((DOCS / "scrape" / "manifest.json").read_text(encoding="utf-8"))
    matches = [item for item in manifest["documents"] if item["source_url"] == ISDA_LISTING_URL]
    assert len(matches) == 1
    doc_id = matches[0]["doc_id"]
    text = (DOCS / "txt" / f"{doc_id}.txt").read_text(encoding="utf-8")
    meta = yaml.safe_load((DOCS / "meta" / f"{doc_id}.yml").read_text(encoding="utf-8"))
    listed = {
        line.split(" - ", 1)[0]
        for line in text.splitlines()
        if line.startswith("ISDA ") and " - " in line
    }

    assert listed == ISDA_CODES
    assert "ISDA 105" not in text
    assert "ISDA 194 - Professional Experience: Internship" in text
    assert "The 2026-2027 catalog lists these 24 ISDA courses:" in text
    assert f"Source URL: {ISDA_LISTING_URL}" in text
    assert text.splitlines()[-1] == "Retrieved: 2026-10-15"
    assert meta["programs"] == ["ISDA"]
    assert meta["content_sha256"] == sha256(text.encode("utf-8")).hexdigest()
    assert matches[0]["content_sha256"] == meta["content_sha256"]
