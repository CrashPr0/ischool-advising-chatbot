"""Stored MSIS capstone eligibility must be answerable without paid embeddings."""

from pathlib import Path

import pytest

from src.config import Config
from src.document_processor import DocumentProcessor
from src.program_scope import metadata_programs
from src.rag_chatbot import DEFAULT_RAG_TOP_K

DOCS = Path(__file__).resolve().parents[1] / "data" / "advising_docs"
CAPSTONE_URL = (
    "https://ischool.sjsu.edu/"
    "infm-211-informatics-culminating-project-capstone-course"
)
QUESTIONS = [
    "What do I need before I can enroll in INFM 211?",
    "What are the prerequisites for INFM 211?",
    "Am I eligible for the MSIS capstone?",
    "What do I need before starting the Informatics culminating project?",
]


@pytest.fixture(scope="module")
def capstone_processor(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
        patch.setattr(
            Config, "VECTOR_STORE_PATH", str(tmp_path_factory.mktemp("msis-capstone"))
        )
        processor = DocumentProcessor(str(DOCS))
        processor.process_and_index()
        yield processor


@pytest.mark.parametrize("mode", ["hybrid", "lexical"])
@pytest.mark.parametrize("question", QUESTIONS)
def test_capstone_eligibility_evidence(capstone_processor, monkeypatch, mode, question):
    monkeypatch.setenv("HYBRID_RETRIEVAL", "true")
    if mode == "lexical":
        def unavailable(*args, **kwargs):
            raise RuntimeError("Offline BM25 fallback")

        monkeypatch.setattr(
            capstone_processor.vector_store, "similarity_search", unavailable
        )
    hits = capstone_processor.retrieve_relevant_docs(
        question, k=DEFAULT_RAG_TOP_K, program="msis"
    )
    assert hits
    assert all("MSIS" in metadata_programs(hit.metadata) for hit in hits)
    # Require the actual eligibility evidence, not just a capstone page URL.
    required = [
        "Be matriculated in the MS in Informatics degree program",
        "3.0 CUM GPA or above",
        "Successful completion of 9 required courses",
        *[f"INFM {number}" for number in range(200, 209)],
    ]
    evidence = [
        " ".join(hit.page_content.split())
        for hit in hits if hit.metadata.get("source_url") == CAPSTONE_URL
    ]
    assert any(all(snippet in text for snippet in required) for text in evidence), (
        mode, question, evidence
    )
