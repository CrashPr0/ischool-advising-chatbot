"""Regression tests for pointers left behind by stripped model links."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rag_chatbot import RAGAdvisingChatbot, _restrict_reply_links  # noqa: E402

BAD_URL = "https://example.invalid/internships"
ALLOWED_URL = "https://ischool.sjsu.edu/internships"
QUESTION = "How do I apply for an MLIS internship?"


class _Doc:
    def __init__(self, content):
        self.page_content = content
        self.metadata = {"source": "internships.txt"}


class _Processor:
    def __init__(self, document):
        self.document = document

    def retrieve_relevant_docs(self, query, k=5, **kwargs):
        return [self.document]


class _Chain:
    def __init__(self, reply):
        self.reply = reply

    def invoke(self, payload):
        return type("Reply", (), {"content": self.reply})()


class _SilentKnowledgeBase:
    @staticmethod
    def find_answer(message):
        return None


def _bot(reply):
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = _SilentKnowledgeBase()
    bot.doc_processor = _Processor(_Doc("MLIS internship guidance."))
    bot.chain = _Chain(reply)
    return bot


def test_live_internship_sentence_drops_only_the_dangling_pointer_sentence():
    factual = (
        "The iSchool maintains a database of over 200 internship opportunities "
        "each semester, including virtual internships."
    )
    reply = (
        f"{factual} You can find more information about the internship program "
        f"[here]({BAD_URL})."
    )

    assert _restrict_reply_links(reply, []) == (factual, 1)


def test_click_here_pointer_sentence_is_removed():
    reply = f"Applications require approval. Please [click here]({BAD_URL})."
    assert _restrict_reply_links(reply, []) == ("Applications require approval.", 1)


def test_this_link_pointer_sentence_is_removed():
    reply = f"Prepare your resume. Please use [this link]({BAD_URL})."
    assert _restrict_reply_links(reply, []) == ("Prepare your resume.", 1)


def test_bare_url_visit_fragment_is_removed():
    reply = f"Ask your internship coordinator first. Visit: {BAD_URL}"
    assert _restrict_reply_links(reply, []) == (
        "Ask your internship coordinator first.",
        1,
    )


def test_descriptive_disallowed_label_remains_plain_text():
    reply = f"See the [MLIS Internships page]({BAD_URL})."
    assert _restrict_reply_links(reply, []) == ("See the MLIS Internships page.", 1)


def test_allowed_generic_link_and_label_are_unchanged():
    reply = f"Apply [here]({ALLOWED_URL})."
    document = _Doc(f"Internship applications: {ALLOWED_URL}")
    assert _restrict_reply_links(reply, [document]) == (reply, 0)


def test_reply_containing_only_a_dangling_pointer_uses_existing_fallback():
    result = _bot(f"[Here]({BAD_URL}).").chat(QUESTION, program="mlis")

    assert result["source"] == "fallback"
    assert result["response"] == RAGAdvisingChatbot._missing_context_response(QUESTION)


def test_multiline_bullets_keep_their_structure_when_pointer_bullet_is_removed():
    reply = (
        "**Before applying**\n"
        "- Prepare your resume.\n"
        f"- [Learn more]({BAD_URL}).\n"
        "- Contact the internship coordinator.\n\n"
        "Keep copies of your materials."
    )
    expected = (
        "**Before applying**\n"
        "- Prepare your resume.\n"
        "- Contact the internship coordinator.\n\n"
        "Keep copies of your materials."
    )

    assert _restrict_reply_links(reply, []) == (expected, 1)


def test_pointer_clause_without_closing_punctuation_does_not_eat_a_letter():
    reply = f"INFO 294 is the course, see [here]({BAD_URL}) now"
    assert _restrict_reply_links(reply, []) == ("INFO 294 is the course", 1)


def test_apply_sentence_keeps_deadline_facts_when_link_is_removed():
    reply = f"Apply [here]({BAD_URL}) by March 1, 2027 to be considered."
    assert _restrict_reply_links(reply, []) == (
        "Apply by March 1, 2027 to be considered.",
        1,
    )


def test_see_sentence_keeps_deadline_facts_when_bare_url_is_removed():
    reply = f"See {BAD_URL} for the March 1 deadline."
    assert _restrict_reply_links(reply, []) == ("See for the March 1 deadline.", 1)


def test_apply_provider_sentence_keeps_every_fact_when_bare_url_is_removed():
    reply = f"You can apply through Cal State Apply at {BAD_URL} before Feb 1."
    assert _restrict_reply_links(reply, []) == (
        "You can apply through Cal State Apply at before Feb 1.",
        1,
    )


def test_add_code_sentence_keeps_course_and_deadline_when_link_is_removed():
    reply = (
        f"Go to [this page]({BAD_URL}) to request INFO 294 add codes; "
        "the deadline is Sep 15."
    )
    assert _restrict_reply_links(reply, []) == (
        "Go to to request INFO 294 add codes; the deadline is Sep 15.",
        1,
    )
