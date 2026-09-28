"""Official undergraduate apply-to-graduate pages and their retrieval."""

import json
from hashlib import sha256
from pathlib import Path

import yaml

from langchain_core.documents import Document

from src.config import Config
from src.document_processor import GRADUATION_RETRIEVAL_HEADER, DocumentProcessor
from src.hybrid_retrieval import expand_graduation_application_query
from src.rag_chatbot import (
    DEFAULT_RAG_TOP_K,
    RAGAdvisingChatbot,
    graduation_deadline_note,
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
    "how do I graduate",
    "How do I apply to graduate from ISDA?",
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
    assert "Earned at least 85 units (90 units for some colleges), with no more than 70 units from community colleges." in text
    assert 'Locate and click the "Apply Button" in MySJSU.' in text
    assert "application fee" not in text.casefold()
    assert "no fee" not in text.casefold()
    assert meta["atomic_section"] == "Application Deadlines"
    assert meta["retrieval_header"] == GRADUATION_RETRIEVAL_HEADER


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
    assert "at least 85 units" in headed[0].page_content
    assert "Apply Button" in headed[0].page_content
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
            blob = "\n".join(hit.page_content for hit in hits)
            assert "Apply for graduation link" in blob
            for row in DEADLINE_ROWS:
                assert row in blob
            assert "at least 85 units" in blob
            assert "Apply Button" in blob
            assert any(
                "application-central.php" in (hit.metadata.get("source_url") or "")
                for hit in hits
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
    """Deadline 1 and Deadline 2 are not the application cutoff."""
    text = _deadline_chunk().page_content
    add_at = text.index(
        "no later than the add deadline of the term in which you wish to graduate"
    )
    priority_at = text.index("you will receive priority registration")
    allows_1 = text.index(
        "Deadline 1 allows two semesters with priority registration."
    )
    allows_2 = text.index(
        "Deadline 2 allows one semester with priority registration."
    )
    fall = text.index("Planned Graduation Term: Fall 2026.")
    spring = text.index("Planned Graduation Term: Spring 2027/ Summer 2027.")
    fall_2027 = text.index("Planned Graduation Term: Fall 2027.")
    assert add_at < allows_1 < allows_2 < fall < spring < fall_2027
    assert priority_at < fall
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
    assert "include every Planned Graduation Term row before other steps" in note
    assert "add deadline of the term in which you wish to graduate" in note
    assert "priority registration" in note
    assert "Do not describe Deadline 1 or Deadline 2 as the final cutoff." in note
    assert "does not replace these rows" in note
    assert graduation_deadline_note("ISDA 100 is a core course.") == ""

    prompt = RAGAdvisingChatbot.SYSTEM_PROMPT
    rules_3_and_4 = prompt.split("5. Keep answers", 1)[0]
    assert "3. Answer from whatever the documents do contain" in rules_3_and_4
    assert "4. Never invent course numbers" in rules_3_and_4
    assert "8. When sources from different catalog years disagree" in prompt
    assert "Deadline 1 allows two semesters" not in prompt
