"""LLM Provider abstraction for multi-provider support."""

import os
from typing import Any, Optional

from langchain_core.language_models import LLM

# Student chat uses a low temperature and a hard output cap. Pass
# temperature=None to omit those controls: the benchmark does this so reasoning
# models can keep their own sampling defaults and are not cut off mid-answer.
_ADVISING_TEMPERATURE = object()
_DEFAULT_ADVISING_TEMPERATURE = 0.2
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 45.0
# 0 so the OpenAI SDK does not sleep for Retry-After on HTTP 429. Student
# chat fails over immediately (see BUSY_RESPONSE in rag_chatbot). Raise with
# LLM_MAX_RETRIES only if that wait is acceptable.
_DEFAULT_MAX_RETRIES = 0
_DEFAULT_MAX_TOKENS = 512
# Fallback retry for a reasoning deployment that rejects temperature and
# wants max_completion_tokens. Not the benchmark path: the timeout and the
# token cap still apply.
REASONING_RETRY = object()


def _env_float(name: str, default: float, low: float, high: float) -> float:
    """Read a float env var, falling back when it is missing or not a number."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        value = default
    else:
        try:
            value = float(str(raw).strip())
        except ValueError:
            value = default
    return float(min(high, max(low, value)))


def _env_int(name: str, default: int, low: int, high: int) -> int:
    """Read an int env var, falling back when it is missing or not an integer."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except ValueError:
            value = default
    return int(min(high, max(low, value)))


def advising_temperature() -> float:
    """Sampling temperature for factual advising answers."""
    return _env_float("LLM_TEMPERATURE", _DEFAULT_ADVISING_TEMPERATURE, 0.0, 2.0)


def chat_limit_kwargs() -> dict:
    """HTTP timeout, retry bound, and completion cap for the chat client.

    One attempt is capped at 45 seconds. ``max_retries`` defaults to 0 so a
    429 cannot sit inside the SDK until Retry-After expires (that wait has
    been about 30 seconds). ``LLM_MAX_TOKENS`` overrides the 512 completion
    cap. The cap stops a long completion from spending the student quota
    before the reply is trimmed for display.
    """
    return {
        "timeout": _env_float(
            "LLM_REQUEST_TIMEOUT",
            _DEFAULT_REQUEST_TIMEOUT_SECONDS,
            1.0,
            120.0,
        ),
        "max_retries": _env_int("LLM_MAX_RETRIES", _DEFAULT_MAX_RETRIES, 0, 5),
        "max_tokens": _env_int("LLM_MAX_TOKENS", _DEFAULT_MAX_TOKENS, 64, 4096),
    }


def _resolve_temperature(temperature: Any) -> Optional[float]:
    """Return a sampling temperature, or None when the caller wants it omitted."""
    if temperature is None:
        return None
    if temperature is _ADVISING_TEMPERATURE:
        return advising_temperature()
    return float(temperature)


def _apply_chat_controls(kwargs: dict, temperature: Any) -> dict:
    """Attach chat spend controls unless this is the benchmark path.

    ``temperature=None`` is the benchmark contract: omit temperature so a
    reasoning model can use its own default, and omit the chat timeout and
    token cap so those longer runs are not cut off.

    ``REASONING_RETRY`` is the student-chat fallback for a deployment that
    rejects ``temperature`` or ``max_tokens``. The timeout and token cap
    stay; temperature is omitted and the cap is sent as
    ``max_completion_tokens``.
    """
    if temperature is None:
        return kwargs
    if temperature is REASONING_RETRY:
        limits = chat_limit_kwargs()
        kwargs["timeout"] = limits["timeout"]
        kwargs["max_retries"] = limits["max_retries"]
        kwargs["max_completion_tokens"] = limits["max_tokens"]
        return kwargs
    kwargs["temperature"] = _resolve_temperature(temperature)
    kwargs.update(chat_limit_kwargs())
    return kwargs


def _model_field_names(llm_cls: Any) -> set:
    fields = getattr(llm_cls, "model_fields", None) or {}
    return set(fields)


try:
    from .config import Config, LLMProvider
except ImportError:  # pragma: no cover
    # Support running with `src/` on sys.path (legacy/tests).
    from config import Config, LLMProvider

