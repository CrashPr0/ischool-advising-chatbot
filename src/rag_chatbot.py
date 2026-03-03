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
    
    SYSTEM_PROMPT = """You are a helpful iSchool Senior Advising Assistant powered by Langchain.
Your role is to help students with questions about:
- Program requirements and courses
- Graduation requirements
- Career guidance
- Course scheduling
- Academic policies

Always be professional, helpful, and accurate. If you don't know something, 
be honest and suggest the student speak with an official advisor.

If relevant context documents are provided, use them to inform your answer.
Ground your responses in the documents when available."""
    
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
        self.conversation_history: List[dict] = []
        
        # Try to load or create vector store
        print("Initializing RAG system...")
        if not self.doc_processor.load_vector_store():
            print("No existing vector store found. To use document retrieval:")
            print("1. Place .txt or .pdf files in ./data/advising_docs/")
            print("2. Or call chatbot.process_documents()")
        
        # Create the prompt template
        self.prompt_template = PromptTemplate(
            input_variables=["context", "question"],
            template=f"""{self.SYSTEM_PROMPT}

{{context}}

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
    
    def chat(self, user_message: str) -> Dict:
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
            retrieved_docs = self.doc_processor.retrieve_relevant_docs(user_message, k=3)
            response_data["retrieved_docs"] = [
                {
                    "content": doc.page_content,
                    "source": doc.metadata.get("source", "unknown")
                }
                for doc in retrieved_docs
            ]
            
            # Step 3: Build context from retrieved documents
            context = self._build_context(user_message, retrieved_docs)
            
            # Step 4: Generate response with LLM
            try:
                llm_response = self.chain.invoke({"context": context, "question": user_message})
                response_data["response"] = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
                response_data["source"] = "rag" if retrieved_docs else "llm"
                response_data["confidence"] = 0.7 if retrieved_docs else 0.5
            except Exception as e:
                response_data["response"] = f"Error generating response: {str(e)}"
                response_data["source"] = "error"
        
        # Add to conversation history
        self.conversation_history.append(response_data)
        
        return response_data
    
    def _build_context(self, query: str, documents: List) -> str:
        """
        Build context string from retrieved documents.
        
        Args:
            query: Original user query
            documents: Retrieved documents
            
        Returns:
            Formatted context string
        """
        if not documents:
            return "No relevant documents found.\n"
        
        context_parts = [f"Relevant Information:\n"]
        
        for i, doc in enumerate(documents, 1):
            source = doc.metadata.get("source", "unknown source")
            content = doc.page_content[:500]  # Limit content length
            context_parts.append(f"[Document {i} - {source}]\n{content}\n")
        
        return "\n".join(context_parts)
    
    def get_history(self) -> List[dict]:
        """Get the conversation history."""
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
