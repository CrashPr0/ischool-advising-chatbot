"""Offline golden retrieval against the real BS ISDA + MLIS corpus."""

import json
from pathlib import Path
from hashlib import sha256

import pytest

from src.config import Config
from src.document_processor import DocumentProcessor
from src.program_scope import prefer_program_documents, query_programs
from src.rag_chatbot import RAGAdvisingChatbot, _restrict_reply_links
from src.knowledge_base import get_knowledge_base
from langchain_core.documents import Document

DOCS = Path(__file__).resolve().parents[1] / "data" / "advising_docs"


@pytest.fixture(scope="module")
def corpus_processor(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    patch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path_factory.mktemp("mlis-index")))
    patch.setenv("HYBRID_RETRIEVAL", "true")
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    yield processor
    patch.undo()


@pytest.mark.parametrize("question,source_parts", [
    ("What are the MLIS core courses?", ["/mlis-required-courses"]),
    ("How do I apply to the MLIS program?", ["/graduate-application", "/admissions", "/library-and-information-science-masters-degree.php"]),
    ("What is the MLIS e-portfolio?", ["/info-289-e-portfolio-faqs"]),
])
def test_mlis_golden_retrieval(corpus_processor, question, source_parts):
    hits = corpus_processor.retrieve_relevant_docs(question, k=8)
    assert hits
    assert any(any(part in hit.metadata.get("source_url", "") for part in source_parts) for hit in hits)
    assert "MLIS" in hits[0].metadata["programs"]


def test_isda_still_leads_core_retrieval(corpus_processor):
    hits = corpus_processor.retrieve_relevant_docs("What are the ISDA core courses?", k=8)
    assert hits[0].metadata["programs"] == ["ISDA"]
    assert any("catalog.sjsu.edu/preview_program.php" in hit.metadata["source_url"] for hit in hits)


def test_all_official_mlis_pages_load_program_metadata(corpus_processor):
    loaded = corpus_processor.load_documents()
    mlis = [doc for doc in loaded if "MLIS" in doc.metadata.get("programs", [])]
    assert len(mlis) == 19
    assert any(doc.page_content.startswith("INFO 210 -") for doc in mlis)
    assert all(doc.metadata.get("source_url", "").startswith("https://") for doc in mlis)
    assert all(
        sha256(Path(doc.metadata["source"]).read_bytes()).hexdigest()
        == doc.metadata["content_sha256"] for doc in mlis
    )
    manifest = json.loads((DOCS / "scrape" / "manifest.json").read_text())
    assert {entry["source_url"] for entry in manifest["documents"] if "MLIS" in entry["programs"]} == {doc.metadata["source_url"] for doc in mlis}


def test_mlis_core_answer_cites_retrieved_source_through_guardrail(corpus_processor):
    question = "What are the MLIS core courses?"
    source = "https://ischool.sjsu.edu/mlis-career-pathways"
    invented = source + "#core-courses"
    hits = corpus_processor.retrieve_relevant_docs(question, k=8, program="mlis")
    core = next(doc for doc in hits if doc.metadata.get("source_url") == source
                and "Core Courses:" in doc.page_content)
    # The ingested page and its chunks have the URL only in companion metadata.
    assert source not in core.page_content
    for number in ("203", "200", "202", "204"):
        assert f"INFO {number}" in core.page_content
    assert get_knowledge_base().find_answer(question) is None

    class CitationChain:
        def invoke(self, payload):
            assert f"Source URL: {source}" in payload["context"]
            assert "Selected Program: MLIS" in payload["context"]
            assert payload["question"] == question
            return type("Reply", (), {"content": (
                "The MLIS core courses are INFO 203, 200, 202, and 204. "
                f"[MLIS Core Courses]({source}). Also see {invented}."
            )})()

    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = get_knowledge_base()
    bot.doc_processor = corpus_processor
    bot.chain = CitationChain()
    result = bot.chat(question, program="mlis")
    assert result["source"] == "rag"
    assert f"[MLIS Core Courses]({source})" in result["response"]
    assert invented not in result["response"]
    assert _restrict_reply_links(result["response"], hits) == (result["response"], 0)


def test_every_mlis_chunk_exposes_its_ingested_citation(corpus_processor):
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    mlis = [doc for doc in corpus_processor.load_documents()
            if "MLIS" in doc.metadata.get("programs", [])]
    for chunk in corpus_processor.split_documents(mlis):
        source = chunk.metadata["source_url"]
        context = bot._build_context("MLIS requirements", [chunk])
        assert f"Source URL: {source}\n" in context
        reply = f"[Official source]({source})"
        cleaned, dropped = _restrict_reply_links(reply, [chunk])
        assert source in cleaned
        assert dropped == 0
        # Official-domain URLs still need to be retrieved, not merely plausible.
        invented = "https://ischool.sjsu.edu/not-an-ingested-page"
        assert invented not in _restrict_reply_links(invented, [chunk])[0]


def test_prompt_requires_citations_from_official_non_catalog_pages():
    prompt = RAGAdvisingChatbot.SYSTEM_PROMPT
    assert "Markdown source links in every factual" in prompt
    assert "Copy the exact Source URL" in prompt
    assert "ischool.sjsu.edu" in prompt
    assert "SJSU (sjsu.edu) pages are valid sources for MLIS" in prompt
    assert "priority rule applies to conflicting facts" in prompt


