"""Configuration management for the iSchool Advising Chatbot."""
import os
from dotenv import load_dotenv
from enum import Enum

load_dotenv()


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    HUGGINGFACE = "huggingface"


class Config:
    """Application configuration."""
    
    # LLM Configuration
    DEFAULT_LLM_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "openai").lower()
    
    # OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

    # Azure OpenAI
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")
    
    # Anthropic
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-2")
    
    # HuggingFace
    HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")
    HUGGINGFACE_MODEL = os.getenv("HUGGINGFACE_MODEL", "meta-llama/Llama-2-7b-hf")
    
    # Application
    VECTOR_STORE_PATH = os.getenv("VECTOR_STORE_PATH", "./data/chroma")
    DOCS_PATH = os.getenv("DOCS_PATH", "").strip() or None
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    
    # LangSmith (Langchain tracing)
    LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
    LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT")
    LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
    LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT")
    
    @classmethod
    def validate_provider(cls, provider: str) -> bool:
        """Check if provider is available and configured."""
        provider = provider.lower()
        
        if provider == "openai":
            return bool(cls.OPENAI_API_KEY)
        elif provider == "azure_openai":
            return bool(
                cls.AZURE_OPENAI_API_KEY
                and cls.AZURE_OPENAI_ENDPOINT
                and cls.AZURE_OPENAI_DEPLOYMENT
            )
        elif provider == "anthropic":
            return bool(cls.ANTHROPIC_API_KEY)
        elif provider == "huggingface":
            return bool(cls.HUGGINGFACE_API_KEY)
        
        return False