# tracer imports - may fail if tracing is not configured
try:
    import langsmith  # noqa: F401

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
    # Anthropic's maintained integration lives outside langchain-community.
    from langchain_anthropic import ChatAnthropic
except ImportError:
    try:  # compatibility with older deployments
        from langchain_community.chat_models import ChatAnthropic
    except ImportError:
        ChatAnthropic = None

try:
    from langchain_huggingface import HuggingFaceEndpoint
except ImportError:
    try:  # compatibility with older deployments
        from langchain_community.llms import HuggingFaceHub as HuggingFaceEndpoint
    except ImportError:
        HuggingFaceEndpoint = None


class LLMFactory:
    """Factory for creating LLM instances from different providers."""

    @staticmethod
    def create_llm(
        provider: Optional[str] = None,
        *,
        model: Optional[str] = None,
        temperature: Any = _ADVISING_TEMPERATURE,
    ) -> LLM:
        """
        Create an LLM instance for the specified provider.

        Args:
            provider: LLM provider name (openai, azure_openai, anthropic,
                     huggingface, openai_compatible, deepseek, qwen, zhipu)
                     Defaults to config value
            model: Optional model or Azure deployment override. This is useful
                   for repeatable model evaluations without mutating Config.
            temperature: Sampling temperature for advising answers. The default
                         is low (0.2, or ``LLM_TEMPERATURE``) because answers
                         are factual. Pass None to omit temperature and the
                         chat timeout/token cap so benchmark reasoning models
                         can use their own defaults. Pass ``REASONING_RETRY``
                         to omit temperature but keep the timeout and send
                         the token cap as ``max_completion_tokens``.

        Returns:
            LLM instance

        Raises:
            ValueError: If provider is not available or not configured
        """
        provider = provider or Config.DEFAULT_LLM_PROVIDER
        provider = Config.normalize_provider(provider)

        if provider == "openai":
            return LLMFactory._create_openai(model=model, temperature=temperature)
        elif provider == "azure_openai":
            return LLMFactory._create_azure_openai(
                deployment=model, temperature=temperature
            )
        elif provider == "anthropic":
            return LLMFactory._create_anthropic(model=model, temperature=temperature)
        elif provider == "huggingface":
            return LLMFactory._create_huggingface(model=model, temperature=temperature)
        elif provider in {"openai_compatible", "deepseek", "qwen", "zhipu"}:
            return LLMFactory._create_openai_compatible(
                provider, model=model, temperature=temperature
            )
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    @staticmethod
    def _create_openai(
        model: Optional[str] = None, temperature: Any = _ADVISING_TEMPERATURE
    ) -> LLM:
        """Create OpenAI LLM."""
        if not Config._configured_secret(Config.OPENAI_API_KEY):
            raise ValueError("OPENAI_API_KEY not configured")
        if ChatOpenAI is None:
            raise ImportError(
                "langchain-openai not installed. Run: pip install langchain-openai"
            )

        kwargs = {
            "model": model or Config.OPENAI_MODEL,
            "api_key": Config.OPENAI_API_KEY,
        }
        return ChatOpenAI(**_apply_chat_controls(kwargs, temperature))

    @staticmethod
    def _create_azure_openai(
        deployment: Optional[str] = None, temperature: Any = _ADVISING_TEMPERATURE
    ) -> LLM:
        """Create Azure OpenAI LLM."""
        if not Config._configured_secret(Config.AZURE_OPENAI_API_KEY):
            raise ValueError("AZURE_OPENAI_API_KEY not configured")
        if not Config.AZURE_OPENAI_ENDPOINT:
            raise ValueError("AZURE_OPENAI_ENDPOINT not configured")
        if not Config.AZURE_OPENAI_DEPLOYMENT:
            raise ValueError("AZURE_OPENAI_DEPLOYMENT not configured")
        if AzureChatOpenAI is None:
            raise ImportError(
                "langchain-openai not installed. Run: pip install langchain-openai"
            )

        kwargs = dict(
            azure_deployment=deployment or Config.AZURE_OPENAI_DEPLOYMENT,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_API_KEY,
            api_version=Config.AZURE_OPENAI_API_VERSION,
        )
        return AzureChatOpenAI(**_apply_chat_controls(kwargs, temperature))

    @staticmethod
    def _create_anthropic(
        model: Optional[str] = None, temperature: Any = _ADVISING_TEMPERATURE
    ) -> LLM:
        """Create Anthropic Claude LLM."""
        if not Config._configured_secret(Config.ANTHROPIC_API_KEY):
            raise ValueError("ANTHROPIC_API_KEY not configured")
        if ChatAnthropic is None:
            raise ImportError("anthropic not installed. Run: pip install anthropic")

        kwargs = {
            "model": model or Config.ANTHROPIC_MODEL,
            "api_key": Config.ANTHROPIC_API_KEY,
        }
        return ChatAnthropic(**_apply_chat_controls(kwargs, temperature))

    @staticmethod
    def _create_huggingface(
        model: Optional[str] = None, temperature: Any = _ADVISING_TEMPERATURE
    ) -> LLM:
        """Create HuggingFace LLM."""
        if not Config._configured_secret(Config.HUGGINGFACE_API_KEY):
            raise ValueError("HUGGINGFACE_API_KEY not configured")
        if HuggingFaceEndpoint is None:
            raise ImportError(
                "langchain-huggingface not installed. Run: "
                "pip install langchain-huggingface"
            )

        return HuggingFaceEndpoint(
            **_huggingface_kwargs(
                repo_id=model or Config.HUGGINGFACE_MODEL,
                api_token=Config.HUGGINGFACE_API_KEY,
                temperature=temperature,
            )
        )

    @staticmethod
    def _create_openai_compatible(
        provider: str,
        model: Optional[str] = None,
        temperature: Any = _ADVISING_TEMPERATURE,
    ) -> LLM:
        """Create a ChatOpenAI client for an OpenAI-compatible endpoint.

        DeepSeek, Alibaba Cloud Model Studio/DashScope, Zhipu, and many
        self-hosted gateways expose the same Chat Completions contract. Using
        ``ChatOpenAI`` keeps the integration dependency-light and means the
        endpoint can be selected entirely through environment variables.
        """
        settings = Config.get_openai_compatible_config(provider)
        missing = [
            env_name
            for key, env_name in (
                ("api_key", settings["api_key_env"]),
                ("base_url", settings["base_url_env"]),
                ("model", settings["model_env"]),
            )
            if not settings[key]
            or (key == "api_key" and not Config._configured_secret(settings[key]))
        ]
        if missing:
            raise ValueError(f"{provider} is not configured; set {', '.join(missing)}")
        if ChatOpenAI is None:
            raise ImportError(
                "langchain-openai not installed. Run: pip install langchain-openai"
            )

        kwargs = dict(
            model=model or settings["model"],
            api_key=settings["api_key"],
            base_url=settings["base_url"],
        )
        return ChatOpenAI(**_apply_chat_controls(kwargs, temperature))

    @staticmethod
    def get_available_providers() -> list:
        """Get list of available and configured providers."""
        available = []

        for provider in LLMProvider:
            if Config.validate_provider(provider.value):
                available.append(provider.value)

        return available


