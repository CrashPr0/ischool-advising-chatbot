"""MLIS core-course evidence stays distinct from other degree requirements."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.documents import Document

from src.rag_chatbot import RAGAdvisingChatbot


REQUIRED_URL = "https://ischool.sjsu.edu/mlis-required-courses"


def _doc(content, url="https://ischool.sjsu.edu/mlis-course-load", program="MLIS"):
    return Document(
        page_content=content,
        metadata={
            "source": "official.txt",
            "source_url": url,
            "programs": [program],
            "title": "Official advising source",
        },
    )


def _required_course_chunks():
    return [
        _doc(
            "MLIS students are required to have taken all core classes "
            "(INFO 203, 200, 202, 204) within their first 16 units in the "
            "program. INFO 203 (1 unit) must be taken in the first semester. "
            "The other core classes (200, 202, 204) are each 3 units.",
            REQUIRED_URL,
        ),
        _doc(
            "All new MLIS students must complete INFO 203: Online Learning "
            "Tools and Strategies for Success, a 1-unit CR/NC course, within "
            "the first four weeks of their first semester.",
            REQUIRED_URL,
        ),
        _doc(
            "INFO 285 Applied Research Methods. All students must complete "
            "INFO 285 (Research Methods) before graduating.",
            REQUIRED_URL,
        ),
        _doc(
            "A waiver, if granted, does not reduce the total units required "
            "for the MLIS degree.",
            REQUIRED_URL,
        ),
        _doc(
            "Students may use either INFO 289 e-Portfolio or INFO 299 Thesis "
            "as their culminating experience.",
            REQUIRED_URL,
        ),
    ]


def _bot(retrieved):
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = SimpleNamespace(find_answer=Mock(return_value=None))
    bot.doc_processor = SimpleNamespace(
        retrieve_relevant_docs=Mock(return_value=list(retrieved)),
        chunks_matching_source=Mock(return_value=_required_course_chunks()),
    )
    bot.chain = SimpleNamespace(
        invoke=Mock(return_value=SimpleNamespace(content="Mocked advising answer."))
    )
    return bot


@pytest.mark.parametrize("program", ["mlis", "both"])
def test_mlis_core_question_pins_required_course_evidence_and_distinction(program):
    bot = _bot([_doc("The MLIS degree requires completion of 43 units.")])

    result = bot.chat(
        "How many units is the MLIS degree and what are the required core courses?",
        program=program,
    )

    assert result["source"] == "rag"
    bot.doc_processor.chunks_matching_source.assert_called_once_with(
        "/mlis-required-courses"
    )
    context = bot.chain.invoke.call_args.args[0]["context"]
    assert f"Source URL: {REQUIRED_URL}" in context
    assert (
        "The core classes are INFO 203 (1 unit), INFO 200, INFO 202, and INFO 204"
        in context
    )
    assert "INFO 285 from the core" in context
    assert "culminating-experience alternatives" in context
    assert "Never describe INFO 285 or INFO 289/299 as core classes" in context


def test_isda_core_question_is_unchanged():
    retrieved = [_doc("The ISDA core list.", program="ISDA")]
    bot = _bot(retrieved)

    bot.chat("What are the required core courses?", program="isda")

    bot.doc_processor.chunks_matching_source.assert_not_called()
    context = bot.chain.invoke.call_args.args[0]["context"]
    assert "The ISDA core list." in context
    assert "MLIS core-course instruction" not in context
    assert REQUIRED_URL not in context


def test_unrelated_mlis_question_is_unchanged():
    bot = _bot([_doc("MLIS internship and career resources.")])

    bot.chat("Where can I find MLIS internship resources?", program="mlis")

    bot.doc_processor.chunks_matching_source.assert_not_called()
    context = bot.chain.invoke.call_args.args[0]["context"]
    assert "MLIS internship and career resources." in context
    assert "MLIS core-course instruction" not in context
    assert REQUIRED_URL not in context
