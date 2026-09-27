"""Official 2026-2027 SJSU catalog pages for the BS ISDA major."""

import json
from hashlib import sha256
from pathlib import Path

import yaml

from src.config import Config
from src.document_processor import DocumentProcessor

DOCS = Path(__file__).resolve().parents[1] / "data" / "advising_docs"
BS_URL = "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19100"
ONLINE_URL = "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19052"
CATALOG_YEAR = "2026-2027"
CORE_COURSES = (
    "ISDA 100 - Technical Writing for Information 3 unit(s)",
    "ISDA 101 - Online Learning: Tools and Strategies for Success 1 unit(s)",
    "ISDA 110 - Technology, Culture, and Society 3 unit(s)",
    "ISDA 111 - Information and Data Science 3 unit(s)",
    "ISDA 120 - Information Security 3 unit(s)",
    "ISDA 121 - Human Centered Cybersecurity 3 unit(s)",
    "ISDA 122 - Privacy, Security, Ethics 2 unit(s)",
    "ISDA 123 - Information Assurance 2 unit(s)",
    "ISDA 130 - User Centered Interface Architecture 3 unit(s)",
    "ISDA 131 - Information Architecture 3 unit(s)",
    "ISDA 140 - Big Data Analytics and Management 3 unit(s)",
    "ISDA 141 - Information Visualization 3 unit(s)",
    "ISDA 170 - The Emerging Future: Technology Issues and Trends 4 unit(s)",
)


def _snapshot(url):
    matches = []
    for path in sorted((DOCS / "txt").glob("web-*.txt")):
        meta = yaml.safe_load(
            (DOCS / "meta" / f"{path.stem}.yml").read_text(encoding="utf-8")
        )
        if meta.get("source_url") == url:
            matches.append((path, path.read_text(encoding="utf-8"), meta))
    assert len(matches) == 1
    return matches[0]


def test_bs_isda_catalog_program_pages_record_the_published_requirements():
    manifest = json.loads((DOCS / "scrape" / "manifest.json").read_text(encoding="utf-8"))
    listed = {item["source_url"]: item for item in manifest["documents"]}
    seeds = (DOCS / "scrape" / "seed_urls.txt").read_text(encoding="utf-8")

    for url in (BS_URL, ONLINE_URL):
        path, text, meta = _snapshot(url)
        assert url in seeds
        assert listed[url]["doc_id"] == path.stem
        assert listed[url]["content_sha256"] == sha256(text.encode("utf-8")).hexdigest()
        assert meta["content_sha256"] == sha256(text.encode("utf-8")).hexdigest()
        assert meta["programs"] == ["ISDA"]
        assert meta["catalog_year"] == CATALOG_YEAR
        assert f"Source URL: {url}" in text
        assert f"{CATALOG_YEAR} Academic Catalog" in text
        assert text.splitlines()[-1] == "Retrieved: 2026-09-27"
        assert "Core Courses (36 units)" in text
        assert "Major Requirements: 48" in text or "Major Requirements (48 units)" in text
        assert "Total: 120" in text or "Total Units Required (120 units)" in text
        for course in CORE_COURSES:
            assert course in text

    _on_path, onsite, _on_meta = _snapshot(BS_URL)
    assert "Major Preparation: 5" in onsite
    assert "University Electives: 19" in onsite
    assert "ISDA 20A - Query Languages 1 unit(s)" in onsite
    assert "ISDA 80, 81, 150, 160, 180, and 181" in onsite
    assert "General Education (GE) (34 Lower Division + 9 Upper Division)" in onsite

    _online_path, online, _online_meta = _snapshot(ONLINE_URL)
    assert "Transfer Units: 60" in online
    assert "Upper Division GE and GWAR / WID: 12" in online
    assert "ISDA 20A - Query Languages 1 unit" in online
    assert "Completion of at least 5 units of the following courses" in online


def test_core_courses_query_retrieves_the_catalog_program_page(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("HYBRID_RETRIEVAL", "false")

    processor = DocumentProcessor(str(DOCS))
    loaded = {doc.metadata.get("source_url") for doc in processor.load_documents()}
    assert {BS_URL, ONLINE_URL} <= loaded
    assert processor.process_and_index() is not None

    hits = processor.retrieve_relevant_docs("What are the ISDA core courses?", k=5)
    blob = "\n".join(hit.page_content for hit in hits)
    assert BS_URL in {hit.metadata.get("source_url") for hit in hits}
    assert BS_URL in blob
    assert CATALOG_YEAR in blob
    for course in CORE_COURSES:
        assert course in blob

    units = processor.retrieve_relevant_docs("How many units is the ISDA major?", k=5)
    units_blob = "\n".join(hit.page_content for hit in units)
    assert BS_URL in {hit.metadata.get("source_url") for hit in units}
    assert "Major Requirements (48 units)" in units_blob
    assert "Core Courses (36 units)" in units_blob

    electives = processor.retrieve_relevant_docs("What electives can I take?", k=5)
    electives_blob = "\n".join(hit.page_content for hit in electives)
    assert "ISDA 80, 81, 150, 160, 180, and 181" in electives_blob
    assert any(
        hit.metadata.get("source_url") in {BS_URL, ONLINE_URL} for hit in electives
    )
