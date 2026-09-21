"""Configuration management for the iSchool Advising Chatbot."""

import os
from dotenv import load_dotenv
from enum import Enum

load_dotenv()


def resolve_vector_store_path(configured_path: str | None = None) -> str:
    """Use an explicit path, then Azure's persistent index, then local data."""
    explicit = (configured_path or "").strip()
    if explicit:
        return explicit
    azure_path = "/home/site/wwwroot/data/chroma"
    if os.path.isdir(azure_path):
        return azure_path
    return "./data/chroma"


class LLMProvider(str, Enum):
    """Supported LLM providers."""

    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    HUGGINGFACE = "huggingface"
    # Providers below expose an OpenAI-compatible Chat Completions API.  They
    # intentionally use the existing ``langchain-openai`` dependency rather
    # than adding one SDK per provider.
    OPENAI_COMPATIBLE = "openai_compatible"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    ZHIPU = "zhipu"


class Config:
    """Application configuration."""

    # LLM Configuration
    DEFAULT_LLM_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "openai").lower()

    # OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")

    # Azure OpenAI
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    AZURE_OPENAI_API_VERSION = os.getenv(
        "AZURE_OPENAI_API_VERSION", "2024-02-15-preview"
    )
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.getenv(
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", ""
    )

    # Indexing is offline and quota-free by default. Operators can explicitly
    # choose Azure/OpenAI embeddings when semantic recall justifies the cost.
    EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "hashing").strip().lower()

    # Anthropic
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-2")

    # HuggingFace
    HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")
    HUGGINGFACE_MODEL = os.getenv("HUGGINGFACE_MODEL", "meta-llama/Llama-2-7b-hf")

    # OpenAI-compatible providers
    #
    # ``OPENAI_COMPATIBLE_*`` is the generic escape hatch for any service that
    # implements the OpenAI Chat Completions contract (including self-hosted
    # gateways). Provider-specific aliases make the common Chinese services
    # easy to configure while keeping credentials in environment variables.
    OPENAI_COMPATIBLE_API_KEY = os.getenv("OPENAI_COMPATIBLE_API_KEY")
    OPENAI_COMPATIBLE_BASE_URL = os.getenv("OPENAI_COMPATIBLE_BASE_URL")
    OPENAI_COMPATIBLE_MODEL = os.getenv("OPENAI_COMPATIBLE_MODEL", "gpt-4o-mini")

    # DeepSeek (official OpenAI-compatible endpoint; do not append /v1).
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    # Alibaba Cloud Model Studio / DashScope (Qwen).  Accept QWEN_* aliases
    # for convenience, while DASHSCOPE_* matches the provider's docs.
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
    QWEN_API_KEY = DASHSCOPE_API_KEY
    DASHSCOPE_BASE_URL = (
        os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("QWEN_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    QWEN_BASE_URL = DASHSCOPE_BASE_URL
    QWEN_MODEL = os.getenv("QWEN_MODEL") or os.getenv("DASHSCOPE_MODEL", "qwen-plus")
    DASHSCOPE_MODEL = QWEN_MODEL

    # Zhipu (Z.ai / GLM).  ZAI_API_KEY is the name used in the provider docs;
    # ZHIPU_API_KEY remains a supported alias.
    ZHIPU_API_KEY = os.getenv("ZAI_API_KEY") or os.getenv("ZHIPU_API_KEY")
    ZAI_API_KEY = ZHIPU_API_KEY
    ZHIPU_BASE_URL = os.getenv(
        "ZHIPU_BASE_URL",
        os.getenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    )
    ZAI_BASE_URL = ZHIPU_BASE_URL
    ZHIPU_MODEL = os.getenv("ZHIPU_MODEL") or os.getenv("ZAI_MODEL", "glm-5.2")
    ZAI_MODEL = ZHIPU_MODEL

    # Application
    VECTOR_STORE_PATH = resolve_vector_store_path(os.getenv("VECTOR_STORE_PATH"))
    VECTOR_COLLECTION_NAME = os.getenv("VECTOR_COLLECTION_NAME", "advising_documents")
    DOCS_PATH = os.getenv("DOCS_PATH", "").strip() or None
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"

    # LangSmith (Langchain tracing)
    LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
    LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT")
    LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
    LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT")

    _PROVIDER_ALIASES = {
        "openai-compatible": "openai_compatible",
        "openai_compat": "openai_compatible",
        "dashscope": "qwen",
        "qwen/dashscope": "qwen",
        "zai": "zhipu",
        "zhipuai": "zhipu",
    }

    @staticmethod
    def _configured_secret(value: str | None) -> bool:
        """Return false for empty or copied-template credential values."""
        normalized = (value or "").strip().lower()
        return bool(normalized) and not normalized.startswith(
            ("replace_with_", "your_", "change_me", "changeme")
        )

    @classmethod
    def normalize_provider(cls, provider: str) -> str:
        """Return a canonical provider name, accepting common aliases."""

        if isinstance(provider, LLMProvider):
            provider = provider.value
        normalized = str(provider).strip().lower()
        return cls._PROVIDER_ALIASES.get(normalized, normalized)

    @classmethod
    def get_openai_compatible_config(cls, provider: str) -> dict:
        """Return connection settings for an OpenAI-compatible provider.

        The mapping is built on every call so applications and tests can
        override ``Config`` attributes without having to reload this module.
        ``api_key_env`` and ``base_url_env`` are included for useful error
        messages and never contain secret values.
        """

        provider = cls.normalize_provider(provider)
        configurations = {
            "openai_compatible": {
                "api_key": cls.OPENAI_COMPATIBLE_API_KEY,
                "base_url": cls.OPENAI_COMPATIBLE_BASE_URL,
                "model": cls.OPENAI_COMPATIBLE_MODEL,
                "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
                "base_url_env": "OPENAI_COMPATIBLE_BASE_URL",
                "model_env": "OPENAI_COMPATIBLE_MODEL",
            },
            "deepseek": {
                "api_key": cls.DEEPSEEK_API_KEY,
                "base_url": cls.DEEPSEEK_BASE_URL,
                "model": cls.DEEPSEEK_MODEL,
                "api_key_env": "DEEPSEEK_API_KEY",
                "base_url_env": "DEEPSEEK_BASE_URL",
                "model_env": "DEEPSEEK_MODEL",
            },
            "qwen": {
                "api_key": cls.DASHSCOPE_API_KEY or cls.QWEN_API_KEY,
                "base_url": cls.DASHSCOPE_BASE_URL or cls.QWEN_BASE_URL,
                "model": cls.QWEN_MODEL,
                "api_key_env": "DASHSCOPE_API_KEY (or QWEN_API_KEY)",
                "base_url_env": "DASHSCOPE_BASE_URL (or QWEN_BASE_URL)",
                "model_env": "QWEN_MODEL",
            },
            "zhipu": {
                "api_key": cls.ZHIPU_API_KEY or cls.ZAI_API_KEY,
                "base_url": cls.ZHIPU_BASE_URL or cls.ZAI_BASE_URL,
                "model": cls.ZHIPU_MODEL or cls.ZAI_MODEL,
                "api_key_env": "ZAI_API_KEY (or ZHIPU_API_KEY)",
                "base_url_env": "ZHIPU_BASE_URL",
                "model_env": "ZHIPU_MODEL",
            },
        }

        try:
            return configurations[provider]
        except KeyError as exc:
            raise ValueError(f"Provider is not OpenAI-compatible: {provider}") from exc

    @classmethod
    def validate_provider(cls, provider: str) -> bool:
        """Check if provider is available and configured."""
        provider = cls.normalize_provider(provider)

        if provider == "openai":
            return cls._configured_secret(cls.OPENAI_API_KEY)
        elif provider == "azure_openai":
            return bool(
                cls._configured_secret(cls.AZURE_OPENAI_API_KEY)
                and cls.AZURE_OPENAI_ENDPOINT
                and cls.AZURE_OPENAI_DEPLOYMENT
            )
        elif provider == "anthropic":
            return cls._configured_secret(cls.ANTHROPIC_API_KEY)
        elif provider == "huggingface":
            return cls._configured_secret(cls.HUGGINGFACE_API_KEY)
        elif provider in {"openai_compatible", "deepseek", "qwen", "zhipu"}:
            settings = cls.get_openai_compatible_config(provider)
            return bool(
                cls._configured_secret(settings["api_key"])
                and settings["base_url"]
                and settings["model"]
            )

        return False
