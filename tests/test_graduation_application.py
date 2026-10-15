"""Official undergraduate apply-to-graduate pages and their retrieval."""

import json
from hashlib import sha256
from pathlib import Path

import yaml
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from langchain_core.documents import Document

from src.config import Config
from src.document_processor import GRADUATION_RETRIEVAL_HEADER, DocumentProcessor
from src.hybrid_retrieval import expand_graduation_application_query
from datetime import date

from src.rag_chatbot import (
    DEFAULT_RAG_TOP_K,
    RAGAdvisingChatbot,
    _graduation_prompt_corpus,
    _program_prompt_context,
    graduation_deadline_note,
    mark_passed_graduation_deadlines,
    order_graduation_prompt_docs,
)

DOCS = Path(__file__).resolve().parents[1] / "data" / "advising_docs"
RETRIEVED = "2026-09-27"

PAGES = {
    "https://www.sjsu.edu/registrar/graduation/application-central.php": "web-acb08947e7ec6c88",
    "https://www.sjsu.edu/registrar/graduation/index.php": "web-99089fe13151591e",
    "https://www.sjsu.edu/registrar/graduation/graduation-checklist.php": "web-afae1df6c67e23a7",
    "https://www.sjsu.edu/registrar/graduation/faq.php": "web-2633bf5e8b922f89",
    "https://www.sjsu.edu/registrar/graduation/graduation-date-changes.php": "web-10353c380b01f255",
    "https://www.sjsu.edu/registrar/graduation/diploma.php": "web-2696b15a85b29fda",
    "https://www.sjsu.edu/registrar/graduation/myprogress-graduation-status.php": "web-652038e1fe815a13",
    "https://www.sjsu.edu/registrar/graduation/evaluators.php": "web-f86ac5362eaae233",
    "https://www.sjsu.edu/registrar/docs/GradAppOnlineTutorial.pdf": "web-afd4ffd17369beaa",
    "https://catalog.sjsu.edu/content.php?catoid=23&navoid=8473": "web-40a52cbd73c9bde9",
    "https://www.sjsu.edu/commencement/ceremonies/index.php": "web-7cb352458448ad58",
    "https://www.sjsu.edu/commencement/participation/tickets.php": "web-5c118118b448cc8a",
    "https://www.sjsu.edu/commencement/participation/grad-vs-comm.php": "web-90bf6cc5164dbaf9",
}

FALL_2026_PAGES = {
    "https://www.sjsu.edu/registrar/calendar/fall-2026.php": ("web-8ea16420cf98e932", ["ISDA"]),
    "https://www.sjsu.edu/gape/deadlines/fall-graduation.php": ("web-5125cd4de9efc5f1", ["MLIS"]),
    "https://ischool.sjsu.edu/application-award-graduate-degree": ("web-f6477eb77f755c3e", ["MLIS"]),
    "https://ischool.sjsu.edu/candidacy-approval-form-information": ("web-f27df85d156e0e80", ["MLIS"]),
}

# The iSchool graduation page is master's candidacy, not the ISDA bachelor's.
NOT_INGESTED = "https://ischool.sjsu.edu/graduation"

DEADLINE_ROWS = (
    "Planned Graduation Term: Fall 2026. Deadline 1: October 10, 2025. Deadline 2: March 20, 2026.",
    "Planned Graduation Term: Spring 2027/ Summer 2027. Deadline 1: March 20, 2026. Deadline 2: October 9, 2026.",
    "Planned Graduation Term: Fall 2027. Deadline 1: October 9, 2026. Deadline 2: March 19, 2027.",
)

APPLY_QUESTIONS = (
    "How do I apply to graduate?",
    "graduation application deadline",
    "when should I apply for graduation",
    "When do I need to apply for graduation?",
    "how do I graduate",
    "How do I apply to graduate from ISDA?",
    "What is the deadline to graduate in Fall 2026?",
)


def _snapshot(url):
    matches = []
    for path in sorted((DOCS / "txt").glob("web-*.txt")):
        meta = yaml.safe_load(
            (DOCS / "meta" / f"{path.stem}.yml").read_text(encoding="utf-8")
        )
        if meta.get("source_url") == url:
            matches.append((path, path.read_text(encoding="utf-8"), meta))
    assert len(matches) == 1
    return matches[0]


