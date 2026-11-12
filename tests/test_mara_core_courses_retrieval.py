"""MARA core-course retrieval covers every course the official page lists.

Offline: chunks the stored official snapshot with the production splitter,
ranks MARA chunks with the production BM25 index, and retrieves through the
production hybrid path (BM25 fused with hashing embeddings). A stub model
answers only from that retrieved context. No network or paid embeddings.
"""

import re
from pathlib import Path

import pytest
import yaml

from src.config import Config
from src.document_processor import DocumentProcessor
from src.hybrid_retrieval import BM25Index, tokenize
from src.knowledge_base import get_knowledge_base
from src.rag_chatbot import DEFAULT_RAG_TOP_K, RAGAdvisingChatbot

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


@pytest.fixture(scope="module")
def corpus_processor(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    patch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path_factory.mktemp("mara-core")))
    patch.setenv("HYBRID_RETRIEVAL", "true")
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    yield processor
    patch.undo()


def _snapshot_text():
    for meta_path in (DOCS / "meta").glob("*.yml"):
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if meta.get("source_url") == REQUIRED_URL:
            return (DOCS / "txt" / f"{meta['doc_id']}.txt").read_text(encoding="utf-8")
    raise AssertionError("official required-courses snapshot is not ingested")


def _published_title_lines(text=None):
    """Title lines copied from the stored required-courses snapshot.

    Each code is published once in the description and again in the compact
    list. Both copies must be the snapshot's own line, including its spacing.
    """
    text = _snapshot_text() if text is None else text
    grouped = {code: [] for code in OFFICIAL_CORE}
    for line in text.splitlines():
        if "Handbook" in line:
            continue
        for code in OFFICIAL_CORE:
            if line.startswith(code) and (
                len(line) == len(code) or line[len(code)].isspace()
            ):
                grouped[code].append(line)
                break
    missing = [code for code, lines in grouped.items() if not lines]
    assert not missing, f"snapshot is missing {missing}"
    for code, lines in grouped.items():
        assert len(set(lines)) == 1, (code, lines)
    return [grouped[code][0] for code in OFFICIAL_CORE]


def _display_title(line):
    return " ".join(line.split())


def test_snapshot_lists_every_official_core_course():
    text = _snapshot_text()
    titles = _published_title_lines(text)
    assert [line.split()[0] + " " + line.split()[1] for line in titles] == OFFICIAL_CORE
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
    retrieved = "\n".join(c.page_content for c in hits)
    missing = [line for line in _published_title_lines() if line not in retrieved]
    assert not missing, f"top-{DEFAULT_RAG_TOP_K} misses {missing}"


@pytest.mark.parametrize("program", ["mara", None])
@pytest.mark.parametrize("question", QUESTIONS)
def test_hybrid_retrieval_returns_the_official_page_and_titles(
    corpus_processor, question, program
):
    hits = corpus_processor.retrieve_relevant_docs(
        question, k=DEFAULT_RAG_TOP_K, program=program
    )
    assert hits
    assert hits[0].metadata.get("source_url") == REQUIRED_URL
    retrieved = "\n".join(c.page_content for c in hits)
    missing = [line for line in _published_title_lines() if line not in retrieved]
    assert not missing, f"top-{DEFAULT_RAG_TOP_K} misses {missing}"


class _ContextAnswer:
    """List only the official titles that the retrieved context actually contains."""

    def __init__(self, titles):
        self.titles = titles

    def invoke(self, payload):
        context = payload["context"]
        assert REQUIRED_URL in context
        present = [line for line in self.titles if line in context]
        listed = "\n".join(f"- {_display_title(line)}" for line in present)
        answer = (
            "The MARA required core courses are:\n"
            f"{listed}\n"
            f"[MARA Required Courses]({REQUIRED_URL})"
        )
        return type("Reply", (), {"content": answer})()


@pytest.mark.parametrize("program", ["mara", None])
@pytest.mark.parametrize("question", QUESTIONS)
def test_core_questions_answer_with_the_published_course_list(
    corpus_processor, question, program
):
    titles = _published_title_lines()
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = get_knowledge_base()
    bot.doc_processor = corpus_processor
    bot.chain = _ContextAnswer(titles)
    result = bot.chat(question, program=program)
    assert result["source"] == "rag"
    assert f"[MARA Required Courses]({REQUIRED_URL})" in result["response"]
    for line in titles:
        assert _display_title(line) in result["response"]
    assert "INFO 200" not in result["response"]
