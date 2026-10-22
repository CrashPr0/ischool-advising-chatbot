"""Tests for the chatbot."""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import Config, LLMProvider, resolve_vector_store_path  # noqa: E402
import llm_providers  # noqa: E402
from llm_providers import LLMFactory  # noqa: E402


class TestConfig:
    """Test configuration module."""

    def test_config_loads(self):
        """Test that config loads without errors."""
        assert Config.DEFAULT_LLM_PROVIDER is not None
        assert Config.VECTOR_STORE_PATH is not None
        assert Config.VECTOR_COLLECTION_NAME is not None

    def test_vector_store_prefers_explicit_configuration(self, monkeypatch):
        monkeypatch.setattr(os.path, "isdir", lambda path: True)
        assert resolve_vector_store_path("/custom/index") == "/custom/index"

    def test_vector_store_uses_azure_persistent_index_when_available(self, monkeypatch):
        monkeypatch.setattr(
            os.path,
            "isdir",
            lambda path: path == "/home/site/wwwroot/data/chroma",
        )
        assert resolve_vector_store_path() == "/home/site/wwwroot/data/chroma"

    @pytest.mark.parametrize(
        "placeholder", ["your_api_key_here", "replace_with_a_secret", "changeme"]
    )
    def test_template_credentials_are_not_treated_as_configured(self, placeholder):
        assert Config._configured_secret(placeholder) is False
        assert Config._configured_secret("real-test-key") is True

    def test_llm_provider_enum(self):
        """Test LLM provider enum."""
        assert LLMProvider.OPENAI.value == "openai"
        assert LLMProvider.AZURE_OPENAI.value == "azure_openai"
        assert LLMProvider.ANTHROPIC.value == "anthropic"
        assert LLMProvider.HUGGINGFACE.value == "huggingface"
        assert LLMProvider.OPENAI_COMPATIBLE.value == "openai_compatible"
        assert LLMProvider.DEEPSEEK.value == "deepseek"
        assert LLMProvider.QWEN.value == "qwen"
        assert LLMProvider.ZHIPU.value == "zhipu"

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("openai-compatible", "openai_compatible"),
            ("openai_compat", "openai_compatible"),
            ("dashscope", "qwen"),
            ("qwen/dashscope", "qwen"),
            ("zai", "zhipu"),
            ("zhipuai", "zhipu"),
        ],
    )
    def test_provider_aliases(self, alias, canonical):
        """Common provider aliases resolve to one canonical name."""
        assert Config.normalize_provider(alias) == canonical

    @pytest.mark.parametrize(
        ("provider", "key_attr", "url_attr"),
        [
            (
                "openai_compatible",
                "OPENAI_COMPATIBLE_API_KEY",
                "OPENAI_COMPATIBLE_BASE_URL",
            ),
            ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
            ("qwen", "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"),
            ("zhipu", "ZHIPU_API_KEY", "ZHIPU_BASE_URL"),
        ],
    )
    def test_openai_compatible_validation(
        self, monkeypatch, provider, key_attr, url_attr
    ):
        """Compatible providers require both credentials and an endpoint."""
        monkeypatch.setattr(Config, key_attr, None)
        monkeypatch.setattr(Config, url_attr, None)
        # Qwen/Zhipu support aliases that are read as fallbacks.
        if provider == "qwen":
            monkeypatch.setattr(Config, "QWEN_API_KEY", None)
            monkeypatch.setattr(Config, "QWEN_BASE_URL", None)
        if provider == "zhipu":
            monkeypatch.setattr(Config, "ZAI_API_KEY", None)
        assert Config.validate_provider(provider) is False

        monkeypatch.setattr(Config, key_attr, "test-key")
        monkeypatch.setattr(Config, url_attr, "https://example.test/v1")
        assert Config.validate_provider(provider) is True


class TestLLMFactory:
    """Test LLM factory."""

    def test_get_available_providers(self):
        """Test getting available providers."""
        providers = LLMFactory.get_available_providers()
        assert isinstance(providers, list)
        # At least one provider should be configured for testing
        print(f"Available providers: {providers}")

    def test_openai_compatible_factory_passes_base_url(self, monkeypatch):
        """Factory creates ChatOpenAI without making a network request."""

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(llm_providers, "ChatOpenAI", FakeChatOpenAI)
        monkeypatch.setattr(Config, "DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setattr(Config, "DEEPSEEK_BASE_URL", "https://api.example.test")
        monkeypatch.setattr(Config, "DEEPSEEK_MODEL", "deepseek-test")

        llm = LLMFactory.create_llm("deepseek")

        assert llm.kwargs == {
            "model": "deepseek-test",
            "temperature": 0.2,
            "timeout": 45.0,
            "max_retries": 0,
            "max_tokens": 512,
            "api_key": "test-key",
            "base_url": "https://api.example.test",
        }

    def test_openai_compatible_factory_requires_configuration(self, monkeypatch):
        """Missing compatible endpoint settings produce an actionable error."""

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(llm_providers, "ChatOpenAI", FakeChatOpenAI)
        monkeypatch.setattr(Config, "OPENAI_COMPATIBLE_API_KEY", "test-key")
        monkeypatch.setattr(Config, "OPENAI_COMPATIBLE_BASE_URL", None)

        with pytest.raises(ValueError, match="OPENAI_COMPATIBLE_BASE_URL"):
            LLMFactory.create_llm("openai_compatible")

    def test_openai_factory_allows_model_override_and_default_temperature(
        self, monkeypatch
    ):
        """Benchmarks can select Luna without mutating global configuration."""

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(llm_providers, "ChatOpenAI", FakeChatOpenAI)
        monkeypatch.setattr(Config, "OPENAI_API_KEY", "test-key")

        llm = LLMFactory.create_llm("openai", model="gpt-5.6-luna", temperature=None)

        assert llm.kwargs == {
            "model": "gpt-5.6-luna",
            "api_key": "test-key",
        }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