def test_graduation_query_expansion_stays_on_apply_to_graduate_questions():
    for question in APPLY_QUESTIONS:
        expanded = expand_graduation_application_query(question)
        assert expanded != question
        assert "graduation application deadline" in expanded
    unchanged = (
        "What are the ISDA core courses?",
        "What classes are required for the ISDA major?",
        "What electives can I take for the ISDA major?",
        "What are the prerequisites for ISDA 150?",
        "What graduate courses can I take as electives?",
        "What are the graduation requirements?",
    )
    for question in unchanged:
        assert expand_graduation_application_query(question) == question


def test_official_graduation_pages_record_source_url_and_retrieval_date():
    manifest = json.loads(
        (DOCS / "scrape" / "manifest.json").read_text(encoding="utf-8")
    )
    listed = {item["source_url"]: item for item in manifest["documents"]}
    seeds = (DOCS / "scrape" / "seed_urls.txt").read_text(encoding="utf-8")
    assert NOT_INGESTED not in seeds
    assert NOT_INGESTED not in listed

    for url, doc_id in PAGES.items():
        path, text, meta = _snapshot(url)
        digest = sha256(text.encode("utf-8")).hexdigest()
        assert path.stem == doc_id
        assert url in seeds
        assert listed[url]["doc_id"] == doc_id
        assert listed[url]["content_sha256"] == digest
        assert meta["content_sha256"] == digest
        assert meta["programs"] == ["ISDA"]
        assert meta["retrieved_at"].startswith(RETRIEVED)
        assert f"Source URL: {url}" in text
        assert f"Retrieved: {RETRIEVED}" in text
        assert text.splitlines()[-1] == f"Retrieved: {RETRIEVED}"


def test_application_central_keeps_published_terms_and_does_not_invent_a_fee():
    _path, text, meta = _snapshot(
        "https://www.sjsu.edu/registrar/graduation/application-central.php"
    )
    for row in DEADLINE_ROWS:
        assert row in text
    assert (
        "- Earned at least 85 units (90 units for some colleges), with no more than 70 units from community colleges"
        in text
    )
    assert text.count("Earned at least 85 units") == 1
    assert text.index("Eligibility for Graduation") < text.index("Application Deadlines")
    assert text.index("Planned Graduation Term: Fall 2026.") < text.index(
        "Deadline 1 allows two semesters with priority registration."
    )
    assert 'Locate and click the "Apply Button" in MySJSU.' in text
    assert "application fee" not in text.casefold()
    assert "no fee" not in text.casefold()
    assert meta["atomic_section"] == "Application Deadlines"
    assert meta["retrieval_header"] == GRADUATION_RETRIEVAL_HEADER


def test_fall_2026_undergraduate_and_mlis_deadline_sources_are_ingested():
    manifest = json.loads(
        (DOCS / "scrape" / "manifest.json").read_text(encoding="utf-8")
    )
    listed = {item["source_url"]: item for item in manifest["documents"]}
    seeds = (DOCS / "scrape" / "seed_urls.txt").read_text(encoding="utf-8")

    for url, (doc_id, programs) in FALL_2026_PAGES.items():
        path, text, meta = _snapshot(url)
        digest = sha256(text.encode("utf-8")).hexdigest()
        assert path.stem == doc_id
        assert url in seeds
        assert listed[url]["doc_id"] == doc_id
        assert listed[url]["content_sha256"] == digest
        assert meta["content_sha256"] == digest
        assert meta["programs"] == programs
        assert meta["retrieved_at"].startswith("2026-10-15")
        assert f"Source URL: {url}" in text
        assert text.splitlines()[-1] == "Retrieved: 2026-10-15"

    _path, calendar, calendar_meta = _snapshot(
        "https://www.sjsu.edu/registrar/calendar/fall-2026.php"
    )
    assert "Tue, Sep. 15" in calendar
    assert 'Last Day to Add/Drop Classes via MySJSU (ends at 11:59 pm)' in calendar
    assert calendar_meta["atomic_section"] == "Important Dates and Deadlines"

    _path, gape, gape_meta = _snapshot(
        "https://www.sjsu.edu/gape/deadlines/fall-graduation.php"
    )
    assert "Deadline: 04/01/2026\nRequirement: Candidacy Form Submission to GAPE" in gape
    assert "Deadline: 09/01/2026\nRequirement: Graduation Application (First-Time Applicants)" in gape
    assert "Deadline: 09/01/2026\nRequirement: Graduation Date Change Request" in gape
    assert "12/16/2026: Degree Conferral" in gape
    assert gape_meta["atomic_section"] == "Fall Graduation Deadlines"

    _path, award, _meta = _snapshot(
        "https://ischool.sjsu.edu/application-award-graduate-degree"
    )
    assert "Due date for December graduation: September 1" in award
    assert "after your candidacy has been approved" in award

    _path, candidacy, _meta = _snapshot(
        "https://ischool.sjsu.edu/candidacy-approval-form-information"
    )
    assert "For December graduation, submit to iSchool by March 25." in candidacy


