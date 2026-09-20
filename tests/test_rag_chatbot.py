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
