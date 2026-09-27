"""Catalog year metadata and current-catalog preference after hybrid fusion."""

import json
from pathlib import Path

from langchain_core.documents import Document

from src.catalog_recency import (
    CATALOG_YEAR_BY_CATOID,
    apply_catalog_metadata,
    catalog_recency_rerank_enabled,
    catalog_subject_key,
    current_catalog_catoid,
    current_catalog_year,
    document_heading,
    include_authoritative_course_pages,
    reorder_documents,
)
from src.config import Config
from src.document_processor import CORE_RETRIEVAL_HEADER, DocumentProcessor
from src.hybrid_retrieval import hybrid_retrieve
from src.rag_chatbot import DEFAULT_RAG_TOP_K
from tests.test_catalog_program_requirements import (
    BS_URL,
    CATALOG_YEAR,
    CORE_COURSES,
    CORE_HEADING,
    ONLINE_URL,
    _complete_core_hits,
)

DOCS = Path(__file__).resolve().parents[1] / "data" / "advising_docs"
ISDA_150_URL = "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=203201"
OLDER_150_URL = (
    "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=14&coid=131442"
)
OLDER_160_URL = (
    "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=15&coid=139851"
)
ISCHOOL_ELECTIVES = "https://ischool.sjsu.edu/bsisda-electives"
REGISTRAR_URL = (
    "https://www.sjsu.edu/registrar/registration/registration-basics/index.php"
)

CURRENT_150 = (
    "ISDA 150 - Topics in Information Science and Data Analytics\n"
    "1-3 unit(s)\n"
    "Prerequisite(s): ISDA 110.\n"
    f"Source URL: {ISDA_150_URL}\n"
)
OLDER_150 = (
    "ISDA 150 - Special Topics in Information Science and Data Analytics\n"
    "3 unit(s)\n"
    "Major electives include this Special Topics course.\n"
    f"Source URL: {OLDER_150_URL}\n"
)
OLDER_160 = (
    "ISDA 160 - User Experience (UX) Research and Design\n"
    "3 unit(s)\n"
    f"Source URL: {OLDER_160_URL}\n"
)
ISCHOOL = (
    "BSISDA Electives\n"
    "Major electives may be selected from courses in the ISDA program such as "
    "ISDA 150 – Special Topics in Information Science and Data Analytics.\n"
)
REGISTRAR = (
    "Registration appointments are assigned by the Office of the Registrar. "
    "Add a class during your appointment window.\n"
)


def _is_current(metadata):
    value = metadata.get("is_current")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class _Collection:
    def __init__(self, records):
        self._records = records

    def get(self, include=None):
        return {
            "ids": [doc_id for doc_id, _content, _metadata in self._records],
            "documents": [content for _doc_id, content, _metadata in self._records],
            "metadatas": [metadata for _doc_id, _content, metadata in self._records],
        }


class _VectorStore:
    def __init__(self, records, order):
        self._collection = _Collection(records)
        self._by_id = {
            doc_id: (content, metadata) for doc_id, content, metadata in records
        }
        self.order = order

    def similarity_search(self, query, k=3):
        chosen = []
        for doc_id in self.order:
            content, metadata = self._by_id[doc_id]
            chosen.append(Document(page_content=content, metadata=metadata))
            if len(chosen) >= k:
                break
        return chosen


def _catalog_records():
    return [
        (
            "older-150",
            OLDER_150,
            {
                "source": "older-150.txt",
                "title": "ISDA 150 - Special Topics in Information Science and Data Analytics",
                "source_url": OLDER_150_URL,
            },
        ),
        (
            "current-150",
            CURRENT_150,
            {
                "source": "current-150.txt",
                "title": "ISDA 150 - Topics in Information Science and Data Analytics",
                "source_url": ISDA_150_URL,
            },
        ),
        (
            "older-160",
            OLDER_160,
            {
                "source": "older-160.txt",
                "title": "ISDA 160 - User Experience (UX) Research and Design",
                "source_url": OLDER_160_URL,
            },
        ),
        (
            "ischool",
            ISCHOOL,
            {"source": "ischool-electives.txt", "source_url": ISCHOOL_ELECTIVES},
        ),
        (
            "registrar",
            REGISTRAR,
            {"source": "registrar.txt", "source_url": REGISTRAR_URL},
        ),
    ]