def test_related_pages_keep_published_process_details():
    _path, tutorial, tutorial_meta = _snapshot(
        "https://www.sjsu.edu/registrar/docs/GradAppOnlineTutorial.pdf"
    )
    assert "Click the Apply for graduation link." in tutorial
    assert "Select Expected Graduation Term and click the button." in tutorial
    assert tutorial_meta["atomic_section"] == "Online Graduation Application Overview"

    _path, catalog, catalog_meta = _snapshot(
        "https://catalog.sjsu.edu/content.php?catoid=23&navoid=8473"
    )
    assert catalog_meta["catalog_year"] == "2026-2027"
    assert "approximately two semesters before their anticipated graduation term" in catalog
    assert "Participation in commencement ceremonies does not constitute conferral of a degree." in catalog

    _path, diploma, _meta = _snapshot(
        "https://www.sjsu.edu/registrar/graduation/diploma.php"
    )
    assert "There is a $10 fee for each diploma." in diploma
    assert "application fee" not in diploma.casefold()

    _path, evaluators, evaluators_meta = _snapshot(
        "https://www.sjsu.edu/registrar/graduation/evaluators.php"
    )
    assert "Major: Information Science and Data Analytic" in evaluators
    assert "College: College of Information, Data & Society" in evaluators
    assert "retrieval_header" not in evaluators_meta

    _path, ceremonies, _meta = _snapshot(
        "https://www.sjsu.edu/commencement/ceremonies/index.php"
    )
    assert (
        "College of Information, Data and Society: Thursday, December 17, 2026 at 7 p.m., PT"
        in ceremonies
    )

    _path, tickets, _meta = _snapshot(
        "https://www.sjsu.edu/commencement/participation/tickets.php"
    )
    assert (
        "in October for graduation at the end of the fall semester and in March "
        "for graduation at the end of the spring semester."
    ) in tickets
    assert "November 20:" in tickets
    assert "November 20, 2026" not in tickets
    assert "$20 each" in tickets


def test_retrieval_header_is_only_on_the_atomic_graduation_chunk():
    processor = DocumentProcessor(str(DOCS))
    loaded = {doc.metadata.get("source_url"): doc for doc in processor.load_documents()}
    url = "https://www.sjsu.edu/registrar/graduation/application-central.php"
    chunks = processor.split_documents([loaded[url]])
    headed = [
        chunk
        for chunk in chunks
        if chunk.page_content.startswith(GRADUATION_RETRIEVAL_HEADER)
    ]
    assert len(headed) == 1
    assert "Application Deadlines" in headed[0].page_content
    for row in DEADLINE_ROWS:
        assert row in headed[0].page_content
    body = headed[0].page_content.split(GRADUATION_RETRIEVAL_HEADER, 1)[-1]
    assert body.index("Planned Graduation Term: Fall 2026.") < body.index(
        "Deadline 1 allows two semesters with priority registration."
    )
    assert any(
        "Eligibility for Graduation" in chunk.page_content and "85 units" in chunk.page_content
        for chunk in chunks
    )
    assert sum(chunk.page_content.count(GRADUATION_RETRIEVAL_HEADER) for chunk in chunks) == 1


