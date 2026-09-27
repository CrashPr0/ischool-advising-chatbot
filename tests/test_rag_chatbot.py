"""Focused behavior tests for the RAG chatbot prompt contract."""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_providers import REASONING_RETRY
from rag_chatbot import (
    BUSY_RESPONSE,
    FILTERED_RESPONSE,
    RAGAdvisingChatbot,
)


def test_system_prompt_does_not_guess_titles_or_units():
    prompt = RAGAdvisingChatbot.SYSTEM_PROMPT
    assert (
        "If a course number appears without its title in the sources, list the number "
        "and any units that are present and say the title isn't in the sources; never "
        "supply a title from your own knowledge."
    ) in prompt
    assert (
        "Never invent course numbers, titles, unit counts, prerequisites, "
        "policies, or deadlines that aren't written in the sources."
    ) in prompt
    assert "policies, or deadlines" in prompt
    assert "Answer from whatever the documents do contain" in prompt
    assert "only for the missing part" in prompt
    assert (
        "Do not withhold facts the sources do contain because a course title "
        "or unit count is missing."
    ) in prompt


def test_chat_retrieval_k_defaults_to_eight(monkeypatch):
    monkeypatch.delenv("RAG_TOP_K", raising=False)
    bot = _bare_bot()
    seen = {}

    def retrieve(query, k=5):
        seen["k"] = k
        return [_Doc("Submit the graduation application and check commencement.")]

    bot.doc_processor.retrieve_relevant_docs = retrieve
    result = bot.chat("How do I apply to graduate?")
    assert seen["k"] == 8
    assert result["source"] == "rag"


def test_chat_retrieval_k_follows_rag_top_k(monkeypatch):
    monkeypatch.setenv("RAG_TOP_K", "5")
    bot = _bare_bot()
    seen = {}

    def retrieve(query, k=8):
        seen["k"] = k
        return [_Doc("Submit the graduation application and check commencement.")]

    bot.doc_processor.retrieve_relevant_docs = retrieve
    bot.chat("How do I apply to graduate?")
    assert seen["k"] == 5


def test_english_question_requires_english_response():
    instruction = RAGAdvisingChatbot._response_language_instruction(
        "Are there scholarships for ISDA students?"
    )
    assert instruction == "Respond in English."


def test_chinese_question_requires_simplified_chinese_response():
    instruction = RAGAdvisingChatbot._response_language_instruction(
        "ISDA 学生可以申请奖学金吗？"
    )
    assert instruction == "Respond in Simplified Chinese."


def test_missing_context_fallback_matches_english_question():
    response = RAGAdvisingChatbot._missing_context_response(
        "Are there scholarships for ISDA students?"
    )
    assert response.startswith("I couldn't find enough information")
    assert "https://ischoolapps.sjsu.edu/advising/index.php" in response


def test_missing_context_fallback_matches_chinese_question():
    response = RAGAdvisingChatbot._missing_context_response(
        "ISDA 学生可以申请奖学金吗？"
    )
    assert response.startswith("我在现有的咨询资料中")
    assert "https://ischoolapps.sjsu.edu/advising/index.php" in response


class _Doc:
    def __init__(self, content):
        self.page_content = content
        self.metadata = {"source": "catalog.txt"}


class _Processor:
    def __init__(self):
        self.queries = []

    def retrieve_relevant_docs(self, query, k=5):
        self.queries.append(query)
        if "required courses" in query.casefold():
            return [_Doc("DATA 201 is required.")]
        return []


class _KnowledgeBase:
    @staticmethod
    def find_answer(message):
        return None


class _Chain:
    def __init__(self):
        self.payloads = []

    def invoke(self, payload):
        self.payloads.append(payload)
        return type("Reply", (), {"content": "DATA 201 is a required course."})()


def _bare_bot():
    """Build a chatbot without loading a provider or Chroma."""
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = _KnowledgeBase()
    bot.doc_processor = _Processor()
    bot.chain = _Chain()
    return bot


