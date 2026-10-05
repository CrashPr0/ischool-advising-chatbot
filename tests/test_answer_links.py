"""Model replies may only keep links from this turn's pages or vetted templates."""

import logging
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from knowledge_base import get_knowledge_base
from rag_chatbot import RAGAdvisingChatbot, _restrict_reply_links
from stripped_link_log import StrippedLinkLog

REAL_SOURCE = (
    "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=203201"
)
INVENTED = "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=999999"
ADVISING = "https://ischoolapps.sjsu.edu/advising/index.php"
QUESTION = "What is ISDA 150?"


class _Doc:
    def __init__(self, content, source_url=""):
        self.page_content = content
        self.metadata = {"source": "page.txt"}
        if source_url:
            self.metadata["source_url"] = source_url


class _Processor:
    def __init__(self, docs):
        self.docs = list(docs)

    def retrieve_relevant_docs(self, query, k=5):
        return list(self.docs)


class _Chain:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def invoke(self, payload):
        self.calls += 1
        return type("Reply", (), {"content": self.content})()


class _RaisingChain:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def invoke(self, payload):
        self.calls += 1
        raise self.error


class _RateLimit(Exception):
    def __init__(self):
        super().__init__("Error code: 429")
        self.status_code = 429


class _ContentFilter(Exception):
    def __init__(self):
        super().__init__("The response was filtered")
        self.status_code = 400
        self.code = "content_filter"
        self.body = {
            "error": {
                "code": "content_filter",
                "innererror": {"code": "ResponsibleAIPolicyViolation"},
            }
        }


def _records():
    path = os.environ["STRIPPED_LINK_LOG_PATH"]
    if not os.path.exists(path):
        return []
    return StrippedLinkLog(path).records()


class _SilentKnowledgeBase:
    @staticmethod
    def find_answer(message):
        return None


def _bot(docs, reply):
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = _SilentKnowledgeBase()
    bot.doc_processor = _Processor(docs)
    bot.chain = _Chain(reply)
    return bot


def _page(source_url=REAL_SOURCE, body="ISDA 150 - Special Topics. 3 units."):
    return _Doc(f"{body}\nSource URL: {source_url}", source_url=source_url)


def test_invented_url_is_removed_and_real_source_url_stays():
    reply = (
        f"ISDA 150 is Special Topics (3 units). Catalog: {REAL_SOURCE}. "
        f"More detail: {INVENTED}."
    )
    result = _bot([_page()], reply).chat(QUESTION)
    assert INVENTED not in result["response"]
    assert REAL_SOURCE in result["response"]
    assert "Special Topics" in result["response"]
    assert result["source"] == "rag"


def test_rewritten_source_url_does_not_replace_the_real_one():
    rewritten = (
        "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=99&coid=203201"
    )
    reply = f"ISDA 150 is Special Topics (3 units). See {rewritten} and {REAL_SOURCE}."
    result = _bot([_page()], reply).chat(QUESTION)
    assert rewritten not in result["response"]
    assert REAL_SOURCE in result["response"]
    assert result["source"] == "rag"


def test_reply_that_is_only_an_invented_url_uses_the_missing_context_fallback():
    result = _bot([_page()], INVENTED).chat(QUESTION)
    assert INVENTED not in result["response"]
    assert result["response"] == RAGAdvisingChatbot._missing_context_response(QUESTION)
    assert result["source"] == "fallback"
    assert result["confidence"] == 0.2


def test_chinese_invented_link_uses_the_chinese_fallback():
    question = "这个课程的链接是什么？"
    result = _bot([_page()], INVENTED).chat(question)
    assert INVENTED not in result["response"]
    assert result["response"] == RAGAdvisingChatbot._missing_context_response(question)
    assert result["response"].startswith("我在现有的咨询资料中")


def test_source_url_citation_is_kept_when_the_body_does_not_repeat_it():
    document = _Doc("ISDA 150 - Special Topics. 3 units.", source_url=REAL_SOURCE)
    reply = f"ISDA 150 is Special Topics (3 units). {REAL_SOURCE}"
    result = _bot([document], reply).chat(QUESTION)
    assert result["response"] == reply
    assert result["source"] == "rag"


def test_link_printed_in_the_page_is_kept_when_it_is_not_the_source_url():
    body_url = "https://michaelsutter.com/sjsu"
    invented = "https://michaelsutter.com/other"
    document = _Doc(
        f"Order the diploma online: {body_url}",
        source_url="https://www.sjsu.edu/registrar/graduation/diploma.php",
    )
    reply = f"Order the diploma at {body_url}. Do not use {invented}."
    result = _bot([document], reply).chat("How do I order a diploma?")
    assert body_url in result["response"]
    assert invented not in result["response"]
    assert "Order the diploma" in result["response"]