def test_apply_to_graduate_questions_retrieve_steps_and_term_deadlines(
    monkeypatch, tmp_path
):
    assert DEFAULT_RAG_TOP_K == 8
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("HYBRID_RETRIEVAL", "true")
    processor = DocumentProcessor(str(DOCS))
    assert processor.process_and_index() is not None

    for rerank in ("false", "true"):
        monkeypatch.setenv("CATALOG_RECENCY_RERANK", rerank)
        for question in APPLY_QUESTIONS:
            hits = processor.retrieve_relevant_docs(question, k=DEFAULT_RAG_TOP_K)
            # With both programs indexed, tutorial steps can miss the top-k.
            # Eligibility can also miss. Login is a different tutorial
            # chunk from the overview. The prompt pins both.
            corpus = _graduation_prompt_corpus(processor, question)
            prompt_docs = order_graduation_prompt_docs(question, hits, corpus)
            prompt = "\n".join(doc.page_content for doc in prompt_docs)
            assert "at least 85 units" in prompt
            assert "add deadline" in prompt.casefold()
            assert "priority registration" in prompt.casefold()
            assert "final graduation application deadline is the Fall 2026 add deadline: September 15, 2026" in prompt
            assert "September 1, 2026 as the graduation application deadline" in prompt
            assert "Apply to graduate with GAPE only after candidacy has been approved" in prompt
            assert "submit the candidacy form to the iSchool by March 25" in prompt
            assert "Apply for graduation link" in prompt
            assert "Login to MySJSU" in prompt
            assert "Verify graduation data" in prompt
            assert "Update Diploma Name" not in prompt
            for row in DEADLINE_ROWS:
                assert row in prompt
            assert any(
                "application-central.php" in (doc.metadata.get("source_url") or "")
                for doc in prompt_docs
            )

    core = processor.retrieve_relevant_docs(
        "What are the ISDA core courses?", k=DEFAULT_RAG_TOP_K
    )
    core_blob = "\n".join(hit.page_content for hit in core)
    assert "ISDA 100 - Technical Writing for Information (3)" in core_blob
    assert "Core Courses (36 units)" in core_blob

    electives = processor.retrieve_relevant_docs(
        "What electives can I take for the ISDA major?", k=DEFAULT_RAG_TOP_K
    )
    electives_blob = "\n".join(hit.page_content for hit in electives)
    assert "ISDA 80, 81, 150, 160, 180, and 181" in electives_blob
    assert "ISDA 150 - Topics in Information Science and Data Analytics" in electives_blob

    prerequisites = processor.retrieve_relevant_docs(
        "What are the prerequisites for ISDA 150?", k=DEFAULT_RAG_TOP_K
    )
    assert any(
        "Prerequisite(s): ISDA 110." in hit.page_content
        and hit.metadata.get("catalog_year") == "2026-2027"
        for hit in prerequisites
    )


def _deadline_chunk():
    processor = DocumentProcessor(str(DOCS))
    loaded = {
        doc.metadata.get("source_url"): doc for doc in processor.load_documents()
    }
    url = "https://www.sjsu.edu/registrar/graduation/application-central.php"
    chunks = processor.split_documents([loaded[url]])
    headed = [
        chunk
        for chunk in chunks
        if "Planned Graduation Term: Fall 2027." in chunk.page_content
    ]
    assert len(headed) == 1
    return headed[0]


def test_deadline_rows_follow_the_published_priority_registration_wording():
    """Deadline 1 and Deadline 2 are priority registration, after the rows."""
    text = _deadline_chunk().page_content
    body = text.split(GRADUATION_RETRIEVAL_HEADER, 1)[-1]
    add_at = body.index(
        "no later than the add deadline of the term in which you wish to graduate"
    )
    priority_at = body.index("you will receive priority registration")
    fall = body.index("Planned Graduation Term: Fall 2026.")
    spring = body.index("Planned Graduation Term: Spring 2027/ Summer 2027.")
    fall_2027 = body.index("Planned Graduation Term: Fall 2027.")
    allows_1 = body.index(
        "Deadline 1 allows two semesters with priority registration."
    )
    allows_2 = body.index(
        "Deadline 2 allows one semester with priority registration."
    )
    assert add_at < priority_at < fall < spring < fall_2027 < allows_1 < allows_2
    assert "the graduation application deadline" not in text
    assert text.startswith(GRADUATION_RETRIEVAL_HEADER)


