"""BS ISDA planning questions retrieve the 2026-2027 catalog roadmap.

Regression for "I want to graduate in two years. How should I plan my
courses?": the corpus only had the program page's "The 2-Year and 3-Year
Roadmap is available online", so the model had no semester plan to give.

Offline: production splitter and hybrid retrieval over deterministic hashing
embeddings on the committed corpus. A stub model answers only from the
retrieved context. No network or paid embeddings.
"""

import json
import re
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from src.config import Config
from src.document_processor import DocumentProcessor
from src.knowledge_base import get_knowledge_base
from src.rag_chatbot import DEFAULT_RAG_TOP_K, RAGAdvisingChatbot
from src.roadmaps import (
    ROADMAP_PROMPT_NOTE,
    is_roadmap_query,
    roadmap_years_in_query,
)

DOCS = Path(__file__).resolve().parent.parent / "data" / "advising_docs"
CATALOG_YEAR = "2026-2027"
TWO_YEAR_URL = "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19291"
THREE_YEAR_URL = "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19293"
FOUR_YEAR_URL = "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19292"
ONLINE_URL = "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19052"
ADVISING_URL = "https://ischool.sjsu.edu/bsisda-advising"
TITLES = {
    TWO_YEAR_URL: "Roadmap: Information Science and Data Analytics, BS (2-Year SJSU Online Program)",
    THREE_YEAR_URL: "Roadmap: Information Science and Data Analytics, BS (3-Year SJSU Online Program)",
    FOUR_YEAR_URL: "Roadmap: Information Science and Data Analytics, BS",
}
# The published 2-Year SJSU Online plan, semester by semester, in page order.
TWO_YEAR_PLAN = """Year 1
Fall Semester (15 units)
ISDA 101 - Online Learning: Tools and Strategies for Success 1 unit
ISDA 110 - Technology, Culture, and Society 3 unit(s)
ISDA 111 - Information and Data Science 3 unit(s)
ISDA 121 - Human Centered Cybersecurity 3 unit(s)
Major Elective - Lower Division 2 unit(s)
100W (Recommend LLD 100WB) 3 unit(s) (WID)
Spring Semester (15 units)
GE Upper Division Area 2/5 3 unit(s)
ISDA 120 - Information Security 3 unit(s)
ISDA 130 - User Centered Interface Architecture 3 unit(s)
ISDA 140 - Big Data Analytics and Management 3 unit(s)
Major Elective - Upper or Lower Division 3 unit(s)
Year 2
Fall Semester (15 units)
GE Upper Division Area 3 3 unit(s)
ISDA 100 - Technical Writing for Information 3 unit(s)
ISDA 131 - Information Architecture 3 unit(s)
ISDA 141 - Information Visualization 3 unit(s)
Major Elective - Upper or Lower Division 3 unit(s)
Spring Semester (15 units)
GE Upper Division Area 4 3 unit(s)
ISDA 122 - Privacy, Security, Ethics 2 unit(s)
ISDA 123 - Information Assurance 2 unit(s)
ISDA 170 - The Emerging Future: Technology Issues and Trends 4 unit(s)
Major Elective - Upper or Lower Division 4 unit(s)"""
TWO_YEAR_QUESTIONS = [
    "I want to graduate in two years. How should I plan my courses?",
    "What is the 2-year plan for ISDA?",
    "Is there a roadmap for the ISDA program?",
    "Can I finish ISDA in 2 years? What classes do I take each semester?",
]
ISDA_ONLY_QUESTIONS = [
    "How should I plan my courses?",
    "What classes should I take each semester?",
]


@pytest.fixture(scope="module")
def corpus_processor(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    patch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path_factory.mktemp("isda-roadmap")))
    patch.setenv("HYBRID_RETRIEVAL", "true")
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    yield processor
    patch.undo()


@pytest.fixture(scope="module")
def roadmap_chunks():
    processor = DocumentProcessor(docs_path=str(DOCS))
    chunks = processor.split_documents(processor.load_documents())
    return [c for c in chunks if c.metadata.get("source_url") in TITLES]


def _snapshot(url):
    matches = []
    for path in sorted((DOCS / "txt").glob("web-*.txt")):
        meta = yaml.safe_load((DOCS / "meta" / f"{path.stem}.yml").read_text(encoding="utf-8"))
        if meta.get("source_url") == url:
            matches.append((path, path.read_text(encoding="utf-8"), meta))
    assert len(matches) == 1, url
    return matches[0]


