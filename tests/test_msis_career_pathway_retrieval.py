"""MSIS career-pathway questions retrieve the pathway course list.

Offline: chunks the stored official snapshots with the production splitter,
ranks MSIS chunks with the production BM25 index, and runs production hybrid
retrieval over deterministic hashing embeddings. No network or model calls.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

from src.config import Config
from src.document_processor import DocumentProcessor
from src.hybrid_retrieval import BM25Index, tokenize
from src.rag_chatbot import DEFAULT_RAG_TOP_K

DOCS = Path(__file__).resolve().parent.parent / "data" / "advising_docs"
ROTATIONS_URL = "https://ischool.sjsu.edu/informatics-course-rotations"
# Career Pathway row of the official rotations table.
PATHWAYS = {
    "Health": ["INFM 210", "INFM 213", "INFM 214"],
    "Cybersecurity": ["INFM 215", "INFM 216", "INFM 217"],
}
ALL_PATHWAY = [code for codes in PATHWAYS.values() for code in codes]
HYBRID_QUESTIONS = [
    "What are the MSIS career pathway courses?",
    "Which cybersecurity courses can I take in MSIS?",
    "Which health career pathway courses can I take in the MS Informatics?",
]
LEXICAL_QUESTIONS = HYBRID_QUESTIONS + [
    "What health informatics courses are offered in the MS Informatics?",
]


def _meta():
    for meta_path in (DOCS / "meta").glob("*.yml"):
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if meta.get("source_url") == ROTATIONS_URL:
            return meta
    raise AssertionError("official course-rotations snapshot is not ingested")


def _covers_pathway(chunks) -> bool:
    retrieved = " ".join(c.page_content for c in chunks)
    return all(code in retrieved for code in ALL_PATHWAY)


@pytest.fixture(scope="module")
def msis_chunks():
    processor = DocumentProcessor(docs_path=str(DOCS))
    chunks = processor.split_documents(processor.load_documents())
    return [c for c in chunks if "MSIS" in (c.metadata.get("programs") or [])]


@pytest.fixture(scope="module")
def hashing_processor(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    patch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path_factory.mktemp("msis-index")))
    patch.setenv("HYBRID_RETRIEVAL", "true")
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    yield processor
    patch.undo()


def test_snapshot_hash_and_header_match_the_official_pathway_row():
    meta = _meta()
    path = DOCS / "txt" / f"{meta['doc_id']}.txt"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert meta["content_sha256"] == digest
    manifest = json.loads((DOCS / "scrape" / "manifest.json").read_text())
    entry = next(e for e in manifest["documents"] if e["doc_id"] == meta["doc_id"])
    assert entry["content_sha256"] == digest

    row = next(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("| Career Pathway")
    )
    for code in ALL_PATHWAY:
        assert code in row, code
    header = meta["retrieval_header"]
    assert set(re.findall(r"INFM \d{3}", header)) == set(ALL_PATHWAY)
    for pathway, codes in PATHWAYS.items():
        assert f"{pathway}: {', '.join(codes)}" in header, pathway


def test_pathway_row_is_a_focused_chunk_with_session_columns(msis_chunks):
    page = [c for c in msis_chunks if c.metadata.get("source_url") == ROTATIONS_URL]
    pathway = [c for c in page if "| Career Pathway" in c.page_content]
    assert len(pathway) == 1
    content = pathway[0].page_content
    assert content.startswith(_meta()["retrieval_header"])
    assert "Fall Session 2" in content and "Summer Session" in content
    assert "INFM 284 special topics" in content
    assert "INFM 200 – Informatics: Fundamentals" not in content
    # The core rows remain in the rotations page, unchanged.
    assert any("INFM 200 – Informatics: Fundamentals" in c.page_content for c in page)


@pytest.mark.parametrize("question", LEXICAL_QUESTIONS)
def test_bm25_top_k_covers_every_pathway_course(msis_chunks, question):
    index = BM25Index([tokenize(c.page_content) for c in msis_chunks])
    hits = [msis_chunks[i] for i in index.top_indices(tokenize(question), DEFAULT_RAG_TOP_K)]
    assert _covers_pathway(hits), f"top-{DEFAULT_RAG_TOP_K} misses pathway courses"


@pytest.mark.parametrize("question", HYBRID_QUESTIONS)
def test_hybrid_top_k_cites_the_rotations_page(hashing_processor, question):
    hits = hashing_processor.retrieve_relevant_docs(
        question, k=DEFAULT_RAG_TOP_K, program="msis"
    )
    assert any(c.metadata.get("source_url") == ROTATIONS_URL for c in hits)
    assert _covers_pathway(hits), f"top-{DEFAULT_RAG_TOP_K} misses pathway courses"