def _huggingface_kwargs(*, repo_id: str, api_token: str, temperature: Any) -> dict:
    """Build Hugging Face kwargs for whichever wrapper is installed.

    The production image uses the community wrapper, which only accepts
    generation settings inside ``model_kwargs``. The optional
    ``langchain-huggingface`` class accepts ``max_new_tokens``,
    ``temperature``, and ``timeout`` as real fields and rejects them inside
    ``model_kwargs``. The chat token cap is applied as ``max_new_tokens``.
    """
    fields = _model_field_names(HuggingFaceEndpoint)
    resolved = _resolve_temperature(temperature)
    if temperature is None:
        token_cap = 512
        limits: dict = {}
    else:
        limits = chat_limit_kwargs()
        token_cap = limits["max_tokens"]

    kwargs: dict = {
        "repo_id": repo_id,
        "huggingfacehub_api_token": api_token,
    }
    generation: dict = {}
    if "max_new_tokens" in fields:
        kwargs["max_new_tokens"] = token_cap
    else:
        generation["max_new_tokens"] = token_cap
    if resolved is not None:
        if "temperature" in fields:
            kwargs["temperature"] = resolved
        else:
            generation["temperature"] = resolved
    if limits and "timeout" in fields:
        kwargs["timeout"] = int(limits["timeout"])
    if generation:
        kwargs["model_kwargs"] = generation
    return kwargs
