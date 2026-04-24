"""RAG-enhanced chatbot with knowledge base and document retrieval."""

from typing import List, Optional, Dict

from langchain_core.prompts import PromptTemplate

from llm_providers import LLMFactory
from knowledge_base import get_knowledge_base
from document_processor import DocumentProcessor
from config import Config


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
6. Do not access or reference personal student records."""

    def __init__(self, llm_provider: Optional[str] = None, docs_path: Optional[str] = None):
        self.llm = LLMFactory.create_llm(llm_provider)
        self.knowledge_base = get_knowledge_base()
        self.doc_processor = DocumentProcessor(docs_path)
        self.conversation_history: List[dict] = []

        print("Initializing RAG system...")
        if not self.doc_processor.load_vector_store():
            print("No existing vector store found. To use document retrieval:")
            print("1. Place .txt or .pdf files in ./data/advising_docs/")
            print("2. Or call chatbot.process_documents()")

        self.prompt_template = PromptTemplate(
            input_variables=["context", "question"],
            template=f"""{self.SYSTEM_PROMPT}

{{context}}

Student Question: {{question}}

Assistant Response:""",
        )

        self.chain = self.prompt_template | self.llm

    def process_documents(self, docs_path: Optional[str] = None) -> None:
        if docs_path:
            self.doc_processor.docs_path = docs_path

        print(f"Processing documents from {self.doc_processor.docs_path}...")
        self.doc_processor.process_and_index()

    def chat(self, user_message: str) -> Dict:
        response_data = {
            "user_message": user_message,
            "response": None,
            "source": None,  # "knowledge_base", "rag", or "llm"
            "retrieved_docs": [],
            "confidence": None,
        }

        kb_answer = self.knowledge_base.find_answer(user_message)
        if kb_answer:
            response_data["response"] = kb_answer["answer"]
            response_data["source"] = "knowledge_base"
            response_data["confidence"] = kb_answer["confidence"]
            print(f"[Knowledge Base Hit] Matched: {kb_answer['matched_question']}")
        else:
            retrieved_docs = self.doc_processor.retrieve_relevant_docs(user_message, k=5)
            response_data["retrieved_docs"] = [
                {"content": doc.page_content, "source": doc.metadata.get("source", "unknown")}
                for doc in retrieved_docs
            ]

            context = self._build_context(user_message, retrieved_docs)

            try:
                llm_response = self.chain.invoke({"context": context, "question": user_message})
                response_data["response"] = (
                    llm_response.content if hasattr(llm_response, "content") else str(llm_response)
                )
                response_data["source"] = "rag" if retrieved_docs else "llm"
                response_data["confidence"] = 0.7 if retrieved_docs else 0.5
            except Exception as e:
                response_data["response"] = f"Error generating response: {str(e)}"
                response_data["source"] = "error"

        self.conversation_history.append(response_data)
        return response_data

    def _build_context(self, query: str, documents: List) -> str:
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
        return self.conversation_history

    def clear_history(self) -> None:
        self.conversation_history = []

    def get_knowledge_base_questions(self) -> List[str]:
        return self.knowledge_base.get_all_questions()

    @staticmethod
    def get_available_providers() -> List[str]:
        return LLMFactory.get_available_providers()


__all__ = ["RAGAdvisingChatbot"]

