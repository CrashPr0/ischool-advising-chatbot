"""Offline tests for optional BM25 + reciprocal-rank-fusion retrieval."""

import threading

from langchain_core.documents import Document

from src.config import Config
from src.document_processor import DocumentProcessor
from src.hybrid_retrieval import (
    BM25Index,
    compact_course_codes,
    expand_course_codes,
    hybrid_retrieval_enabled,
    hybrid_retrieve,
    reciprocal_rank_fusion,
    tokenize,
)

ELECTIVE = (
    "Major electives include ISDA 150 Special Topics in Information Science "
    "and Data Analytics. Prerequisites are listed in the catalog entry."
)
ISDA_111 = (
    "ISDA 111 Information and Data Science. "
    "Prerequisites: ISDA 20A and ISDA 20B or equivalents."
)
GENERIC = (
    "Prerequisites: Completion of Core General Education and upper division "
    "standing are prerequisites to all SJSU studies courses."
)
OFFICE = (
    "Prerequisites: schedule advising appointments through the iSchool "
    "advising office before registering."
)


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
    """Return a fixed neighbor order so tests can see fusion change the rank."""

    def __init__(self, records, order):
        self._collection = _Collection(records)
        self._by_id = {
            doc_id: (content, metadata) for doc_id, content, metadata in records
        }
        self.order = order
        self.calls = []

    def similarity_search(self, query, k=3):
        self.calls.append((query, k))
        chosen = []
        for doc_id in self.order:
            content, metadata = self._by_id[doc_id]
            chosen.append(Document(page_content=content, metadata=metadata))
            if len(chosen) >= k:
                break
        return chosen


def _records():
    return [
        ("elective", ELECTIVE, {"source": "elective.txt"}),
        ("isda111", ISDA_111, {"source": "required.txt"}),
        ("generic", GENERIC, {"source": "studies.txt"}),
        ("office", OFFICE, {"source": "office.txt"}),
    ]


def _processor(store):
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.vector_store = store
    processor._index_lock = threading.RLock()
    return processor


def test_spaced_and_compact_course_codes_share_a_token():
    spaced = tokenize("What are the prerequisites for ISDA 150?")
    compact = tokenize("What are the prerequisites for ISDA150?")
    assert "isda150" in spaced
    assert "isda150" in compact
    assert "isda" not in spaced
    assert spaced == compact
    assert tokenize("Prerequisites: ISDA 20A and ISDA20A") == [
        "prerequisite",
        "isda20a",
        "isda20a",
    ]
    assert "for150" not in tokenize("prerequisites for ISDA 150")
    assert expand_course_codes("Compare ISDA150 with ISDA 20A") == (
        "Compare ISDA 150 with ISDA 20A"
    )
    assert compact_course_codes("ISDA 150") == "isda150"


def test_requisite_spellings_share_a_token_and_course_codes_stay_intact():
    prerequisite_forms = [
        "prerequisite",
        "prerequisites",
        "Prerequisite(s)",
        "prereq",
        "prereqs",
    ]
    assert [tokenize(form) for form in prerequisite_forms] == [
        ["prerequisite"]
    ] * len(prerequisite_forms)

    corequisite_forms = [
        "corequisite",
        "corequisites",
        "Corequisite(s)",
        "coreq",
        "coreqs",
    ]
    assert [tokenize(form) for form in corequisite_forms] == [
        ["corequisite"]
    ] * len(corequisite_forms)

    either_forms = [
        "pre/corequisite",
        "pre/corequisites",
        "Pre/Corequisite(s)",
        "pre/coreq",
        "pre / coreqs",
    ]
    assert [tokenize(form) for form in either_forms] == [["precorequisite"]] * len(
        either_forms
    )

    assert tokenize("Prerequisite(s): ISDA 20A. Pre/Corequisite(s): ISDA 101.") == [
        "prerequisite",
        "isda20a",
        "precorequisite",
        "isda101",
    ]
    assert tokenize("ISDA 20S and ISDA150") == ["isda20s", "isda150"]
    assert tokenize("courses") == tokenize("course") == ["course"]


