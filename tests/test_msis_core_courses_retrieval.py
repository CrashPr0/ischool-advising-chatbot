"""MSIS core-course retrieval covers every course the official page lists.

Offline: chunks the stored official snapshot with the production splitter and
retrieves through the production MSIS-scoped path (BM25 fused with hashing
embeddings, then program/title preference). No network or paid embeddings.
"""

import re
from pathlib import Path

import pytest
import yaml

from src.config import Config
from src.document_processor import DocumentProcessor
from src.rag_chatbot import DEFAULT_RAG_TOP_K

DOCS = Path(__file__).resolve().parent.parent / "data" / "advising_docs"
REQUIRED_URL = "https://ischool.sjsu.edu/informatics-required-courses"
# Course lines published on the official page, in its own order.
OFFICIAL_CORE = [f"INFM {n}" for n in (200, 201, 202, 203, 204, 205, 206, 207, 208, 211)]
QUESTIONS = [
    "What are the core courses for the MS Informatics program?",
    "What are the required courses for the MS in Informatics?",
    "Which classes are required for the Informatics master's degree?",
]


@pytest.fixture(scope="module")
def corpus_processor(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    patch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path_factory.mktemp("msis-core")))
    patch.setenv("HYBRID_RETRIEVAL", "true")
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    yield processor
    patch.undo()


@pytest.fixture(scope="module")
def msis_chunks():
    processor = DocumentProcessor(docs_path=str(DOCS))
    chunks = processor.split_documents(processor.load_documents())
    return [c for c in chunks if "MSIS" in (c.metadata.get("programs") or [])]


def _snapshot_text():
    for meta_path in (DOCS / "meta").glob("*.yml"):
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if meta.get("source_url") == REQUIRED_URL:
            return (DOCS / "txt" / f"{meta['doc_id']}.txt").read_text(encoding="utf-8")
    raise AssertionError("official required-courses snapshot is not ingested")


def test_snapshot_lists_every_official_core_course():
    text = _snapshot_text()
    for code in OFFICIAL_CORE:
        assert re.search(rf"^{code} ", text, re.M), code


def test_core_list_is_not_split_across_chunks(msis_chunks):
    """Splitting the list leaves some courses out of every retrieved chunk."""
    page = [c for c in msis_chunks if c.metadata.get("source_url") == REQUIRED_URL]
    assert any(
        all(code in c.page_content for code in OFFICIAL_CORE) for c in page
    ), "no single chunk carries the whole official core-course list"


@pytest.mark.parametrize("question", QUESTIONS)
def test_top_k_retrieval_covers_all_core_courses(corpus_processor, question):
    hits = corpus_processor.retrieve_relevant_docs(
        question, k=DEFAULT_RAG_TOP_K, program="msis"
    )
    assert hits[0].metadata["source_url"] == REQUIRED_URL
    retrieved = " ".join(c.page_content for c in hits)
    missing = [code for code in OFFICIAL_CORE if code not in retrieved]
    assert not missing, f"top-{DEFAULT_RAG_TOP_K} misses {missing}"
