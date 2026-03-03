"""Tests for the knowledge base."""
import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from knowledge_base import KnowledgeBase, get_knowledge_base


class TestKnowledgeBase:
    """Test knowledge base functionality."""
    
    def test_knowledge_base_initialization(self):
        """Test that knowledge base initializes correctly."""
        kb = KnowledgeBase()
        assert kb is not None
        assert len(kb.COMMON_QA) > 0
    
    def test_exact_question_match(self):
        """Test exact question matching."""
        kb = KnowledgeBase()
        result = kb.find_answer("what do i need to graduate")
        assert result is not None
        assert result["is_hardcoded"] is True
        assert result["confidence"] == 1.0
    
    def test_keyword_matching(self):
        """Test keyword-based matching."""
        kb = KnowledgeBase()
        result = kb.find_answer("I want to graduate, what are my requirements?")
        assert result is not None
        assert result["is_hardcoded"] is True
        assert result["confidence"] >= 0.5
    
    def test_greeting_matching(self):
        """Test greeting matching."""
        kb = KnowledgeBase()
        result = kb.find_answer("hello")
        assert result is not None
        assert "Assistant" in result["answer"] or "Hello" in result["answer"]
    
    def test_no_match(self):
        """Test that non-matching questions return None."""
        kb = KnowledgeBase()
        result = kb.find_answer("xyzabc xyz random nonsense xxxxx")
        # Should either return None or low confidence match
        if result:
            assert result["confidence"] < 0.5
    
    def test_get_all_questions(self):
        """Test getting all questions."""
        kb = KnowledgeBase()
        questions = kb.get_all_questions()
        assert isinstance(questions, list)
        assert len(questions) > 0
        assert "what do i need to graduate" in questions
    
    def test_add_qa(self):
        """Test adding new Q&A."""
        kb = KnowledgeBase()
        initial_count = len(kb.COMMON_QA)
        
        kb.add_qa(
            "test question",
            "test answer",
            ["test", "question"]
        )
        
        assert len(kb.COMMON_QA) == initial_count + 1
        result = kb.find_answer("test question")
        assert result is not None
        assert result["answer"] == "test answer"
    
    def test_singleton_instance(self):
        """Test that knowledge base singleton works."""
        kb1 = get_knowledge_base()
        kb2 = get_knowledge_base()
        assert kb1 is kb2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