def test_bm25_ranks_the_exact_course_code_above_generic_prerequisites():
    corpus = [tokenize(text) for text in (GENERIC, OFFICE, ELECTIVE, ISDA_111)]
    index = BM25Index(corpus)
    query = tokenize("What are the prerequisites for ISDA 150?")
    assert index.top_indices(query, limit=1) == [2]
    letter = tokenize("What are the prerequisites for ISDA20A?")
    assert index.top_indices(letter, limit=1) == [3]


def test_rrf_combines_lists_and_breaks_ties_toward_bm25():
    fused = reciprocal_rank_fusion(
        [["both", "vector-only"], ["both", "lexical-only"]],
        limit=3,
        tie_break=["both", "lexical-only"],
    )
    assert fused[0] == "both"

    tied = reciprocal_rank_fusion(
        [["vector-hit"], ["lexical-hit"]],
        limit=2,
        tie_break=["lexical-hit"],
    )
    assert tied == ["lexical-hit", "vector-hit"]


def test_hybrid_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("HYBRID_RETRIEVAL", raising=False)
    assert hybrid_retrieval_enabled() is False
    for value in ("false", "no", "0", "off", ""):
        monkeypatch.setenv("HYBRID_RETRIEVAL", value)
        assert hybrid_retrieval_enabled() is False
    for value in ("true", "TRUE", "yes", "on", "1"):
        monkeypatch.setenv("HYBRID_RETRIEVAL", value)
        assert hybrid_retrieval_enabled() is True


def test_flag_off_keeps_vector_order_and_the_original_query(monkeypatch):
    monkeypatch.delenv("HYBRID_RETRIEVAL", raising=False)
    store = _VectorStore(_records(), ["office", "generic", "isda111", "elective"])
    hits = _processor(store).retrieve_relevant_docs(
        "What are the prerequisites for ISDA150?", k=2
    )
    assert [hit.metadata["source"] for hit in hits] == ["office.txt", "studies.txt"]
    assert store.calls == [("What are the prerequisites for ISDA150?", 2)]


def test_flag_on_surfaces_the_course_code_chunk_despite_vector_order(monkeypatch):
    monkeypatch.setenv("HYBRID_RETRIEVAL", "true")
    store = _VectorStore(_records(), ["office", "generic", "isda111", "elective"])
    hits = hybrid_retrieve(store, "What are the prerequisites for ISDA150?", k=2)
    assert hits[0].metadata["source"] == "elective.txt"
    assert "ISDA 150" in hits[0].page_content
    assert store.calls[0][0] == "What are the prerequisites for ISDA 150?"

    through_processor = _processor(store).retrieve_relevant_docs(
        "What are the prerequisites for ISDA 111?", k=1
    )
    assert through_processor[0].metadata["source"] == "required.txt"
    assert "ISDA 20A" in through_processor[0].page_content


def test_hashing_index_hybrid_finds_compact_course_code(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("HYBRID_RETRIEVAL", "true")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "elective.txt").write_text(ELECTIVE, encoding="utf-8")
    (docs / "required.txt").write_text(ISDA_111, encoding="utf-8")
    (docs / "studies.txt").write_text(GENERIC, encoding="utf-8")
    (docs / "office.txt").write_text(OFFICE, encoding="utf-8")

    processor = DocumentProcessor(str(docs))
    assert processor.process_and_index() is not None

    compact = processor.retrieve_relevant_docs(
        "What are the prerequisites for ISDA150?", k=2
    )
    assert "ISDA 150" in compact[0].page_content

    spaced = processor.retrieve_relevant_docs(
        "What are the prerequisites for ISDA 111?", k=1
    )
    assert "ISDA 20A and ISDA 20B" in spaced[0].page_content

    monkeypatch.setenv("HYBRID_RETRIEVAL", "false")
    vector_only = processor.retrieve_relevant_docs(
        "What are the prerequisites for ISDA150?", k=2
    )
    assert vector_only
    assert all(isinstance(hit.page_content, str) for hit in vector_only)
