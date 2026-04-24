"""LLM Provider abstraction for multi-provider support."""
from typing import Optional
from langchain_core.language_models import LLM
try:
    from .config import Config, LLMProvider
except ImportError:  # pragma: no cover
    # Support running with `src/` on sys.path (legacy/tests).
    from config import Config, LLMProvider

# tracer imports - may fail if tracing is not configured
try:
    from langsmith import Client
    LANGSMITH_AVAILABLE = True
except ImportError:
    LANGSMITH_AVAILABLE = False


def _get_callbacks():
    """Return callbacks list with LangSmith tracing if enabled."""
    callbacks = []
    if Config.LANGSMITH_TRACING and LANGSMITH_AVAILABLE and Config.LANGSMITH_API_KEY:
        # LangSmith integration is handled through environment variables
        # Set LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY to enable
        pass
    return callbacks

try:
    from langchain_openai import ChatOpenAI, AzureChatOpenAI
except ImportError:
    ChatOpenAI = None
    AzureChatOpenAI = None

try:
    from langchain_community.chat_models import ChatAnthropic
except ImportError:
    ChatAnthropic = None

try:
    from langchain_community.llms import HuggingFaceHub
except ImportError:
    HuggingFaceHub = None


class LLMFactory:
    """Factory for creating LLM instances from different providers."""
    
    @staticmethod
    def create_llm(provider: Optional[str] = None) -> LLM:
        """
        Create an LLM instance for the specified provider.
        
        Args:
            provider: LLM provider name (openai, azure_openai, anthropic, huggingface)
                     Defaults to config value
        
        Returns:
            LLM instance
            
        Raises:
            ValueError: If provider is not available or not configured
        """
        provider = provider or Config.DEFAULT_LLM_PROVIDER
        provider = provider.lower()
        
        if provider == "openai":
            return LLMFactory._create_openai()
        elif provider == "azure_openai":
            return LLMFactory._create_azure_openai()
        elif provider == "anthropic":
            return LLMFactory._create_anthropic()
        elif provider == "huggingface":
            return LLMFactory._create_huggingface()
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")
    
    @staticmethod
    def _create_openai() -> LLM:
        """Create OpenAI LLM."""
        if not Config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not configured")
        if ChatOpenAI is None:
            raise ImportError("langchain-openai not installed. Run: pip install langchain-openai")
        
        return ChatOpenAI(
            model=Config.OPENAI_MODEL,
            temperature=0.7,
            api_key=Config.OPENAI_API_KEY
        )

    @staticmethod
    def _create_azure_openai() -> LLM:
        """Create Azure OpenAI LLM."""
        if not Config.AZURE_OPENAI_API_KEY:
            raise ValueError("AZURE_OPENAI_API_KEY not configured")
        if not Config.AZURE_OPENAI_ENDPOINT:
            raise ValueError("AZURE_OPENAI_ENDPOINT not configured")
        if not Config.AZURE_OPENAI_DEPLOYMENT:
            raise ValueError("AZURE_OPENAI_DEPLOYMENT not configured")
        if AzureChatOpenAI is None:
            raise ImportError("langchain-openai not installed. Run: pip install langchain-openai")

        return AzureChatOpenAI(
            azure_deployment=Config.AZURE_OPENAI_DEPLOYMENT,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_API_KEY,
            api_version=Config.AZURE_OPENAI_API_VERSION,
            temperature=0.7
        )
    
    @staticmethod
    def _create_anthropic() -> LLM:
        """Create Anthropic Claude LLM."""
        if not Config.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        if ChatAnthropic is None:
            raise ImportError("anthropic not installed. Run: pip install anthropic")
        
        return ChatAnthropic(
            model=Config.ANTHROPIC_MODEL,
            temperature=0.7,
            api_key=Config.ANTHROPIC_API_KEY
        )
    
    @staticmethod
    def _create_huggingface() -> LLM:
        """Create HuggingFace LLM."""
        if not Config.HUGGINGFACE_API_KEY:
            raise ValueError("HUGGINGFACE_API_KEY not configured")
        if HuggingFaceHub is None:
            raise ImportError("langchain-community not installed. Run: pip install langchain-community")
        
        return HuggingFaceHub(
            repo_id=Config.HUGGINGFACE_MODEL,
            huggingfacehub_api_token=Config.HUGGINGFACE_API_KEY,
            model_kwargs={"temperature": 0.7, "max_length": 512}
        )
    
    @staticmethod
    def get_available_providers() -> list:
        """Get list of available and configured providers."""
        available = []
        
        for provider in LLMProvider:
            if Config.validate_provider(provider.value):
                available.append(provider.value)
        
        return available