def test_catalog_and_tutorial_are_not_labeled_with_the_term_table():
    processor = DocumentProcessor(str(DOCS))
    loaded = {
        doc.metadata.get("source_url"): doc for doc in processor.load_documents()
    }
    catalog_url = "https://catalog.sjsu.edu/content.php?catoid=23&navoid=8473"
    tutorial_url = "https://www.sjsu.edu/registrar/docs/GradAppOnlineTutorial.pdf"
    for url in (catalog_url, tutorial_url):
        chunks = processor.split_documents([loaded[url]])
        for chunk in chunks:
            assert "Deadline 1 allows two semesters" not in chunk.page_content
            assert not chunk.page_content.startswith(GRADUATION_RETRIEVAL_HEADER)
    tutorial_chunks = processor.split_documents([loaded[tutorial_url]])
    apply_steps = [
        chunk
        for chunk in tutorial_chunks
        if "Apply for graduation link" in chunk.page_content
    ]
    assert len(apply_steps) == 1
    assert len(apply_steps[0].page_content) < 2000
    assert "Update Diploma Name" not in apply_steps[0].page_content


def test_prompt_leads_with_every_term_row_and_leaves_rules_3_4_and_8():
    table = _deadline_chunk()
    catalog = Document(
        page_content=(
            "Students should normally apply for graduation approximately two "
            "semesters before their anticipated graduation term."
        ),
        metadata={
            "source": "catalog.txt",
            "catalog_year": "2026-2027",
            "source_url": "https://catalog.sjsu.edu/content.php?catoid=23&navoid=8473",
        },
    )
    tutorial = Document(
        page_content="Click the Apply for graduation link.",
        metadata={"source": "tutorial.txt"},
    )
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    context = bot._build_context(
        "How do I apply to graduate?", [catalog, tutorial, table]
    )
    assert context.index("Document 1") < context.index(
        "Planned Graduation Term: Fall 2026."
    )
    assert context.index("Planned Graduation Term: Fall 2027.") < context.index(
        "approximately two semesters"
    )
    note = graduation_deadline_note(context)
    assert "List Planned Graduation Term rows from the sources" in note
    assert "Do not present application deadlines that are before today as upcoming" in note
    assert "add deadline of the term in which you wish to graduate" in note
    assert "priority registration" in note
    assert "not that cutoff" in note
    assert "does not replace those rows" in note
    assert "before any MySJSU tutorial steps" in note
    assert graduation_deadline_note("ISDA 100 is a core course.") == ""
    future = (
        "You must apply no later than the add deadline of the term. "
        "If you apply by the dates shown below, you will receive priority registration. "
        "Planned Graduation Term: Winter 2031. Deadline 1: January 1, 2031."
    )
    assert graduation_deadline_note(future)
    assert "Fall 2027" not in graduation_deadline_note(future)

    prompt = RAGAdvisingChatbot.SYSTEM_PROMPT
    rules_3_and_4 = prompt.split("5. Keep answers", 1)[0]
    assert "3. Answer from whatever the documents do contain" in rules_3_and_4
    assert "4. Never invent course numbers" in rules_3_and_4
    assert "8. When sources from different catalog years disagree" in prompt
    assert "Deadline 1 allows two semesters" not in prompt


CENTRAL = "https://www.sjsu.edu/registrar/graduation/application-central.php"
TUTORIAL_URL = "https://www.sjsu.edu/registrar/docs/GradAppOnlineTutorial.pdf"


def _central_pieces():
    processor = DocumentProcessor(str(DOCS))
    loaded = {
        doc.metadata.get("source_url"): doc for doc in processor.load_documents()
    }
    chunks = processor.split_documents([loaded[CENTRAL]])
    eligibility = next(
        chunk
        for chunk in chunks
        if "Eligibility for Graduation" in chunk.page_content and "85 units" in chunk.page_content
    )
    table = next(
        chunk
        for chunk in chunks
        if "Planned Graduation Term: Fall 2027." in chunk.page_content
    )
    return eligibility, table


