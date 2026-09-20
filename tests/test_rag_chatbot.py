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