def test_catalog_year_comes_from_the_source_url_catoid():
    current = apply_catalog_metadata({"source_url": ISDA_150_URL}, CURRENT_150)
    assert current["catoid"] == "23"
    assert current["catalog_year"] == "2026-2027"
    assert current["is_current"] is True

    older = apply_catalog_metadata(
        {"source_url": OLDER_150_URL, "title": "ISDA 150 - Special Topics"},
        OLDER_150,
    )
    assert older["catoid"] == "14"
    assert older["catalog_year"] == "2023-2024"
    assert older["is_current"] is False

    year_2024 = apply_catalog_metadata({"source_url": OLDER_160_URL}, OLDER_160)
    assert year_2024["catoid"] == "15"
    assert year_2024["catalog_year"] == "2024-2025"
    assert year_2024["is_current"] is False

    unknown = apply_catalog_metadata(
        {
            "source_url": "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=99&coid=1"
        },
        "2018-2019 Academic Catalog\nISDA 80 - Something\n",
    )
    assert unknown["catoid"] == "99"
    assert unknown["catalog_year"] == "2018-2019"
    assert unknown["is_current"] is False


def test_manifest_catalog_urls_resolve_to_the_current_catalog():
    manifest = json.loads(
        (DOCS / "scrape" / "manifest.json").read_text(encoding="utf-8")
    )
    catalog_urls = [
        item["source_url"]
        for item in manifest["documents"]
        if "catalog.sjsu.edu" in item["source_url"]
    ]
    assert catalog_urls
    saw_current = False
    for url in catalog_urls:
        meta = apply_catalog_metadata({"source_url": url, "doc_id": "web"}, "")
        assert meta["catoid"]
        assert meta["catalog_year"]
        if meta["catoid"] == current_catalog_catoid():
            assert meta["catalog_year"] == CATALOG_YEAR_BY_CATOID[meta["catoid"]]
            assert meta["is_current"] is True
            saw_current = True
        else:
            assert meta["is_current"] is False
    assert saw_current


def test_ischool_and_registrar_pages_are_not_catalog_chunks():
    ischool = apply_catalog_metadata(
        {"source_url": ISCHOOL_ELECTIVES, "title": "BSISDA Electives"},
        ISCHOOL
        + " https://catalog.sjsu.edu/preview_course_nopop.php?catoid=14&coid=131442",
    )
    assert "catoid" not in ischool
    assert "catalog_year" not in ischool
    assert "is_current" not in ischool

    registrar = apply_catalog_metadata(
        {"source_url": REGISTRAR_URL},
        "See https://catalog.sjsu.edu/content.php?catoid=14&navoid=5018 for class level.",
    )
    assert "catoid" not in registrar
    assert "catalog_year" not in registrar
    assert "is_current" not in registrar


def test_loaded_catalog_documents_carry_year_and_chunks_keep_it():
    processor = DocumentProcessor(str(DOCS))
    loaded = {doc.metadata.get("source_url"): doc for doc in processor.load_documents()}
    current = loaded[ISDA_150_URL]
    assert current.metadata["catoid"] == "23"
    assert current.metadata["catalog_year"] == "2026-2027"
    assert current.metadata["is_current"] is True
    chunks = processor.split_documents([current])
    assert chunks
    assert all(chunk.metadata["catoid"] == "23" for chunk in chunks)
    assert all(chunk.metadata["catalog_year"] == "2026-2027" for chunk in chunks)
    assert all(chunk.metadata["is_current"] is True for chunk in chunks)

    program = loaded[BS_URL]
    assert program.metadata["catalog_year"] == CATALOG_YEAR
    assert program.metadata["catoid"] == "23"
    assert program.metadata["is_current"] is True

    for url in (ISCHOOL_ELECTIVES, REGISTRAR_URL):
        page = loaded[url]
        assert "catoid" not in page.metadata
        assert "is_current" not in page.metadata
        assert "catalog_year" not in page.metadata