def test_apply_to_graduate_prompt_pins_application_central_when_ranking_misses_it():
    """A top-k that is only the tutorial still gets eligibility and every term row."""
    eligibility, table = _central_pieces()
    tutorial = Document(
        page_content=(
            "How to apply to graduate in MySJSU\n"
            "3. Review/Update your diploma name. Update Diploma Name before deadlines.\n"
            "7. Click the Apply for graduation link.\n"
            "9. Select Expected Graduation Term and click the button.\n"
            "Source URL: " + TUTORIAL_URL + "\n"
            "Retrieved: 2026-09-27\n"
        ),
        metadata={"source": "tutorial.txt", "source_url": TUTORIAL_URL},
    )
    ordered = order_graduation_prompt_docs(
        "How do I apply to graduate?",
        [tutorial],
        [eligibility, table],
    )
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    context = bot._build_context("How do I apply to graduate?", ordered)
    assert context.index("at least 85 units") < context.index(
        "Planned Graduation Term: Fall 2026."
    )
    assert context.index("Planned Graduation Term: Fall 2027.") < context.index(
        "Apply for graduation link"
    )
    assert "Update Diploma Name" not in context
    assert "add deadline" in context.casefold()
    assert "priority registration" in context.casefold()
    for row in DEADLINE_ROWS:
        assert row in context
    note = graduation_deadline_note(context)
    assert "at least 85 units" in note
    assert "priority registration" in note
    assert "List Planned Graduation Term rows from the sources" in note
    assert "Do not present application deadlines that are before today as upcoming" in note


def test_pinned_context_keeps_the_short_mysjsu_navigation_path():
    """The overview chunk has no login line. The pinned path still includes it."""
    eligibility, table = _central_pieces()
    processor = DocumentProcessor(str(DOCS))
    loaded = {
        doc.metadata.get("source_url"): doc for doc in processor.load_documents()
    }
    tutorial_chunks = processor.split_documents([loaded[TUTORIAL_URL]])
    ranked = [
        chunk
        for chunk in tutorial_chunks
        if "Apply for graduation link" in chunk.page_content
    ]
    assert len(ranked) == 1
    assert "Login to MySJSU" not in ranked[0].page_content
    assert "Update Diploma Name" not in ranked[0].page_content
    ordered = order_graduation_prompt_docs(
        "How do I apply to graduate?",
        ranked,
        [eligibility, table, *tutorial_chunks],
    )
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    context = bot._build_context("How do I apply to graduate?", ordered)
    nav = context[context.index("Planned Graduation Term: Fall 2027.") :]
    # The login sentence already says "My Academics", so that phrase is not
    # a later step. Order the lines that first appear after the term rows.
    steps = (
        "Login to MySJSU",
        "Click the Student Center link",
        "Apply for graduation link",
        "Expected Graduation Term",
        "Verify graduation data",
        "successfully applied",
    )
    position = -1
    for phrase in steps:
        found = nav.index(phrase)
        assert found > position
        position = found
    after_center = nav[nav.index("Click the Student Center link") :]
    assert "On the Student Center page, click the My Academics link" in after_center
    assert "Update Diploma Name" not in context
    assert "Update Diploma Address" not in context
    assert "Diploma Address" not in context
    tutorial = ordered[-1]
    assert "Login to MySJSU" in tutorial.page_content
    assert len(tutorial.page_content) < 1600
    assert context.index("at least 85 units") < context.index("Login to MySJSU")


def test_tutorial_ranked_first_is_trimmed_and_placed_after_the_table():
    eligibility, table = _central_pieces()
    tutorial = Document(
        page_content="Click the Apply for graduation link.\nUpdate Diploma Name stays out.",
        metadata={"source": "tutorial.txt", "source_url": TUTORIAL_URL},
    )
    ordered = order_graduation_prompt_docs(
        "How do I apply to graduate?",
        [tutorial, table],
        [eligibility],
    )
    assert ordered[0].page_content == eligibility.page_content
    assert ordered[1].page_content == table.page_content
    assert ordered[-1] is not tutorial
    assert "Apply for graduation link" in ordered[-1].page_content
    assert "Update Diploma Name" not in ordered[-1].page_content


def test_pinning_does_not_run_for_course_questions_and_accepts_a_new_term():
    eligibility, table = _central_pieces()
    untouched = [Document(page_content="ISDA 100 is required.", metadata={"source": "core.txt"})]
    assert (
        order_graduation_prompt_docs(
            "What are the ISDA core courses?", untouched, [eligibility, table]
        )
        == untouched
    )
    winter = Document(
        page_content=(
            "You must apply for graduation no later than the add deadline of the term "
            "in which you wish to graduate. If you apply by the dates shown below, "
            "you will receive priority registration.\n"
            "Planned Graduation Term: Winter 2031. Deadline 1: January 2, 2031. "
            "Deadline 2: May 2, 2031."
        ),
        metadata={"source": "future.txt", "source_url": CENTRAL},
    )
    ordered = order_graduation_prompt_docs(
        "how do I graduate",
        [Document(page_content="Click the Apply for graduation link.", metadata={})],
        [winter],
    )
    assert ordered[0].page_content == winter.page_content
    assert "Winter 2031" in ordered[0].page_content