def test_vetted_advising_template_link_is_kept_without_being_retrieved():
    document = _Doc("ISDA 150 - Special Topics. 3 units.")
    reply = (
        "ISDA 150 is Special Topics (3 units). For anything still missing, "
        f"contact {ADVISING}"
    )
    result = _bot([document], reply).chat(QUESTION)
    assert result["response"] == reply
    assert ADVISING in result["response"]


def test_catalog_home_template_does_not_allow_every_catalog_url():
    text = (
        "Start at https://catalog.sjsu.edu/ and then open "
        "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=1"
    )
    cleaned, dropped = _restrict_reply_links(text, [])
    assert "https://catalog.sjsu.edu/" in cleaned
    assert "coid=1" not in cleaned
    assert dropped == 1


def test_a_link_from_an_earlier_turn_is_not_treated_as_this_turn_source():
    document = _Doc("ISDA 100 is Technical Writing (3 units).")
    reply = f"ISDA 100 is Technical Writing (3 units). Details: {REAL_SOURCE}"
    history = [{"user": "Where is the catalog?", "assistant": f"See {REAL_SOURCE}"}]
    result = _bot([document], reply).chat("What is ISDA 100?", history=list(history))
    assert REAL_SOURCE not in result["response"]
    assert "Technical Writing" in result["response"]
    assert REAL_SOURCE in history[0]["assistant"]


def test_markdown_invented_link_drops_the_url_and_keeps_the_label():
    reply = (
        "ISDA 150 is Special Topics (3 units). "
        "See [the invented form](https://evil.example/isda-form)."
    )
    result = _bot([_page()], reply).chat(QUESTION)
    assert "https://evil.example/isda-form" not in result["response"]
    assert "the invented form" in result["response"]
    assert "Special Topics" in result["response"]


def test_markdown_real_source_link_is_not_rewritten():
    reply = f"See [ISDA 150]({REAL_SOURCE}) for the title and units."
    result = _bot([_page()], reply).chat(QUESTION)
    assert result["response"] == reply


def test_scheme_and_host_rewrites_are_dropped():
    document = _page()
    http_rewrite = REAL_SOURCE.replace("https://", "http://", 1)
    host_rewrite = REAL_SOURCE.replace("catalog.sjsu.edu", "catalog.sjsu.edu.evil.test", 1)
    cleaned, dropped = _restrict_reply_links(
        f"Use {http_rewrite} or {host_rewrite}.",
        [document],
    )
    assert http_rewrite not in cleaned
    assert "evil.test" not in cleaned
    assert dropped == 2


def test_query_order_of_a_real_source_url_is_kept():
    real = "https://catalog.sjsu.edu/content.php?catoid=23&navoid=8473"
    reordered = "https://catalog.sjsu.edu/content.php?navoid=8473&catoid=23"
    cleaned, dropped = _restrict_reply_links(
        f"See {reordered}.",
        [_Doc(f"Source URL: {real}", source_url=real)],
    )
    assert dropped == 0
    assert cleaned == f"See {reordered}."


def test_reply_without_links_is_unchanged():
    text = "DATA 201 is a required course.  "
    assert _restrict_reply_links(text, [_page()]) == (text, 0)


def test_invented_mailto_is_dropped():
    cleaned, dropped = _restrict_reply_links(
        "Email advisor@sjsu.edu at mailto:advisor@sjsu.edu.",
        [_page()],
    )
    assert "mailto:" not in cleaned
    assert dropped == 1


def test_knowledge_base_template_links_are_not_filtered():
    bot = _bot([], "this reply is not used")
    bot.knowledge_base = get_knowledge_base()
    result = bot.chat("hello")
    assert result["source"] == "knowledge_base"
    assert ADVISING in result["response"]
    assert "https://catalog.sjsu.edu/" in result["response"]
    assert "https://www.sjsu.edu/registrar/" in result["response"]


def test_dropped_invented_link_is_logged_and_not_stored(caplog):
    history = []
    bot = _bot([_page()], f"ISDA 150 is Special Topics (3 units). See {INVENTED}.")
    with caplog.at_level(logging.INFO):
        result = bot.chat(QUESTION, history=history)
    assert INVENTED not in result["response"]
    assert INVENTED not in history[-1]["response"]
    lines = [
        record.message
        for record in caplog.records
        if record.message.startswith("chat_request")
    ]
    assert len(lines) == 1
    assert "dropped_links=1" in lines[0]
    assert "source=rag" in lines[0]


