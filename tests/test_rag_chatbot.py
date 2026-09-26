"""Focused behavior tests for the RAG chatbot prompt contract."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rag_chatbot import RAGAdvisingChatbot


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
    assert turns == [{"user": "What are the required courses?", "assistant": "DATA 201."}]


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
        _retrieval_query_with_history("What is DATA 201?", turns)
        == "What is DATA 201?"
    )