def test_graduation_deadline_note_marks_past_dates_for_fixed_today():
    """Past Deadline 1/2 dates must not be framed as upcoming."""
    table = _deadline_chunk().page_content
    note = graduation_deadline_note(table, today=date(2026, 10, 13))
    assert "Today is 2026-10-13." in note
    assert "Do not present application deadlines that are before today as upcoming" in note
    assert "October 10, 2025" in note
    assert "March 20, 2026" in note
    assert "already passed" in note
    # Oct 9, 2026 is also past as of Oct 13; March 19, 2027 remains open.
    assert "October 9, 2026" in note
    assert "March 19, 2027" in note
    assert "Prefer still-open deadlines from the sources" in note
    assert "upcoming" in note  # only in the "do not present ... as upcoming" sentence
    assert "invent" in note.casefold()


def test_graduation_deadline_note_when_all_listed_deadlines_passed():
    context = (
        "You must apply for graduation no later than the add deadline of the term "
        "in which you wish to graduate. If you apply by the dates shown below, "
        "you will receive priority registration.\n"
        "Planned Graduation Term: Fall 2026. Deadline 1: October 10, 2025. "
        "Deadline 2: March 20, 2026."
    )
    note = graduation_deadline_note(context, today=date(2026, 10, 13))
    assert "Every listed Deadline 1/Deadline 2 date in the sources has already passed" in note
    assert "Application Central" in note
    assert "do not invent replacement dates" in note.casefold()
    assert "Prefer still-open" not in note


def test_both_or_unspecified_graduation_prompt_requires_both_programs():
    guidance = _program_prompt_context("both", "When do I need to apply for graduation?")
    assert "Selected Program: BOTH" in guidance
    assert "BS ISDA" in guidance
    assert "MLIS" in guidance
    assert "Never silently answer for only one program" in guidance
    assert _program_prompt_context(None, "When do I need to apply for graduation?") == guidance
    # Non-graduation both keeps prior behavior (no selected-program banner).
    assert _program_prompt_context("both", "What are the master's core courses?") == ""
    assert "Selected Program: ISDA" in _program_prompt_context(
        "isda", "When do I need to apply for graduation?"
    )


def test_graduation_corpus_respects_explicit_scope_and_defaults_to_both(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path / "scope-chroma"))
    processor = DocumentProcessor(str(DOCS))
    assert processor.process_and_index() is not None
    question = "What is the deadline to graduate in Fall 2026?"

    def urls(program):
        return {
            doc.metadata.get("source_url")
            for doc in _graduation_prompt_corpus(processor, question, program=program)
        }

    assert set(FALL_2026_PAGES) <= urls(None)
    assert "https://www.sjsu.edu/registrar/calendar/fall-2026.php" in urls("isda")
    assert not any("/gape/" in url for url in urls("isda"))
    assert "https://www.sjsu.edu/gape/deadlines/fall-graduation.php" in urls("mlis")
    assert not any("/registrar/" in url for url in urls("mlis"))


