"""RAG-enhanced chatbot with knowledge base and document retrieval."""
import logging
import os
import re
from typing import List, Optional, Dict
from langchain_core.prompts import PromptTemplate
try:
    from .llm_providers import LLMFactory
    from .knowledge_base import get_knowledge_base
    from .document_processor import DocumentProcessor
except ImportError:  # pragma: no cover
    # Support running with `src/` on sys.path (legacy/tests).
    from llm_providers import LLMFactory
    from knowledge_base import get_knowledge_base
    from document_processor import DocumentProcessor


logger = logging.getLogger(__name__)


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a bounded integer environment setting without crashing requests."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using %s", name, default)
        value = default
    return max(minimum, min(value, maximum))


class RAGAdvisingChatbot:
    """
    Retrieval Augmented Generation (RAG) chatbot for iSchool advising.
    
    Combines:
    1. Hardcoded knowledge base for common questions
    2. Document retrieval for context
    3. LLM for complex questions
    """
    
    SYSTEM_PROMPT = """You are the ISDA Advising Support Chatbot for San José State University's \
BS in Information Science and Data Analytics program.

Your job is to answer students' advising questions **using only the context \
documents provided below**. Follow these rules strictly:

1. Base every factual claim on the provided documents. Quote course numbers, \
unit counts, prerequisites, and policies exactly as they appear.
2. If the documents contain the answer, give a clear, specific response with \
relevant details (course names, unit counts, links, deadlines, etc.).
3. If the documents do NOT contain enough information to answer, say so honestly \
and recommend the student contact the iSchool advising team or visit \
https://ischoolapps.sjsu.edu/advising/index.php.
4. Never fabricate course numbers, unit counts, policies, or deadlines.
5. Keep answers concise but complete. Use bullet points for lists.
6. Do not access or reference personal student records.
7. Follow the response-language instruction exactly. Never infer the response
   language from retrieved documents, URLs, model defaults, or prior requests."""
    
    def __init__(self, llm_provider: Optional[str] = None, docs_path: Optional[str] = None):
        """
        Initialize the RAG chatbot.
        
        Args:
            llm_provider: LLM provider to use (openai, anthropic, huggingface)
            docs_path: Path to advising documents directory
        """
        # Initialize components
        self.llm = LLMFactory.create_llm(llm_provider)
        self.knowledge_base = get_knowledge_base()
        self.doc_processor = DocumentProcessor(docs_path)
        # Chat is stateless by default. The Flask layer owns bounded,
        # session-scoped history so a singleton chatbot cannot leak one
        # visitor's messages to another. This list remains only for backwards
        # compatibility with direct CLI callers that explicitly request it.
        self.conversation_history: List[dict] = []
        
        # Try to load or create vector store
        print("Initializing RAG system...")
        if not self.doc_processor.load_vector_store():
            print("No existing vector store found. To use document retrieval:")
            print("1. Place .txt or .pdf files in ./data/advising_docs/")
            print("2. Or call chatbot.process_documents()")
        
        # Create the prompt template
        self.prompt_template = PromptTemplate(
            input_variables=["context", "language_instruction", "question"],
            template=f"""{self.SYSTEM_PROMPT}

{{context}}

Response Language: {{language_instruction}}

Student Question: {{question}}

Assistant Response:"""
        )
        
        # Create the chain using the pipe operator (modern Langchain API)
        self.chain = self.prompt_template | self.llm
    
    def process_documents(self, docs_path: Optional[str] = None) -> None:
        """
        Load and process advising documents.
        
        Args:
            docs_path: Optional path to documents directory
        """
        if docs_path:
            self.doc_processor.docs_path = docs_path
        
        print(f"Processing documents from {self.doc_processor.docs_path}...")
        self.doc_processor.process_and_index()
    
    def chat(self, user_message: str, history: Optional[List[dict]] = None) -> Dict:
        """
        Send a message and get a response using RAG.
        
        The response chain:
        1. Check knowledge base for hardcoded answers
        2. If not found, retrieve relevant documents
        3. Use LLM with document context to generate response
        
        Args:
            user_message: The student's question
            
        Returns:
            Dict with response metadata
        """
        if not isinstance(user_message, str):
            raise ValueError("Message must be a string")
        user_message = user_message.strip()
        max_length = _bounded_env_int("CHAT_MESSAGE_MAX_LENGTH", 4000, 1, 16000)
        if not user_message:
            raise ValueError("Message cannot be empty")
        if len(user_message) > max_length:
            raise ValueError(f"Message exceeds the {max_length}-character limit")

        response_data = {
            "user_message": user_message,
            "response": None,
            "source": None,  # "knowledge_base", "rag", or "llm"
            "retrieved_docs": [],
            "confidence": None
        }
        
        # Step 1: Check knowledge base first
        kb_answer = self.knowledge_base.find_answer(user_message)
        
        if kb_answer:
            response_data["response"] = kb_answer["answer"]
            response_data["source"] = "knowledge_base"
            response_data["confidence"] = kb_answer["confidence"]
            
            print(f"[Knowledge Base Hit] Matched: {kb_answer['matched_question']}")
        
        else:
            # Step 2: Retrieve relevant documents
            top_k = _bounded_env_int("RAG_TOP_K", 5, 1, 20)
            retrieved_docs = self.doc_processor.retrieve_relevant_docs(user_message, k=top_k)
            response_data["retrieved_docs"] = [
                {
                    "content": doc.page_content,
                    "source": doc.metadata.get("source", "unknown")
                }
                for doc in retrieved_docs
            ]
            
            # Step 3: Build context from retrieved documents
            context = self._build_context(user_message, retrieved_docs)

            if not retrieved_docs:
                response_data["response"] = self._missing_context_response(user_message)
                response_data["source"] = "fallback"
                response_data["confidence"] = 0.2
                if history is not None:
                    history.append(response_data)
                return response_data
            
            # Step 4: Generate response with LLM
            try:
                llm_response = self.chain.invoke(
                    {
                        "context": context,
                        "language_instruction": self._response_language_instruction(
                            user_message
                        ),
                        "question": user_message,
                    }
                )
                response_text = (
                    llm_response.content
                    if hasattr(llm_response, "content")
                    else str(llm_response)
                )
                response_limit = _bounded_env_int(
                    "RAG_RESPONSE_MAX_CHARS", 12_000, 500, 50_000
                )
                response_data["response"] = str(response_text)[:response_limit]
                response_data["source"] = "rag" if retrieved_docs else "llm"
                response_data["confidence"] = 0.7 if retrieved_docs else 0.5
            except Exception:
                logger.exception("Error generating chatbot response")
                response_data["response"] = (
                    "I’m sorry, but I couldn’t generate a response right now. "
                    "Please try again or contact the iSchool advising team."
                )
                response_data["source"] = "error"

        # Only append when a caller explicitly supplies a history list. Never
        # mutate process-global state by default (the web app passes none).
        if history is not None:
            history.append(response_data)
        
        return response_data

    @staticmethod
    def _response_language_instruction(user_message: str) -> str:
        """Select Chinese only when the student's question contains Han text."""
        if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", user_message):
            return "Respond in Simplified Chinese."
        return "Respond in English."

    @classmethod
    def _missing_context_response(cls, user_message: str) -> str:
        """Return a safe, deterministic response when retrieval finds nothing."""
        advising_url = "https://ischoolapps.sjsu.edu/advising/index.php"
        if "Simplified Chinese" in cls._response_language_instruction(user_message):
            return (
                "我在现有的咨询资料中没有找到足够的信息来回答这个问题。"
                f"请联系 iSchool 咨询团队或访问 {advising_url} 获取帮助。"
            )
        return (
            "I couldn't find enough information in the available advising "
            "documents to answer that question. Please contact the iSchool "
            f"advising team or visit {advising_url} for help."
        )
    
    def _build_context(self, query: str, documents: List) -> str:
        """Build context string from retrieved document chunks."""
        if not documents:
            return (
                "No relevant documents were found for this question. "
                "Tell the student you don't have enough information and suggest "
                "they contact the iSchool advising team.\n"
            )

        context_parts = ["--- Retrieved advising documents ---\n"]

        for i, doc in enumerate(documents, 1):
            source = doc.metadata.get("source", "unknown")
            content = doc.page_content.strip()
            context_parts.append(f"[Document {i} — {source}]\n{content}\n")

        context_parts.append("--- End of documents ---")
        return "\n".join(context_parts)
    
    def get_history(self) -> List[dict]:
        """Get explicitly recorded direct-caller history (normally empty)."""
        return self.conversation_history
    
    def clear_history(self) -> None:
        """Clear the conversation history."""
        self.conversation_history = []
    
    def get_knowledge_base_questions(self) -> List[str]:
        """Get all available hardcoded questions."""
        return self.knowledge_base.get_all_questions()
    
    @staticmethod
    def get_available_providers() -> List[str]:
        """Get available LLM providers."""
        return LLMFactory.get_available_providers()