def test_follow_up_uses_the_previous_question_without_calling_a_real_model():
    bot = _bare_bot()
    alone = bot.chat("what about the prerequisites?")
    assert alone["source"] == "fallback"
    assert bot.chain.payloads == []
    assert bot.doc_processor.queries[-1] == "what about the prerequisites?"

    turns = [{"user": "What are the required courses?", "assistant": "DATA 201."}]
    answered = bot.chat("what about the prerequisites?", history=list(turns))

    assert answered["source"] == "rag"
    assert answered["response"] == "DATA 201 is a required course."
    query = bot.doc_processor.queries[-1]
    assert query.startswith("What are the required courses?")
    assert query.endswith("what about the prerequisites?")
    payload = bot.chain.payloads[-1]
    assert payload["question"] == "what about the prerequisites?"
    assert "Assistant: DATA 201." in payload["context"]
    assert "Earlier conversation is context" in payload["context"]
    assert turns == [
        {"user": "What are the required courses?", "assistant": "DATA 201."}
    ]


def test_a_new_question_is_not_searched_with_the_previous_turn():
    bot = _bare_bot()
    turns = [
        {"user_message": "What are the required courses?", "response": "DATA 201."}
    ]
    result = bot.chat(
        "Are there scholarships for ISDA students?",
        history=turns,
    )
    assert bot.doc_processor.queries[-1] == "Are there scholarships for ISDA students?"
    assert result["source"] == "fallback"
    assert bot.chain.payloads == []


def test_session_turns_keep_only_the_latest_student_text():
    from rag_chatbot import _retrieval_query_with_history, _session_turns

    assert _session_turns(None) == []
    assert _session_turns([]) == []
    turns = _session_turns(
        [
            {"user": "  one  ", "assistant": " A ", "source": "test"},
            "skip",
            {"user_message": "two", "response": " B "},
            {"user": "   ", "assistant": "ignored"},
            {"user": "three", "assistant": 3},
            {"user": "four", "assistant": "D"},
            {"user": "five", "assistant": "E"},
        ]
    )
    assert [turn["user"] for turn in turns] == ["two", "three", "four", "five"]
    assert turns[1]["assistant"] == ""

    query = _retrieval_query_with_history(
        "Does it count?",
        [{"user": "Tell me about the capstone.", "assistant": "It is DATA 298."}],
    )
    assert query.startswith("Tell me about the capstone.")
    assert query.endswith("Does it count?")
    assert (
        _retrieval_query_with_history("What is DATA 201?", turns) == "What is DATA 201?"
    )


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
                "innererror": {
                    "code": "ResponsibleAIPolicyViolation",
                    "content_filter_result": {
                        "hate": {"filtered": False, "severity": "safe"},
                        "violence": {"filtered": True, "severity": "medium"},
                    },
                },
            }
        }


class _Pipe:
    def __or__(self, other):
        return other


class _RaisingChain:
    def __init__(self, error):
        self.error = error

    def invoke(self, payload):
        raise self.error


def _chat_lines(caplog):
    return [
        record.message
        for record in caplog.records
        if record.message.startswith("chat_request")
    ]


def test_chat_logs_one_timings_line_and_hides_timings_by_default(caplog, monkeypatch):
    monkeypatch.delenv("CHAT_DEBUG_TIMINGS", raising=False)
    bot = _bare_bot()
    with caplog.at_level(logging.INFO):
        result = bot.chat("what about the prerequisites?")
    assert result["source"] == "fallback"
    assert "timings" not in result
    lines = _chat_lines(caplog)
    assert len(lines) == 1
    for field in (
        "retrieval_ms=",
        "embedding_ms=",
        "llm_ms=",
        "total_ms=",
        "llm_attempts=0",
        "rate_limit_count=",
        "deployment=",
    ):
        assert field in lines[0]


def test_debug_timings_are_on_the_result_but_not_in_history(monkeypatch):
    monkeypatch.setenv("CHAT_DEBUG_TIMINGS", "true")
    bot = _bare_bot()
    history = []
    result = bot.chat("what about the prerequisites?", history=history)
    assert result["timings"]["llm_attempts"] == 0
    assert "retrieval_ms" in result["timings"]
    assert "embedding_ms" in result["timings"]
    assert "timings" not in history[0]