def test_a_stripped_url_is_recorded_and_a_kept_url_is_not():
    other = "https://evil.example/isda-form"
    reply = (
        f"ISDA 150 is Special Topics (3 units). Catalog: {REAL_SOURCE}. "
        f"Also see {INVENTED} and {other}."
    )
    history = [
        {
            "user": "Where was last semester's form?",
            "assistant": "Try https://evil.example/old-form",
        }
    ]
    result = _bot([_page()], reply).chat(QUESTION, history=list(history))
    assert INVENTED not in result["response"]
    assert other not in result["response"]
    assert REAL_SOURCE in result["response"]
    rows = _records()
    assert len(rows) == 1
    row = rows[0]
    assert row["question"] == QUESTION
    assert "Where was last semester's form?" not in row["question"]
    assert row["outcome"] == "edited"
    assert row["stripped_urls"] == [INVENTED, other]
    assert REAL_SOURCE not in row["stripped_urls"]
    assert "https://evil.example/old-form" not in row["stripped_urls"]
    datetime.fromisoformat(row["created_at"])
    assert set(row) == {"created_at", "question", "outcome", "stripped_urls"}


def test_a_kept_source_url_alone_is_not_recorded():
    reply = f"ISDA 150 is Special Topics (3 units). {REAL_SOURCE}"
    result = _bot([_page()], reply).chat(QUESTION)
    assert result["response"] == reply
    assert _records() == []


def test_fallback_replacement_is_recorded_as_fallback():
    result = _bot([_page()], INVENTED).chat(QUESTION)
    assert result["source"] == "fallback"
    assert result["response"] == RAGAdvisingChatbot._missing_context_response(QUESTION)
    rows = _records()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "fallback"
    assert rows[0]["question"] == QUESTION
    assert rows[0]["stripped_urls"] == [INVENTED]
    assert ADVISING not in rows[0]["stripped_urls"]


def test_stripped_link_record_is_still_there_after_the_connection_closes():
    _bot([_page()], f"ISDA 150 is Special Topics. See {INVENTED}.").chat(QUESTION)
    path = os.environ["STRIPPED_LINK_LOG_PATH"]
    again = StrippedLinkLog(path).records()
    assert len(again) == 1
    assert again[0]["stripped_urls"] == [INVENTED]
    connection = sqlite3.connect(path)
    try:
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(stripped_link_checks)")
        ]
    finally:
        connection.close()
    assert columns == ["id", "created_at", "question", "outcome", "stripped_urls"]


def test_template_and_crisis_turns_are_not_recorded():
    crisis = _bot([_page()], f"See {INVENTED}")

    class _CrisisTemplates:
        @staticmethod
        def find_answer(message):
            return {
                "answer": (
                    "I'm glad you told me. You do not have to handle this alone. "
                    "https://988lifeline.org/"
                ),
                "matched_question": "crisis",
                "confidence": 1.0,
            }

    crisis.knowledge_base = _CrisisTemplates()
    crisis_result = crisis.chat("I want to die")
    assert crisis_result["source"] == "knowledge_base"
    assert crisis.chain.calls == 0
    assert "https://988lifeline.org/" in crisis_result["response"]

    greeting = _bot([], "unused")
    greeting.knowledge_base = get_knowledge_base()
    greeting_result = greeting.chat("hello")
    assert greeting_result["source"] == "knowledge_base"
    assert greeting.chain.calls == 0

    filtered = _bot([_page()], f"See {INVENTED}")
    filtered.chain = _RaisingChain(_ContentFilter())
    filtered_result = filtered.chat(QUESTION)
    assert filtered_result["source"] == "filtered"
    assert ADVISING in filtered_result["response"]

    busy = _bot([_page()], f"See {INVENTED}")
    busy.chain = _RaisingChain(_RateLimit())
    busy_result = busy.chat(QUESTION)
    assert busy_result["source"] == "busy"

    assert _records() == []


def test_a_log_write_failure_still_returns_the_filtered_reply(monkeypatch):
    def explode(self, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(StrippedLinkLog, "record", explode)
    reply = f"ISDA 150 is Special Topics (3 units). See {INVENTED}."
    result = _bot([_page()], reply).chat(QUESTION)
    assert INVENTED not in result["response"]
    assert "Special Topics" in result["response"]
    assert result["source"] == "rag"
    assert _records() == []
