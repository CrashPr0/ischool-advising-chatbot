"""MLIS core-course retrieval covers every course the official page lists.

Offline: chunks the stored official snapshot with the production splitter and
ranks the MLIS chunks with the production BM25 index. No network or embeddings.
"""

import re
from pathlib import Path

import pytest
import yaml

from src.document_processor import DocumentProcessor
from src.hybrid_retrieval import BM25Index, tokenize
from src.rag_chatbot import DEFAULT_RAG_TOP_K

DOCS = Path(__file__).resolve().parent.parent / "data" / "advising_docs"
REQUIRED_URL = "https://ischool.sjsu.edu/mlis-required-courses"
# Core classes named on the official page (within the first 16 units).
OFFICIAL_CORE = ["INFO 200", "INFO 202", "INFO 203", "INFO 204"]
QUESTIONS = [
    "What are the MLIS core courses?",
    "What are the core courses for the MLIS program?",
]


@pytest.fixture(scope="module")
def mlis_chunks():
    processor = DocumentProcessor(docs_path=str(DOCS))
    chunks = processor.split_documents(processor.load_documents())
    return [c for c in chunks if "MLIS" in (c.metadata.get("programs") or [])]


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


def test_core_list_is_not_split_across_chunks(mlis_chunks):
    """Splitting the list leaves some courses out of every retrieved chunk."""
    page = [c for c in mlis_chunks if c.metadata.get("source_url") == REQUIRED_URL]
    assert any(
        all(code in c.page_content for code in OFFICIAL_CORE) for c in page
    ), "no single chunk carries the whole official core-course list"


def test_required_courses_page_ranks_in_top_k(mlis_chunks):
    """The official required-courses URL must itself appear in top-k hits."""
    index = BM25Index([tokenize(c.page_content) for c in mlis_chunks])
    question = "What are the MLIS core courses?"
    hits = [mlis_chunks[i] for i in index.top_indices(tokenize(question), DEFAULT_RAG_TOP_K)]
    urls = {c.metadata.get("source_url") for c in hits}
    assert REQUIRED_URL in urls, f"top-{DEFAULT_RAG_TOP_K} urls={sorted(u for u in urls if u)}"


@pytest.mark.parametrize("question", QUESTIONS)
def test_top_k_retrieval_covers_all_core_courses(mlis_chunks, question):
    index = BM25Index([tokenize(c.page_content) for c in mlis_chunks])
    hits = [mlis_chunks[i] for i in index.top_indices(tokenize(question), DEFAULT_RAG_TOP_K)]
    retrieved = " ".join(c.page_content for c in hits)
    missing = [code for code in OFFICIAL_CORE if code not in retrieved]
    assert not missing, f"top-{DEFAULT_RAG_TOP_K} misses {missing}"
