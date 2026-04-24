"""Tests for the chatbot."""
import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config import Config, LLMProvider
from llm_providers import LLMFactory


class TestConfig:
    """Test configuration module."""
    
    def test_config_loads(self):
        """Test that config loads without errors."""
        assert Config.DEFAULT_LLM_PROVIDER is not None
        assert Config.VECTOR_STORE_PATH is not None
    
    def test_llm_provider_enum(self):
        """Test LLM provider enum."""
        assert LLMProvider.OPENAI.value == "openai"
        assert LLMProvider.AZURE_OPENAI.value == "azure_openai"
        assert LLMProvider.ANTHROPIC.value == "anthropic"
        assert LLMProvider.HUGGINGFACE.value == "huggingface"


class TestLLMFactory:
    """Test LLM factory."""
    
    def test_get_available_providers(self):
        """Test getting available providers."""
        providers = LLMFactory.get_available_providers()
        assert isinstance(providers, list)
        # At least one provider should be configured for testing
        print(f"Available providers: {providers}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