def _is_two_year_plan(document):
    return (
        document.metadata.get("source_url") == TWO_YEAR_URL
        and TWO_YEAR_PLAN in document.page_content
    )


def test_roadmap_snapshots_are_registered_current_catalog_pages():
    manifest = json.loads((DOCS / "scrape" / "manifest.json").read_text(encoding="utf-8"))
    listed = {item["source_url"]: item for item in manifest["documents"]}
    seeds = (DOCS / "scrape" / "seed_urls.txt").read_text(encoding="utf-8")
    for url, title in TITLES.items():
        path, text, meta = _snapshot(url)
        digest = sha256(text.encode("utf-8")).hexdigest()
        assert path.stem == "web-" + sha256(url.encode("utf-8")).hexdigest()[:16]
        assert meta["doc_id"] == path.stem
        assert meta["title"] == title
        assert meta["programs"] == ["ISDA"]
        assert meta["catalog_year"] == CATALOG_YEAR
        assert meta["content_sha256"] == digest
        assert listed[url]["doc_id"] == path.stem
        assert listed[url]["content_sha256"] == digest
        assert url in seeds
        assert text.startswith(f"{CATALOG_YEAR} Academic Catalog\n{title}\nSource URL: {url}\n")
        assert text.endswith("Retrieved: 2026-11-20\n")
        assert "A minimum of 120 units is required for this degree." in text
    # Older catalog roadmaps (catoid=10, MyRoadmaps) are not ingested as current.
    assert "catoid=10&poid=9276" not in seeds


def test_two_year_snapshot_keeps_the_published_plan_and_notes():
    _path, text, _meta = _snapshot(TWO_YEAR_URL)
    assert TWO_YEAR_PLAN in text
    assert "Students must have completed 60+ units in order to enroll in GE UD courses." in text
    assert "Upper or Lower Division Electives (12 units)" in text


def test_two_year_plan_is_one_chunk_and_longer_plans_keep_each_year(roadmap_chunks):
    two_year = [c for c in roadmap_chunks if _is_two_year_plan(c)]
    assert len(two_year) == 1
    assert two_year[0].page_content.startswith("BS ISDA 2-year roadmap")

    for url, years in ((THREE_YEAR_URL, 3), (FOUR_YEAR_URL, 4)):
        _path, text, _meta = _snapshot(url)
        for year in range(1, years + 1):
            block = text.split(f"\nYear {year}\n", 1)[1].split("\n\n", 1)[0]
            assert "Fall Semester" in block and "Spring Semester" in block
            holders = [
                c for c in roadmap_chunks
                if c.metadata.get("source_url") == url
                and f"Year {year}\n{block}" in c.page_content
            ]
            assert len(holders) == 1, (url, year)


@pytest.mark.parametrize(
    ("question", "program", "expected"),
    [
        ("I want to graduate in two years. How should I plan my courses?", "isda", True),
        ("I want to graduate in two years. How should I plan my courses?", None, True),
        ("How should I plan my courses?", "isda", True),
        ("How should I plan my courses?", None, False),
        ("What is the MLIS 2-year plan?", None, False),
        ("I want to graduate in two years. How should I plan my courses?", "mlis", False),
        ("How do I graduate?", "isda", False),
        ("What are the ISDA core courses?", "isda", False),
    ],
)
def test_roadmap_question_detection(question, program, expected):
    assert is_roadmap_query(question, program) is expected


def test_roadmap_length_follows_the_question():
    assert roadmap_years_in_query("graduate in two years") == 2
    assert roadmap_years_in_query("the 3-year roadmap") == 3
    assert roadmap_years_in_query("a four year plan") == 4
    assert roadmap_years_in_query("How should I plan my courses?") == 2
    assert roadmap_years_in_query("I'm a freshman. How should I plan my courses?") == 4


