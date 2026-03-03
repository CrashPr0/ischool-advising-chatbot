"""iSchool Advising Chatbot using Langchain."""
from typing import List, Optional
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from llm_providers import LLMFactory
from config import Config


class AdvisingChatbot:
    """Chatbot for iSchool advising support."""
    
    # System prompt for the chatbot
    SYSTEM_PROMPT = """You are a helpful iSchool Senior Advising Assistant. 
Your role is to help students with questions about:
- Program requirements and courses
- Graduation requirements
- Career guidance
- Course scheduling
- Academic policies

Always be professional, helpful, and accurate. If you don't know something, 
be honest and suggest the student speak with an official advisor."""
    
    def __init__(self, llm_provider: Optional[str] = None):
        """
        Initialize the chatbot.
        
        Args:
            llm_provider: LLM provider to use (openai, anthropic, huggingface)
        """
        self.llm = LLMFactory.create_llm(llm_provider)
        self.conversation_history: List[dict] = []
        
        # Create the prompt template
        self.prompt_template = PromptTemplate(
            input_variables=["context", "question"],
            template=f"""{self.SYSTEM_PROMPT}

Context: {{context}}

Student Question: {{question}}

Assistant Response:"""
        )
        
        # Create the conversation chain
        self.chain = LLMChain(llm=self.llm, prompt=self.prompt_template)
    
    def chat(self, user_message: str, context: str = "") -> str:
        """
        Send a message and get a response.
        
        Args:
            user_message: The student's question
            context: Additional context (e.g., from retrieved documents)
        
        Returns:
            The chatbot's response
        """
        # Run the chain
        response = self.chain.run(context=context, question=user_message)
        
        # Store in conversation history
        self.conversation_history.append({
            "user": user_message,
            "assistant": response,
            "context": context
        })
        
        return response
    
    def get_history(self) -> List[dict]:
        """Get the conversation history."""
        return self.conversation_history
    
    def clear_history(self) -> None:
        """Clear the conversation history."""
        self.conversation_history = []
    
    @staticmethod
    def get_available_providers() -> List[str]:
        """Get available LLM providers."""
        return LLMFactory.get_available_providers()
