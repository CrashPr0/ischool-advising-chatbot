"""Questions that name two courses retrieve both catalog course pages.

Regression for "Can I take ISDA 130 and 131 at the same time?": the bare
``131`` never matched the ``isda131`` token of the ISDA 131 page, so only the
ISDA 130 page reached the model and it could not see that ISDA 131 lists
ISDA 130 as its prerequisite.

Offline: production splitter, BM25, and hybrid retrieval over deterministic
hashing embeddings on the committed corpus. A stub model answers only from the
retrieved context. No network or paid embeddings.
"""

from pathlib import Path

import pytest
import yaml
from langchain_core.documents import Document

from src.catalog_recency import include_requested_course_pages
from src.config import Config
from src.document_processor import DocumentProcessor
from src.hybrid_retrieval import (
    expand_shared_prefix_course_codes,
    requested_course_codes,
)
from src.knowledge_base import get_knowledge_base
from src.rag_chatbot import DEFAULT_RAG_TOP_K, RAGAdvisingChatbot

DOCS = Path(__file__).resolve().parent.parent / "data" / "advising_docs"
ISDA_130_URL = "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=202916"
ISDA_131_URL = "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=202917"
ISDA_131_PREREQ = "Prerequisite(s): ISDA 130."
QUESTIONS = [
    "Can I take ISDA 130 and 131 at the same time?",
    "Can I take ISDA 130 and ISDA 131 together?",
    "Can I take ISDA 130 and 131 concurrently?",
    "Can I take ISDA 131 and 130 in the same semester?",
]


@pytest.fixture(scope="module")
def corpus_processor(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    patch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path_factory.mktemp("isda-pair")))
    patch.setenv("HYBRID_RETRIEVAL", "true")
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    yield processor
    patch.undo()


def _snapshot(url):
    for meta_path in (DOCS / "meta").glob("*.yml"):
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if meta.get("source_url") == url:
            return (DOCS / "txt" / f"{meta['doc_id']}.txt").read_text(encoding="utf-8")
    raise AssertionError(f"{url} is not ingested")


@pytest.mark.parametrize(
    ("query", "expanded"),
    [
        (
            "Can I take ISDA 130 and 131 at the same time?",
            "Can I take ISDA 130 and ISDA 131 at the same time?",
        ),
        ("INFO 200, 202, and 204", "INFO 200, INFO 202, and INFO 204"),
        ("ISDA 20A/20B", "ISDA 20A/ISDA 20B"),
        ("MARA 200 & 204 together?", "MARA 200 & MARA 204 together?"),
        ("INFM 210 or 213?", "INFM 210 or INFM 213?"),
        # A number followed by a unit word is a quantity, not a course.
        ("ISDA 130 and 20 units", "ISDA 130 and 20 units"),
        # Unsupported prefixes are left alone.
        ("ABC 100 and 200", "ABC 100 and 200"),
    ],
)
def test_shared_prefix_course_lists_repeat_the_department(query, expanded):
    assert expand_shared_prefix_course_codes(query) == expanded


def test_requested_course_codes_reads_shared_prefix_lists():
    assert requested_course_codes("Can I take ISDA 130 and 131 together?") == [
        "isda130",
        "isda131",
    ]
    assert requested_course_codes("INFO 200, 202, or MARA 210") == [
        "info200",
        "info202",
        "mara210",
    ]
    assert requested_course_codes("What are the ISDA core courses?") == []


def _course_record(code, title, url):
    text = f"{code} - {title}\n3 unit(s)\nSource URL: {url}\n"
    return (code, text, {"title": f"{code} - {title}", "source_url": url})


def test_requested_course_pages_are_pinned_for_any_prefix():
    first = _course_record(
        "INFO 200", "Information Communities",
        "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=1",
    )
    second = _course_record(
        "INFO 202", "Information Retrieval System Design",
        "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=2",
    )
    filler = [Document(page_content=f"filler {n}", metadata={}) for n in range(3)]
    first_doc = Document(page_content=first[1], metadata=first[2])

    hits = include_requested_course_pages(
        [first_doc] + filler, [first, second], ["info200", "info202"], k=3
    )
    assert [hit.page_content for hit in hits] == [first[1], second[1], "filler 0"]
    assert hits[0] is first_doc

    # One code, or every page already present, leaves the ranking alone.
    assert include_requested_course_pages(filler, [first, second], ["info200"], 3) == filler
    both = [first_doc, Document(page_content=second[1], metadata=second[2])]
    assert include_requested_course_pages(both, [first, second], ["info200", "info202"], 3) == both


def test_isda_131_snapshot_lists_isda_130_as_its_prerequisite():
    text = _snapshot(ISDA_131_URL)
    assert text.startswith("ISDA 131 - Information Architecture\n")
    assert ISDA_131_PREREQ in text
    assert "Corequisite" not in text
    assert "ISDA 130 - User Centered Interface Architecture" in _snapshot(ISDA_130_URL)


@pytest.mark.parametrize("program", ["isda", None])
@pytest.mark.parametrize("question", QUESTIONS)
def test_two_course_question_retrieves_both_course_pages(
    corpus_processor, question, program
):
    hits = corpus_processor.retrieve_relevant_docs(
        question, k=DEFAULT_RAG_TOP_K, program=program
    )
    assert len(hits) <= DEFAULT_RAG_TOP_K
    by_url = {hit.metadata.get("source_url"): hit for hit in hits}
    assert ISDA_130_URL in by_url
    assert ISDA_131_URL in by_url
    assert ISDA_131_PREREQ in by_url[ISDA_131_URL].page_content
    assert by_url[ISDA_131_URL].page_content.startswith("ISDA 131 - Information Architecture")


class _RequisiteAnswer:
    """Answer only from requisite lines present in the retrieved context."""

    def __init__(self):
        self.payload = None

    def invoke(self, payload):
        self.payload = payload
        context = payload["context"]
        assert ISDA_130_URL in context and ISDA_131_URL in context
        assert ISDA_131_PREREQ in context
        answer = (
            "No. The 2026-2027 catalog lists ISDA 130 as the prerequisite for "
            "ISDA 131 (\"Prerequisite(s): ISDA 130.\"), so take ISDA 130 first. "
            f"[ISDA 131 - Information Architecture]({ISDA_131_URL})"
        )
        return type("Reply", (), {"content": answer})()


@pytest.mark.parametrize("program", ["isda", None])
def test_chat_sends_both_course_pages_and_the_requisite_rule(corpus_processor, program):
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = get_knowledge_base()
    bot.doc_processor = corpus_processor
    bot.chain = _RequisiteAnswer()
    result = bot.chat(QUESTIONS[0], program=program)
    assert result["source"] == "rag"
    assert "ISDA 130 as the prerequisite for ISDA 131" in result["response"]
    assert f"({ISDA_131_URL})" in result["response"]


def test_system_prompt_reads_prerequisites_as_completed_before():
    prompt = RAGAdvisingChatbot.SYSTEM_PROMPT
    rule = prompt.split("11. ", 1)[1].split("12. ", 1)[0]
    assert '"Prerequisite(s):" line names courses that must be completed' in rule
    assert "before that course" in rule
    assert "corequisite" in rule
    assert "may be taken" in rule and "concurrently" in rule
    assert "check both courses' requisite lines" in rule
    assert "ask advising about exceptions" in rule
    assert "Never infer a requisite the sources do not state." in rule
    # The suggestion rule stays last.
    assert "12. Answer the question first and in full." in prompt
