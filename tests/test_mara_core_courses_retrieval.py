"""MARA core-course retrieval covers every course the official page lists.

Offline: chunks the stored official snapshot with the production splitter and
ranks the MARA chunks with the production BM25 index. No network or embeddings.
"""

import re
from pathlib import Path

import pytest
import yaml

from src.document_processor import DocumentProcessor
from src.hybrid_retrieval import BM25Index, tokenize
from src.rag_chatbot import DEFAULT_RAG_TOP_K

DOCS = Path(__file__).resolve().parent.parent / "data" / "advising_docs"
REQUIRED_URL = "https://ischool.sjsu.edu/mara-required-courses"
# Course lines published on the official page, in its own order.
OFFICIAL_CORE = [f"MARA {n}" for n in (200, 204, 210, 211, 249, 283, 285, 286, 287, 289)]
QUESTIONS = [
    "What are the MARA core courses?",
    "What are the core courses for the MARA program?",
    "What are the required courses for MARA?",
    "Which classes are required for the Master of Archives and Records Administration?",
]


@pytest.fixture(scope="module")
def mara_chunks():
    processor = DocumentProcessor(docs_path=str(DOCS))
    chunks = processor.split_documents(processor.load_documents())
    return [c for c in chunks if "MARA" in (c.metadata.get("programs") or [])]


def _snapshot_text():
    for meta_path in (DOCS / "meta").glob("*.yml"):
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if meta.get("source_url") == REQUIRED_URL:
            return (DOCS / "txt" / f"{meta['doc_id']}.txt").read_text(encoding="utf-8")
    raise AssertionError("official required-courses snapshot is not ingested")


def test_snapshot_lists_every_official_core_course():
    text = _snapshot_text()
    for code in OFFICIAL_CORE:
        assert re.search(rf"^{code}\s", text, re.M), code


def test_core_list_is_not_split_across_chunks(mara_chunks):
    """Splitting the list leaves some courses out of every retrieved chunk."""
    page = [c for c in mara_chunks if c.metadata.get("source_url") == REQUIRED_URL]
    assert any(
        all(re.search(rf"^{code}\s", c.page_content, re.M) for code in OFFICIAL_CORE)
        for c in page
    ), "no single chunk carries the whole official core-course list"


@pytest.mark.parametrize("question", QUESTIONS)
def test_top_k_retrieval_covers_all_core_courses(mara_chunks, question):
    index = BM25Index([tokenize(c.page_content) for c in mara_chunks])
    hits = [mara_chunks[i] for i in index.top_indices(tokenize(question), DEFAULT_RAG_TOP_K)]
    assert any(c.metadata.get("source_url") == REQUIRED_URL for c in hits)
    retrieved = " ".join(c.page_content for c in hits)
    missing = [code for code in OFFICIAL_CORE if code not in retrieved]
    assert not missing, f"top-{DEFAULT_RAG_TOP_K} misses {missing}"