def test_rate_limit_without_fallback_is_busy_not_the_apology(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", raising=False)
    bot = _bare_bot()
    bot.chain = _RaisingChain(_RateLimit())
    result = bot.chat("What are the required courses?")
    assert result["source"] == "busy"
    assert result["response"] == BUSY_RESPONSE
    assert "sorry" not in result["response"].lower()


def test_content_filter_redirects_and_does_not_call_the_fallback(caplog, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "backup-deployment")
    created = []

    def create_llm(*args, **kwargs):
        created.append(kwargs)
        raise AssertionError("fallback should not run for a content filter")

    monkeypatch.setattr("rag_chatbot.LLMFactory.create_llm", create_llm)
    bot = _bare_bot()
    bot.chain = _RaisingChain(_ContentFilter())
    with caplog.at_level(logging.INFO):
        result = bot.chat("What are the required courses?")
    assert created == []
    assert result["source"] == "filtered"
    assert "rephrase" in result["response"].lower()
    assert "https://ischoolapps.sjsu.edu/advising/index.php" in result["response"]
    assert result["response"] == FILTERED_RESPONSE
    line = _chat_lines(caplog)[0]
    assert "source=filtered" in line
    assert "filter_category=violence" in line


def test_fallback_deployment_answers_after_a_429(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "backup-deployment")
    created = []

    class Fallback:
        def invoke(self, payload):
            return type("Reply", (), {"content": "from the fallback"})()

    def create_llm(provider=None, model=None, temperature=None):
        created.append((provider, model, temperature))
        return Fallback()

    monkeypatch.setattr("rag_chatbot.LLMFactory.create_llm", create_llm)
    bot = _bare_bot()
    bot.prompt_template = _Pipe()
    bot.chain = _RaisingChain(_RateLimit())
    result = bot.chat("What are the required courses?")
    assert result["source"] == "rag"
    assert result["response"] == "from the fallback"
    assert created[0][1] == "backup-deployment"


def test_fallback_retries_without_temperature_when_the_model_rejects_it(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "reasoner")
    temperatures = []
    calls = {"n": 0}

    class Fallback:
        def invoke(self, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError(
                    "Unsupported parameter: 'temperature' is not supported with this model. "
                    "Use max_completion_tokens instead of max_tokens."
                )
            return type("Reply", (), {"content": "reasoned answer"})()

    def create_llm(provider=None, model=None, temperature=None):
        temperatures.append(temperature)
        return Fallback()

    monkeypatch.setattr("rag_chatbot.LLMFactory.create_llm", create_llm)
    bot = _bare_bot()
    bot.prompt_template = _Pipe()
    bot.chain = _RaisingChain(_RateLimit())
    result = bot.chat("What are the required courses?")
    assert result["response"] == "reasoned answer"
    assert result["source"] == "rag"
    assert temperatures[-1] is REASONING_RETRY


def test_error_log_names_the_exception(caplog):
    bot = _bare_bot()
    bot.chain = _RaisingChain(RuntimeError("model exploded"))
    with caplog.at_level(logging.INFO):
        result = bot.chat("What are the required courses?")
    assert result["source"] == "error"
    assert "couldn’t generate a response" in result["response"]
    line = _chat_lines(caplog)[0]
    assert "error_type=RuntimeError" in line
    assert 'error_message="model exploded"' in line


def test_server_error_uses_the_fallback_deployment(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "backup-deployment")

    class ServerError(Exception):
        def __init__(self):
            super().__init__("bad gateway")
            self.status_code = 502

    class Fallback:
        def invoke(self, payload):
            return type("Reply", (), {"content": "recovered"})()

    monkeypatch.setattr(
        "rag_chatbot.LLMFactory.create_llm",
        lambda *args, **kwargs: Fallback(),
    )
    bot = _bare_bot()
    bot.prompt_template = _Pipe()
    bot.chain = _RaisingChain(ServerError())
    result = bot.chat("What are the required courses?")
    assert result["response"] == "recovered"
