"""Official 2026-2027 SJSU catalog pages for the BS ISDA major."""

import json
import re
from hashlib import sha256
from pathlib import Path

import yaml

from src.config import Config
from src.document_processor import CORE_RETRIEVAL_HEADER, DocumentProcessor
from src.rag_chatbot import DEFAULT_RAG_TOP_K

DOCS = Path(__file__).resolve().parents[1] / "data" / "advising_docs"
BS_URL = "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19100"
ONLINE_URL = "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19052"
CATALOG_YEAR = "2026-2027"
CORE_HEADING = "Core Courses (36 units)"
CORE_COURSES = (
    "ISDA 100 - Technical Writing for Information (3)",
    "ISDA 101 - Online Learning: Tools and Strategies for Success (1)",
    "ISDA 110 - Technology, Culture, and Society (3)",
    "ISDA 111 - Information and Data Science (3)",
    "ISDA 120 - Information Security (3)",
    "ISDA 121 - Human Centered Cybersecurity (3)",
    "ISDA 122 - Privacy, Security, Ethics (2)",
    "ISDA 123 - Information Assurance (2)",
    "ISDA 130 - User Centered Interface Architecture (3)",
    "ISDA 131 - Information Architecture (3)",
    "ISDA 140 - Big Data Analytics and Management (3)",
    "ISDA 141 - Information Visualization (3)",
    "ISDA 170 - The Emerging Future: Technology Issues and Trends (4)",
)
CORE_UNITS = {
    "100": 3,
    "101": 1,
    "110": 3,
    "111": 3,
    "120": 3,
    "121": 3,
    "122": 2,
    "123": 2,
    "130": 3,
    "131": 3,
    "140": 3,
    "141": 3,
    "170": 4,
}


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
    manifest = json.loads(
        (DOCS / "scrape" / "manifest.json").read_text(encoding="utf-8")
    )
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
        assert text.count(CORE_HEADING) == 1
        assert "13 courses:" not in text
        assert "Core course titles and units:" not in text
        assert (
            "Major Requirements: 48" in text or "Major Requirements (48 units)" in text
        )
        assert "Total: 120" in text or "Total Units Required (120 units)" in text
        for course in CORE_COURSES:
            assert course in text
        assert meta["atomic_section"] == CORE_HEADING
        assert meta["title"] in text

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
    for url in (BS_URL, ONLINE_URL):
        complete = [
            hit
            for hit in hits
            if hit.metadata.get("source_url") == url
            and CORE_HEADING in hit.page_content
            and CATALOG_YEAR in hit.page_content
            and f"Source URL: {url}" in hit.page_content
            and all(course in hit.page_content for course in CORE_COURSES)
        ]
        assert len(complete) == 1
    for hit in hits:
        present = [course for course in CORE_COURSES if course in hit.page_content]
        assert present == [] or len(present) == len(CORE_COURSES)

    units = processor.retrieve_relevant_docs("How many units is the ISDA major?", k=5)
    units_blob = "\n".join(hit.page_content for hit in units)
    assert BS_URL in {hit.metadata.get("source_url") for hit in units}
    assert "Major Requirements (48 units)" in units_blob
    assert "Core Courses (36 units)" in units_blob

    electives = processor.retrieve_relevant_docs(
        "What electives can I take for ISDA?", k=5
    )
    electives_blob = "\n".join(hit.page_content for hit in electives)
    assert "ISDA 80, 81, 150, 160, 180, and 181" in electives_blob
    assert any(
        hit.metadata.get("source_url") in {BS_URL, ONLINE_URL} for hit in electives
    )


def test_each_snapshot_has_one_complete_core_chunk(monkeypatch):
    """The real splitter must not leave a partial core list in any chunk."""
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")

    processor = DocumentProcessor(str(DOCS))
    loaded = {doc.metadata.get("source_url"): doc for doc in processor.load_documents()}
    for url in (BS_URL, ONLINE_URL):
        _path, _text, meta = _snapshot(url)
        chunks = processor.split_documents([loaded[url]])
        core_chunks = [chunk for chunk in chunks if CORE_HEADING in chunk.page_content]
        assert len(core_chunks) == 1
        core = core_chunks[0]
        assert meta["title"] in core.page_content
        assert CATALOG_YEAR in core.page_content
        assert f"Source URL: {url}" in core.page_content
        assert core.page_content.startswith(CORE_RETRIEVAL_HEADER + "\n")
        assert core.page_content.count(CORE_RETRIEVAL_HEADER) == 1
        for course in CORE_COURSES:
            assert course in core.page_content
        paired = re.findall(r"^ISDA (\d{3}) - .+\((\d+)\)$", core.page_content, re.M)
        assert len(paired) == 13
        assert {number: int(units) for number, units in paired} == CORE_UNITS
        assert sum(int(units) for _number, units in paired) == 36
        assert "ISDA 80, 81, 150, 160, 180, and 181" not in core.page_content
        assert any(
            "ISDA 80, 81, 150, 160, 180, and 181" in chunk.page_content
            for chunk in chunks
        )
        for chunk in chunks:
            if chunk is core:
                continue
            assert CORE_HEADING not in chunk.page_content
            assert CORE_RETRIEVAL_HEADER not in chunk.page_content
            for course in CORE_COURSES:
                assert course not in chunk.page_content
            assert len(chunk.page_content) <= 600


def _complete_core_hits(hits, url):
    return [
        hit
        for hit in hits
        if hit.metadata.get("source_url") == url
        and CORE_HEADING in hit.page_content
        and all(course in hit.page_content for course in CORE_COURSES)
    ]


def test_core_and_required_questions_retrieve_the_complete_core_chunk(
    monkeypatch, tmp_path
):
    """Hashing and hybrid both surface one complete core chunk within chat k."""
    assert DEFAULT_RAG_TOP_K == 8
    assert CORE_RETRIEVAL_HEADER == (
        "BS ISDA required core courses (major requirements, 36 units) — "
        "what classes are required for the Information Science and Data Analytics major"
    )
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path / "chroma"))
    processor = DocumentProcessor(str(DOCS))
    assert processor.process_and_index() is not None

    questions = (
        "What are the ISDA core courses?",
        "What classes are required for the ISDA major?",
    )
    for hybrid in ("false", "true"):
        monkeypatch.setenv("HYBRID_RETRIEVAL", hybrid)
        for question in questions:
            hits = processor.retrieve_relevant_docs(question, k=DEFAULT_RAG_TOP_K)
            for url in (BS_URL, ONLINE_URL):
                assert len(_complete_core_hits(hits, url)) == 1
            for hit in hits:
                present = [
                    course for course in CORE_COURSES if course in hit.page_content
                ]
                assert present == [] or len(present) == len(CORE_COURSES)