@pytest.mark.parametrize("program", ["isda", None])
@pytest.mark.parametrize("question", TWO_YEAR_QUESTIONS)
def test_two_year_questions_retrieve_the_two_year_plan_first(
    corpus_processor, question, program
):
    hits = corpus_processor.retrieve_relevant_docs(
        question, k=DEFAULT_RAG_TOP_K, program=program
    )
    assert hits and _is_two_year_plan(hits[0])
    assert hits[0].metadata.get("catalog_year") == CATALOG_YEAR
    # The SJSU Online admission requirement the 2-Year plan assumes.
    assert any(
        hit.metadata.get("source_url") == ONLINE_URL
        and "at least 60 transferable semester" in hit.page_content
        for hit in hits
    )


@pytest.mark.parametrize("question", ISDA_ONLY_QUESTIONS)
def test_isda_planning_questions_retrieve_the_two_year_plan(corpus_processor, question):
    hits = corpus_processor.retrieve_relevant_docs(
        question, k=DEFAULT_RAG_TOP_K, program="isda"
    )
    assert hits and _is_two_year_plan(hits[0])


@pytest.mark.parametrize(
    ("question", "url", "years"),
    [
        ("What is the 3-year roadmap for ISDA?", THREE_YEAR_URL, 3),
        ("What is the 4-year plan for ISDA?", FOUR_YEAR_URL, 4),
    ],
)
def test_longer_plans_follow_the_years_named(corpus_processor, question, url, years):
    hits = corpus_processor.retrieve_relevant_docs(
        question, k=DEFAULT_RAG_TOP_K, program="isda"
    )
    head = hits[:years]
    assert all(hit.metadata.get("source_url") == url for hit in head)
    assert [re.search(r"^Year (\d)$", h.page_content, re.M).group(1) for h in head] == [
        str(year) for year in range(1, years + 1)
    ]


class _RoadmapAnswer:
    """List the semester plan only from roadmap lines present in the context."""

    def __init__(self):
        self.payload = None

    def invoke(self, payload):
        self.payload = payload
        context = payload["context"]
        assert TWO_YEAR_PLAN in context
        lines = [
            line for line in TWO_YEAR_PLAN.splitlines()
            if line.startswith(("Year", "Fall", "Spring", "ISDA"))
        ]
        answer = (
            "The 2026-2027 2-Year SJSU Online roadmap:\n"
            + "\n".join(f"- {line}" for line in lines)
            + "\nIt assumes at least 60 transferable units. Use it with the catalog "
            f"and confirm with [BSISDA Advising]({ADVISING_URL}). "
            f"[Roadmap]({TWO_YEAR_URL})"
        )
        return type("Reply", (), {"content": answer})()


@pytest.mark.parametrize("program", ["isda", None])
def test_chat_answers_with_the_semester_plan_and_advising_caveat(corpus_processor, program):
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = get_knowledge_base()
    bot.doc_processor = corpus_processor
    bot.chain = _RoadmapAnswer()
    result = bot.chat(TWO_YEAR_QUESTIONS[0], program=program)
    assert result["source"] == "rag"
    context = bot.chain.payload["context"]
    assert ROADMAP_PROMPT_NOTE in context
    assert "at least 60 transferable semester" in context
    response = result["response"]
    assert "- Year 1" in response and "- Year 2" in response
    for code in ("ISDA 101", "ISDA 110", "ISDA 111", "ISDA 121", "ISDA 170"):
        assert code in response
    # The advising link comes from the vetted roadmap note and survives the allowlist.
    assert f"({ADVISING_URL})" in response
    assert f"({TWO_YEAR_URL})" in response


def test_roadmap_note_frames_the_answer_without_new_facts():
    note = ROADMAP_PROMPT_NOTE
    assert "semester-by-semester sequence" in note
    assert "exactly as published" in note
    assert "do not restate the page's generic \"four years\" sentence" in note
    assert "do not add requirements the sources do not state" in note
    assert f"[BSISDA Advising]({ADVISING_URL})" in note


def test_non_planning_questions_get_no_roadmap_note(corpus_processor):
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = get_knowledge_base()
    bot.doc_processor = corpus_processor

    class _Echo:
        payload = None

        def invoke(self, payload):
            _Echo.payload = payload
            return type("Reply", (), {"content": "ISDA 131 lists ISDA 130."})()

    bot.chain = _Echo()
    bot.chat("What are the prerequisites for ISDA 131?", program="isda")
    assert ROADMAP_PROMPT_NOTE not in _Echo.payload["context"]