def test_current_catalog_year_follows_the_environment(monkeypatch):
    monkeypatch.delenv("CURRENT_CATALOG_CATOID", raising=False)
    monkeypatch.delenv("CURRENT_CATALOG_YEAR", raising=False)
    identity = json.loads(
        (
            Path(__file__).resolve().parents[1] / "data" / "catalog_identity.json"
        ).read_text(encoding="utf-8")
    )
    assert current_catalog_catoid() == identity["current_catoid"]
    assert current_catalog_year() == identity["current_year"]

    monkeypatch.setenv("CURRENT_CATALOG_CATOID", "14")
    monkeypatch.setenv("CURRENT_CATALOG_YEAR", "2023-2024")
    older = apply_catalog_metadata({"source_url": OLDER_150_URL}, OLDER_150)
    current = apply_catalog_metadata({"source_url": ISDA_150_URL}, CURRENT_150)
    assert older["is_current"] is True
    assert older["catalog_year"] == "2023-2024"
    assert current["is_current"] is False
    assert current["catalog_year"] == "2026-2027"


def test_rerank_flag_defaults_on(monkeypatch):
    monkeypatch.delenv("CATALOG_RECENCY_RERANK", raising=False)
    assert catalog_recency_rerank_enabled() is True
    for value in ("false", "no", "0", "off", ""):
        monkeypatch.setenv("CATALOG_RECENCY_RERANK", value)
        assert catalog_recency_rerank_enabled() is False
    for value in ("true", "TRUE", "yes", "on", "1"):
        monkeypatch.setenv("CATALOG_RECENCY_RERANK", value)
        assert catalog_recency_rerank_enabled() is True


def test_rerank_prefers_current_isda_150_over_older_special_topics():
    older, current, _other, ischool, registrar = [
        Document(page_content=content, metadata=dict(metadata))
        for _doc_id, content, metadata in _catalog_records()
    ]
    # Fusion order: outdated Special Topics title, then non-catalog pages,
    # then the 2026-2027 course page.
    fused = [older, ischool, registrar, current]
    ranked = reorder_documents(fused)

    assert "Topics in Information Science and Data Analytics" in ranked[0].page_content
    assert ranked[0].metadata["catalog_year"] == "2026-2027"
    assert ranked[0].metadata["catoid"] == "23"
    assert ranked[0].metadata["is_current"] is True
    assert ranked[1].metadata["source_url"] == ISCHOOL_ELECTIVES
    assert "catoid" not in ranked[1].metadata
    assert ranked[2].metadata["source_url"] == REGISTRAR_URL
    assert "Special Topics" in ranked[3].page_content
    assert ranked[3].metadata["catalog_year"] == "2023-2024"
    assert ranked[3].metadata["is_current"] is False
    assert catalog_subject_key(current.page_content, ranked[0].metadata) == (
        catalog_subject_key(older.page_content, ranked[3].metadata)
    )


def test_rerank_leaves_a_different_course_and_can_be_disabled(monkeypatch):
    records = _catalog_records()
    by_label = {
        doc_id: Document(page_content=content, metadata=dict(metadata))
        for doc_id, content, metadata in records
    }
    # ISDA 160 is a different course, so the current ISDA 150 page must not
    # take its slot. The iSchool page stays where fusion put it.
    fused = [by_label["older-160"], by_label["ischool"], by_label["current-150"]]
    ranked = reorder_documents(fused)
    assert ranked[0].metadata["source_url"] == OLDER_160_URL
    assert ranked[1].metadata["source_url"] == ISCHOOL_ELECTIVES
    assert ranked[2].metadata["catalog_year"] == "2026-2027"

    monkeypatch.setenv("CATALOG_RECENCY_RERANK", "false")
    disabled = reorder_documents(
        [by_label["older-150"], by_label["ischool"], by_label["current-150"]]
    )
    assert "Special Topics" in disabled[0].page_content
    assert disabled[0].metadata["catalog_year"] == "2023-2024"
    assert disabled[2].metadata["catalog_year"] == "2026-2027"


