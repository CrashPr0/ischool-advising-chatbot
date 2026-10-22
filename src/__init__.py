"""iSchool Advising Chatbot - Langchain-based chatbot for student advising."""

__version__ = "0.2.0"
__author__ = "SJSU CMPE 195 Team 22"

try:
    from .rag_chatbot import RAGAdvisingChatbot
    from .config import Config, LLMProvider
    from .llm_providers import LLMFactory
    from .knowledge_base import KnowledgeBase, get_knowledge_base
    from .document_processor import DocumentProcessor, setup_sample_docs
except ImportError:  # pragma: no cover
    from rag_chatbot import RAGAdvisingChatbot
    from config import Config, LLMProvider
    from llm_providers import LLMFactory
    from knowledge_base import KnowledgeBase, get_knowledge_base
    from document_processor import DocumentProcessor, setup_sample_docs

__all__ = [
    "RAGAdvisingChatbot",
    "Config",
    "LLMProvider",
    "LLMFactory",
    "KnowledgeBase",
    "get_knowledge_base",
    "DocumentProcessor",
    "setup_sample_docs",
]