@pytest.mark.parametrize("question,expected", [
    ("library science core courses", {"MLIS"}),
    ("Master’s admissions", {"MLIS"}),
    ("master's course load", {"MLIS"}),
    ("undergraduate requirements", {"ISDA"}),
    ("ISDA150 prerequisites", {"ISDA"}),
    ("Compare ISDA and MLIS", {"ISDA", "MLIS"}),
    ("What are the core courses?", set()),
])
def test_program_hints(question, expected):
    assert query_programs(question) == expected


def test_program_preference_accepts_lists_and_serialized_metadata():
    isda = Document(page_content="ISDA", metadata={"programs": ["ISDA"]})
    shared = Document(page_content="SJSU", metadata={})
    mlis = Document(page_content="MLIS", metadata={"programs": '["MLIS"]'})
    original = [isda, shared, mlis]
    assert prefer_program_documents(original, "MLIS requirements") == [mlis, shared, isda]
    assert prefer_program_documents(original, "core courses") == original
    assert prefer_program_documents(original, "Compare ISDA and MLIS") == original
    assert prefer_program_documents([shared], "MLIS requirements") == [shared]


def test_prompt_scope_and_program_separation():
    prompt = RAGAdvisingChatbot.SYSTEM_PROMPT
    assert "SJSU iSchool BS ISDA and MLIS programs" in prompt
    assert "Never mix ISDA and MLIS requirements" in prompt
    assert "ask which program or give both" in prompt
    assert "Refuse unrelated requests" in prompt
    for program in ("MARA", "Informatics", "MSIDT", "Teacher Librarian"):
        assert program in prompt


def test_both_pages_offer_mlis_topics_and_keep_isda():
    root = DOCS.parents[1]
    for name in ("templates/index.html", "docs/index.html"):
        text = (root / name).read_text()
        assert "What are the ISDA core courses?" in text
        assert "What are the MLIS core courses?" in text
        assert "How do I apply to the MLIS program?" in text
        assert "What is the MLIS e-portfolio?" in text
        assert "MLIS advising" in text
        assert "20261023-program-picker" in text


def test_mlis_retrieval_survives_query_embedding_failure(corpus_processor, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("Embedding service unavailable")

    monkeypatch.setattr(corpus_processor.vector_store, "similarity_search", unavailable)
    hits = corpus_processor.retrieve_relevant_docs("What are the MLIS core courses?", k=8)
    assert any("/mlis-required-courses" in hit.metadata.get("source_url", "") for hit in hits)
    assert "MLIS" in hits[0].metadata["programs"]


@pytest.mark.parametrize("program,expected", [("isda", "ISDA"), ("mlis", "MLIS")])
def test_selected_program_controls_ambiguous_retrieval(
    corpus_processor, program, expected
):
    hits = corpus_processor.retrieve_relevant_docs(
        "What are the core courses?", k=8, program=program
    )
    assert expected in hits[0].metadata["programs"]


@pytest.mark.parametrize("program", [None, "bogus", [], {}, 42, "MLIS"])
def test_invalid_selection_uses_keyword_fallback(program):
    isda = Document(page_content="ISDA", metadata={"programs": ["ISDA"]})
    mlis = Document(page_content="MLIS", metadata={"programs": ["MLIS"]})
    assert prefer_program_documents([isda, mlis], "master's core courses", program) == [
        mlis,
        isda,
    ]


def test_explicit_selection_overrides_masters_heuristic():
    isda = Document(page_content="ISDA", metadata={"programs": ["ISDA"]})
    mlis = Document(page_content="MLIS", metadata={"programs": ["MLIS"]})
    assert prefer_program_documents([mlis, isda], "master's core courses", "isda") == [
        isda,
        mlis,
    ]
    # "Not sure / both" falls back to the keyword heuristic.
    assert prefer_program_documents([isda, mlis], "master's core courses", "both") == [
        mlis,
        isda,
    ]
    assert prefer_program_documents([isda, mlis], "core courses", "both") == [isda, mlis]


@pytest.mark.parametrize("mode", ["hybrid", "vector", "lexical"])
@pytest.mark.parametrize("program,expected", [("isda", "ISDA"), ("mlis", "MLIS")])
def test_selection_overrides_query_in_each_retrieval_path(
    corpus_processor, monkeypatch, mode, program, expected
):
    monkeypatch.setenv("HYBRID_RETRIEVAL", "false" if mode == "vector" else "true")
    if mode == "lexical":

        def unavailable(*args, **kwargs):
            raise RuntimeError("Embedding service unavailable")

        monkeypatch.setattr(
            corpus_processor.vector_store, "similarity_search", unavailable
        )
    hits = corpus_processor.retrieve_relevant_docs(
        "What are the master's core courses?", k=8, program=program
    )
    assert expected in hits[0].metadata["programs"]


def test_invalid_retrieval_program_matches_no_selection(corpus_processor):
    query = "What are the master's core courses?"
    expected = corpus_processor.retrieve_relevant_docs(query, k=8)
    for invalid in ("invalid", {}, []):
        assert (
            corpus_processor.retrieve_relevant_docs(query, k=8, program=invalid)
            == expected
        )