def test_hybrid_fusion_then_prefers_the_current_isda_150_chunk(monkeypatch):
    monkeypatch.setenv("CATALOG_RECENCY_RERANK", "true")
    records = _catalog_records()
    store = _VectorStore(
        records,
        ["older-150", "ischool", "registrar", "older-160", "current-150"],
    )
    question = "What electives can I take for the ISDA major?"
    # Fusion puts the iSchool page first. It is not a catalog snapshot, so it
    # stays. The 2026-2027 ISDA 150 page then takes the slot of the older
    # Special Topics catalog page. ISDA 160 is a different course and stays.
    hits = hybrid_retrieve(store, question, k=5)
    assert [hit.metadata["source"] for hit in hits] == [
        "ischool-electives.txt",
        "current-150.txt",
        "registrar.txt",
        "older-160.txt",
        "older-150.txt",
    ]
    assert "Topics in Information Science and Data Analytics" in hits[1].page_content
    assert hits[1].metadata["catalog_year"] == "2026-2027"
    assert hits[1].metadata["catoid"] == "23"
    assert hits[1].metadata["is_current"] is True
    assert "catoid" not in hits[0].metadata
    assert "Special Topics" in hits[4].page_content
    assert hits[4].metadata["catalog_year"] == "2023-2024"
    assert hits[4].metadata["is_current"] is False

    monkeypatch.setenv("CATALOG_RECENCY_RERANK", "false")
    before = hybrid_retrieve(store, question, k=5)
    assert [hit.metadata["source"] for hit in before] == [
        "ischool-electives.txt",
        "older-150.txt",
        "registrar.txt",
        "older-160.txt",
        "current-150.txt",
    ]
    assert before[1].metadata["catalog_year"] == "2023-2024"


def test_conflicting_ischool_title_pulls_the_current_course_page():
    program = Document(
        page_content="Major electives include ISDA 80, 81, 150, 160, 180, and 181.",
        metadata={
            "source": "program.txt",
            "source_url": "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19100",
            "title": "Information Science and Data Analytics, BS",
            "catalog_year": "2026-2027",
            "catoid": "23",
            "is_current": True,
        },
    )
    ischool = Document(
        page_content=(
            "ISDA 150 – Special Topics in Information Science and Data Analytics"
        ),
        metadata={
            "source": "ischool.txt",
            "source_url": "https://ischool.sjsu.edu/bsisda-electives",
        },
    )
    filler = Document(
        page_content="Office hours are posted each semester.",
        metadata={
            "source": "hours.txt",
            "source_url": "https://ischool.sjsu.edu/hours",
        },
    )
    current = Document(
        page_content=CURRENT_150,
        metadata={
            "source": "current-150.txt",
            "title": "ISDA 150 - Topics in Information Science and Data Analytics",
            "source_url": ISDA_150_URL,
        },
    )
    records = [
        ("program", program.page_content, program.metadata),
        ("ischool", ischool.page_content, ischool.metadata),
        ("filler", filler.page_content, filler.metadata),
        ("current", current.page_content, current.metadata),
    ]
    pulled = include_authoritative_course_pages(
        [ischool, program, filler], records, k=3
    )
    blob = "\n".join(hit.page_content for hit in pulled)
    assert "ISDA 150 - Topics in Information Science and Data Analytics" in blob
    assert "Special Topics" in blob
    assert "ISDA 80, 81, 150, 160, 180, and 181" in blob
    assert len(pulled) == 3
    assert all(hit.page_content != filler.page_content for hit in pulled)


def test_context_heading_labels_catalog_year_and_non_catalog_source_type():
    assert document_heading(
        1,
        {
            "source": "electives.txt",
            "source_url": "https://ischool.sjsu.edu/bsisda-electives",
        },
    ) == (
        "[Document 1 — electives.txt | "
        "Source type: iSchool web page (not the official catalog)]"
    )
    assert document_heading(
        2,
        {
            "source": "registrar.txt",
            "source_url": (
                "https://www.sjsu.edu/registrar/registration/"
                "registration-basics/index.php"
            ),
        },
    ) == (
        "[Document 2 — registrar.txt | "
        "Source type: Registrar web page (not the official catalog)]"
    )
    assert (
        document_heading(3, {"source": "isda150.txt", "catalog_year": "2026-2027"})
        == "[Document 3 — isda150.txt | Catalog year: 2026-2027]"
    )


