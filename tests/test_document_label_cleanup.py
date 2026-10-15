"""Internal context numbers must not appear as citations in student replies."""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rag_chatbot import RAGAdvisingChatbot, _strip_document_labels


@pytest.mark.parametrize(
    "label",
    [
        "(Source: Document 3)",
        "(Sources: Documents 1 and 3)",
        "(Document 2)",
        "[Document 4]",
        "(Source: Document 1, Document 2)",
        "Source: Document 5",
    ],
)
def test_internal_labels_are_removed(label):
    assert _strip_document_labels(f"INFO 200 is 3 units {label}.") == (
        "INFO 200 is 3 units."
    )


@pytest.mark.parametrize(
    "reply, expected",
    [
        (
            "For more details, you can refer to the course load information "
            "[here](https://ischool.sjsu.edu/mlis-course-load) (Source: Document 3).",
            "For more details, you can refer to the course load information "
            "[here](https://ischool.sjsu.edu/mlis-course-load).",
        ),
        (
            "For more detailed information, you can visit the "
            "[Application Central page](https://www.sjsu.edu/registrar/graduation/"
            "application-central.php) (Source: Document 1).",
            "For more detailed information, you can visit the "
            "[Application Central page](https://www.sjsu.edu/registrar/graduation/"
            "application-central.php).",
        ),
    ],
)
def test_live_replies_keep_the_markdown_citations(reply, expected):
    assert _strip_document_labels(reply) == expected


def test_removal_closes_gaps_and_preserves_line_breaks():
    reply = (
        "Read (Document 2) the requirements. Source: Document 5\n"
        "See [Document 4], then apply.\n"
        "Keep this. Sources: Documents 1 and 3"
    )
    assert _strip_document_labels(reply) == (
        "Read the requirements.\nSee, then apply.\nKeep this."
    )


def test_removal_preserves_nested_list_indentation_and_reply_whitespace():
    reply = (
        "\n  Graduation requirements:  \n"
        "   - Earned at least 85 units (Source: Document 1)  \n"
        "   - Submit  your application.  \n\n"
    )
    cleaned = _strip_document_labels(reply)
    assert cleaned == (
        "\n  Graduation requirements:  \n"
        "   - Earned at least 85 units\n"
        "   - Submit  your application.  \n\n"
    )
    assert "(Source: Document 1)" not in cleaned


@pytest.mark.parametrize(
    "reply",
    [
        "Please submit the document to the Graduate Studies office.",
        "Document 1 of your application must be signed.",
        "Submit (Document 1 of your application) to the office.",
        "Read [Document 1 of your application] carefully.",
        "Source: Document 5 of your application is required.",
        "INFO 200 is 3 units.  Submit your documents.",
    ],
)
def test_ordinary_prose_is_unchanged(reply):
    assert _strip_document_labels(reply) == reply


def test_links_and_urls_are_copied_unchanged_when_a_label_is_removed():
    citations = (
        "[Document 4](https://ischool.sjsu.edu/mlis-course-load) "
        "[See  (Document 2)](https://example.org/documents) "
        "https://example.org/(Document2)"
    )
    assert _strip_document_labels(f"{citations} (Source: Document 3).") == (
        f"{citations}."
    )


def test_system_prompt_forbids_context_document_numbers():
    assert (
        'Never refer to context documents by number (e.g. "Document 3"), '
        "since students cannot see those labels; cite with the Source URL link instead."
    ) in RAGAdvisingChatbot.SYSTEM_PROMPT.split("9. Cite", 1)[1]


def test_real_chat_path_removes_labels_and_keeps_link_filtering():
    url = "https://ischool.sjsu.edu/mlis-course-load"
    reply = (
        f"INFO 200 is 3 units. See [course load]({url}) (Source: Document 3). "
        "Also see [this page](https://invented.example/course-load)."
    )
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = SimpleNamespace(find_answer=lambda message: None)
    document = SimpleNamespace(
        page_content="INFO 200 is 3 units.",
        metadata={"source": "course-load.txt", "source_url": url},
    )
    bot.doc_processor = SimpleNamespace(
        retrieve_relevant_docs=lambda query, k=5: [document]
    )
    bot.chain = SimpleNamespace(invoke=lambda payload: SimpleNamespace(content=reply))
    history = []

    result = bot.chat("What is the MLIS course load?", history=history)

    assert result["response"] == f"INFO 200 is 3 units. See [course load]({url})."
    assert result["source"] == "rag"
    assert history[-1]["response"] == result["response"]