def test_passed_labeling_includes_actual_fall_2026_program_deadlines():
    context = (
        "For a BS ISDA undergraduate, the final graduation application deadline "
        "is September 15, 2026. For MLIS, the graduation application deadline for "
        "first-time applicants is September 1, 2026."
    )
    reply = "BS ISDA: September 15, 2026. MLIS: September 1, 2026."
    assert mark_passed_graduation_deadlines(
        reply, context, today=date(2026, 10, 15)
    ) == (
        "BS ISDA: September 15, 2026 (already passed). "
        "MLIS: September 1, 2026 (already passed)."
    )


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("- Deadline 1: October 9, 2026 (allows two semesters)",
         "- Deadline 1: October 9, 2026 (already passed) (allows two semesters)"),
        ("**Deadline 2:** October 9, 2026",
         "**Deadline 2:** October 9, 2026 (already passed)"),
        ("| Fall 2027 | October 9, 2026 | March 19, 2027 |",
         "| Fall 2027 | October 9, 2026 (already passed) | March 19, 2027 |"),
        ("Deadline 2: March 19, 2027", "Deadline 2: March 19, 2027"),
        ("Apply through MySJSU.", "Apply through MySJSU."),
        ("October 14, 2026", "October 14, 2026"),
        ("Deadline 1: October 9, 2026 (already passed)",
         "Deadline 1: October 9, 2026 (already passed)"),
        ("October 8, 2026", "October 8, 2026"),
    ],
)
def test_reply_marks_only_past_source_deadline_dates(reply, expected):
    context = "\n".join(DEADLINE_ROWS) + " Deadline 1: October 14, 2026."
    assert mark_passed_graduation_deadlines(
        reply, context, today=date(2026, 10, 14)
    ) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "Deadline 1: October 9, 2026 (this deadline has passed)",
        "Deadline 1: October 9, 2026 - this date has already passed.",
        "Deadline 1: October 9, 2026, which was already closed.",
    ],
)
def test_reply_that_already_says_passed_is_not_double_labeled(reply):
    context = "\n".join(DEADLINE_ROWS)
    assert mark_passed_graduation_deadlines(
        reply, context, today=date(2026, 10, 14)
    ) == reply


def test_passed_wording_for_a_later_date_does_not_suppress_label():
    context = "\n".join(DEADLINE_ROWS)
    reply = "October 9, 2026 or March 19, 2027 (has passed)"
    assert mark_passed_graduation_deadlines(
        reply, context, today=date(2026, 10, 14)
    ).startswith("October 9, 2026 (already passed) or")


def test_passed_wording_on_next_line_does_not_suppress_label():
    context = "\n".join(DEADLINE_ROWS)
    reply = "Deadline 1: October 9, 2026\nThat window has passed."
    assert "October 9, 2026 (already passed)\n" in mark_passed_graduation_deadlines(
        reply, context, today=date(2026, 10, 14)
    )


def test_reply_without_source_deadline_dates_is_unchanged():
    reply = "Orientation is October 9, 2026."
    assert mark_passed_graduation_deadlines(
        reply, "Orientation is October 9, 2026.", today=date(2026, 10, 14)
    ) == reply


@pytest.mark.parametrize("program", ["isda", "both"])
def test_chat_corrects_model_ignoring_past_deadline_note(monkeypatch, program):
    from src import rag_chatbot

    monkeypatch.setattr(rag_chatbot, "_graduation_today", lambda: date(2026, 10, 14))
    table = _deadline_chunk()
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = SimpleNamespace(find_answer=Mock(return_value=None))
    bot.doc_processor = SimpleNamespace(
        retrieve_relevant_docs=Mock(return_value=[table])
    )
    # Reproduce the live response even though the prompt says this date passed.
    reply = (
        "Here are the upcoming application deadlines:\n"
        "- Planned Graduation Term: Fall 2027\n"
        "  - Deadline 1: October 9, 2026 (allows two semesters with priority registration)\n"
        "  - Deadline 2: March 19, 2027"
    )
    bot.chain = SimpleNamespace(invoke=Mock(return_value=SimpleNamespace(content=reply)))
    history = []
    result = bot.chat("When do I need to apply for graduation?", history, program)
    assert result["source"] == "rag"
    assert result["response"] == reply.replace(
        "October 9, 2026", "October 9, 2026 (already passed)"
    )
    assert history[0]["response"] == result["response"]
    payload = bot.chain.invoke.call_args.args[0]
    assert "Today is 2026-10-14" in payload["graduation_note"]


def test_graduation_today_uses_los_angeles_at_utc_midnight(monkeypatch):
    from datetime import datetime, timezone
    from src import rag_chatbot

    utc_now = datetime(2026, 10, 10, 1, tzinfo=timezone.utc)
    clock = Mock()
    clock.now.side_effect = lambda tz: utc_now.astimezone(tz)
    monkeypatch.setattr(rag_chatbot, "datetime", clock)
    # October 9 is still open in California, even though UTC is October 10.
    assert rag_chatbot._graduation_today() == date(2026, 10, 9)
    reply = "Deadline 1: October 9, 2026"
    assert mark_passed_graduation_deadlines(reply, reply) == reply
    assert "Today is 2026-10-09" in graduation_deadline_note(_deadline_chunk().page_content)