def test_model_context_labels_an_ischool_page_and_a_catalog_page():
    from src.rag_chatbot import RAGAdvisingChatbot

    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    context = bot._build_context(
        "What electives can I take for the ISDA major?",
        [
            Document(
                page_content="ISDA 150 – Special Topics in Information Science and Data Analytics",
                metadata={
                    "source": "ischool.txt",
                    "source_url": "https://ischool.sjsu.edu/bsisda-electives",
                },
            ),
            Document(
                page_content="ISDA 150 - Topics in Information Science and Data Analytics",
                metadata={
                    "source": "catalog.txt",
                    "catalog_year": "2026-2027",
                    "catoid": "23",
                },
            ),
        ],
    )
    assert "Source type: iSchool web page (not the official catalog)" in context
    assert "Catalog year: 2026-2027" in context


def test_real_corpus_keeps_core_graduation_and_current_isda_150(monkeypatch, tmp_path):
    """Current-catalog preference must not drop the core list or graduation."""
    assert DEFAULT_RAG_TOP_K == 8
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("HYBRID_RETRIEVAL", "true")
    monkeypatch.setenv("CATALOG_RECENCY_RERANK", "true")

    processor = DocumentProcessor(str(DOCS))
    assert processor.process_and_index() is not None

    questions = (
        "What are the ISDA core courses?",
        "What classes are required for the ISDA major?",
    )
    for question in questions:
        hits = processor.retrieve_relevant_docs(question, k=DEFAULT_RAG_TOP_K)
        for url in (BS_URL, ONLINE_URL):
            complete = _complete_core_hits(hits, url)
            assert len(complete) == 1
            assert complete[0].metadata["catalog_year"] == "2026-2027"
            assert complete[0].metadata["catoid"] == "23"
            assert _is_current(complete[0].metadata)
            assert complete[0].page_content.startswith(CORE_RETRIEVAL_HEADER + "\n")
            assert CORE_HEADING in complete[0].page_content
        for hit in hits:
            present = [course for course in CORE_COURSES if course in hit.page_content]
            assert present == [] or len(present) == len(CORE_COURSES)

    monkeypatch.setenv("CATALOG_RECENCY_RERANK", "false")
    graduation_before = processor.retrieve_relevant_docs(
        "How do I apply to graduate?", k=DEFAULT_RAG_TOP_K
    )
    monkeypatch.setenv("CATALOG_RECENCY_RERANK", "true")
    graduation = processor.retrieve_relevant_docs(
        "How do I apply to graduate?", k=DEFAULT_RAG_TOP_K
    )
    # No older catalog snapshot competes with these hits, so preference must
    # not reshuffle them. The top 8 includes the graduation-requirements
    # passage and the Cal State Apply passage.
    assert [hit.page_content for hit in graduation] == [
        hit.page_content for hit in graduation_before
    ]
    graduation_blob = "\n".join(hit.page_content for hit in graduation)
    assert "graduation requirements" in graduation_blob
    assert "they graduate" in graduation_blob
    assert "Apply through Cal State Apply" in graduation_blob

    electives = processor.retrieve_relevant_docs(
        "What electives can I take for the ISDA major?", k=DEFAULT_RAG_TOP_K
    )
    electives_blob = "\n".join(hit.page_content for hit in electives)
    assert (
        "ISDA 150 - Topics in Information Science and Data Analytics" in electives_blob
    )
    assert "ISDA 80, 81, 150, 160, 180, and 181" in electives_blob
    assert any(
        hit.metadata.get("catalog_year") == "2026-2027"
        and "Topics in Information Science and Data Analytics" in hit.page_content
        for hit in electives
    )

    prerequisites = processor.retrieve_relevant_docs(
        "What are the prerequisites for ISDA 150?", k=DEFAULT_RAG_TOP_K
    )
    current_title = [
        hit
        for hit in prerequisites
        if "ISDA 150 - Topics in Information Science and Data Analytics"
        in hit.page_content
    ]
    assert current_title
    assert current_title[0].metadata["catalog_year"] == "2026-2027"
    assert current_title[0].metadata["catoid"] == "23"
    assert _is_current(current_title[0].metadata)
    assert "Prerequisite(s): ISDA 110." in current_title[0].page_content
