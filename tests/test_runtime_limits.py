"""Runtime limits for Gunicorn, the chat client, page-load spend, and pins."""

import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import llm_providers  # noqa: E402
from config import Config  # noqa: E402
from llm_providers import (  # noqa: E402
    LLMFactory,
    _ADVISING_TEMPERATURE,
    _huggingface_kwargs,
    chat_limit_kwargs,
)
from check_pip_audit import classify_advisories, main as audit_main  # noqa: E402


def _clear_llm_env(monkeypatch):
    for name in (
        "LLM_TEMPERATURE",
        "LLM_REQUEST_TIMEOUT",
        "LLM_MAX_RETRIES",
        "LLM_MAX_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)


class _FakeChat:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _load_gunicorn_config():
    spec = importlib.util.spec_from_file_location(
        f"gunicorn_app_config_{uuid.uuid4().hex}", ROOT / "gunicorn.conf.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chat_client_caps_temperature_timeout_retries_and_tokens(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm_providers, "ChatOpenAI", _FakeChat)
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "test-key")

    llm = LLMFactory.create_llm("openai")

    assert llm.kwargs["temperature"] == 0.2
    assert llm.kwargs["timeout"] == 45.0
    assert llm.kwargs["max_retries"] == 0
    assert llm.kwargs["max_tokens"] == 512


def test_azure_chat_client_uses_the_same_caps(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm_providers, "AzureChatOpenAI", _FakeChat)
    monkeypatch.setattr(Config, "AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        Config, "AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com"
    )
    monkeypatch.setattr(Config, "AZURE_OPENAI_DEPLOYMENT", "advising")
    monkeypatch.setattr(Config, "AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

    llm = LLMFactory.create_llm("azure_openai")

    assert llm.kwargs["azure_deployment"] == "advising"
    assert llm.kwargs["temperature"] == 0.2
    assert llm.kwargs["timeout"] == 45.0
    assert llm.kwargs["max_retries"] == 0
    assert llm.kwargs["max_tokens"] == 512


def test_benchmark_omits_temperature_and_chat_caps(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm_providers, "ChatOpenAI", _FakeChat)
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "test-key")

    llm = LLMFactory.create_llm("openai", model="gpt-5.6-luna", temperature=None)

    assert llm.kwargs == {"model": "gpt-5.6-luna", "api_key": "test-key"}


def test_explicit_temperature_still_gets_spend_caps(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm_providers, "ChatAnthropic", _FakeChat)
    monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "test-key")

    llm = LLMFactory.create_llm("anthropic", temperature=0.0)

    assert llm.kwargs["temperature"] == 0.0
    assert llm.kwargs["max_tokens"] == 512
    assert llm.kwargs["max_retries"] == 0


def test_env_overrides_are_clamped(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "9")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT", "0")
    monkeypatch.setenv("LLM_MAX_RETRIES", "nope")
    monkeypatch.setenv("LLM_MAX_TOKENS", "999999")
    monkeypatch.setattr(llm_providers, "ChatOpenAI", _FakeChat)
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "test-key")

    llm = LLMFactory.create_llm("openai", temperature=_ADVISING_TEMPERATURE)

    assert llm.kwargs["temperature"] == 2.0
    assert llm.kwargs["timeout"] == 1.0
    assert llm.kwargs["max_retries"] == 0
    assert llm.kwargs["max_tokens"] == 4096
    assert chat_limit_kwargs()["max_tokens"] == 4096


def test_huggingface_community_wrapper_puts_caps_in_model_kwargs(monkeypatch):
    _clear_llm_env(monkeypatch)

    class CommunityHub:
        model_fields = {"repo_id": None, "model_kwargs": None}

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(llm_providers, "HuggingFaceEndpoint", CommunityHub)
    monkeypatch.setattr(Config, "HUGGINGFACE_API_KEY", "test-key")

    llm = LLMFactory.create_llm("huggingface")

    assert llm.kwargs["model_kwargs"]["max_new_tokens"] == 512
    assert llm.kwargs["model_kwargs"]["temperature"] == 0.2
    assert "timeout" not in llm.kwargs
    assert "max_tokens" not in llm.kwargs


def test_huggingface_modern_wrapper_accepts_timeout_field(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(
        llm_providers,
        "HuggingFaceEndpoint",
        type(
            "ModernHub",
            (),
            {
                "model_fields": {
                    "repo_id": None,
                    "max_new_tokens": None,
                    "temperature": None,
                    "timeout": None,
                    "huggingfacehub_api_token": None,
                }
            },
        ),
    )

    kwargs = _huggingface_kwargs(
        repo_id="org/model", api_token="token", temperature=_ADVISING_TEMPERATURE
    )

    assert kwargs["max_new_tokens"] == 512
    assert kwargs["temperature"] == 0.2
    assert kwargs["timeout"] == 45
    assert "model_kwargs" not in kwargs


def test_huggingface_benchmark_keeps_a_generation_cap_only():
    kwargs = _huggingface_kwargs(
        repo_id="org/model", api_token="token", temperature=None
    )
    assert kwargs["model_kwargs"] == {"max_new_tokens": 512}


def test_gunicorn_defaults_match_the_shared_config(monkeypatch):
    for name in (
        "GUNICORN_WORKERS",
        "GUNICORN_THREADS",
        "GUNICORN_TIMEOUT",
        "GUNICORN_GRACEFUL_TIMEOUT",
        "PORT",
    ):
        monkeypatch.delenv(name, raising=False)

    config = _load_gunicorn_config()

    assert config.worker_class == "gthread"
    assert config.workers == 2
    assert config.threads == 4
    assert config.timeout == 180
    assert config.graceful_timeout == 210
    assert config.bind == "0.0.0.0:8000"
    assert config.graceful_timeout > config.timeout


def test_gunicorn_env_overrides_stay_in_range(monkeypatch):
    monkeypatch.setenv("PORT", "5000")
    monkeypatch.setenv("GUNICORN_WORKERS", "100")
    monkeypatch.setenv("GUNICORN_THREADS", "0")
    monkeypatch.setenv("GUNICORN_TIMEOUT", "200")
    monkeypatch.setenv("GUNICORN_GRACEFUL_TIMEOUT", "30")

    config = _load_gunicorn_config()

    assert config.bind == "0.0.0.0:5000"
    assert config.workers == 4
    assert config.threads == 1
    assert config.timeout == 200
    assert config.graceful_timeout == 200


def test_procfile_and_dockerfile_use_the_same_gunicorn_config():
    procfile = (ROOT / "Procfile").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert procfile.strip() == "web: gunicorn --config gunicorn.conf.py app:app"
    assert "COPY requirements.txt constraints.txt" in dockerfile
    assert "ENV PORT=5000" in dockerfile
    assert 'CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]' in dockerfile


def test_page_load_does_not_spend_a_chat_or_build_the_index():
    source = (ROOT / "static" / "script.js").read_text(encoding="utf-8")

    assert 'apiUrl("/api/status")' not in source
    assert "sendMessage(hint" not in source
    assert "silent" not in source
    assert "sendMessage(q)" in source
    assert 'apiUrl("/api/chat")' in source
    assert 'apiUrl("/api/voice-config")' in source
    assert "Got it! I'm now focused on" in source
    assert "setServiceStatus" in source


def test_dependencies_are_pinned_and_chroma_is_knowingly_unfixed():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    constraints = (ROOT / "constraints.txt").read_text(encoding="utf-8")
    requirement_lines = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert requirement_lines[0] == "-c constraints.txt"
    assert "chromadb==1.5.9" in requirement_lines
    assert "python-dotenv==1.2.3" in requirement_lines
    assert all(">=" not in line for line in requirement_lines)
    assert "chromadb==1.5.9" in constraints
    assert "python-dotenv==1.2.3" in constraints
    assert "PYSEC-2026-311" in requirements
    assert "no patched" in constraints.lower() or "no patched" in requirements.lower()


def test_audit_checker_fails_only_on_fixable_advisories(tmp_path):
    report = {
        "dependencies": [
            {
                "name": "chromadb",
                "version": "1.5.9",
                "vulns": [
                    {"id": "PYSEC-2026-311", "fix_versions": []},
                    {"id": "PYSEC-2026-311", "fix_versions": []},
                    {"id": "PYSEC-2026-3813", "fix_versions": []},
                ],
            },
            {
                "name": "python-dotenv",
                "version": "1.0.0",
                "vulns": [{"id": "PYSEC-2026-2270", "fix_versions": ["1.2.2"]}],
            },
        ]
    }
    fixable, unfixed = classify_advisories(report)
    assert len(unfixed) == 2
    assert len(fixable) == 1

    unfixed_only = tmp_path / "unfixed.json"
    unfixed_only.write_text(
        json.dumps(
            [
                {
                    "name": "chromadb",
                    "version": "1.5.9",
                    "vulns": report["dependencies"][0]["vulns"],
                }
            ]
        ),
        encoding="utf-8",
    )
    mixed = tmp_path / "mixed.json"
    mixed.write_text(json.dumps(report), encoding="utf-8")

    assert audit_main([str(unfixed_only)]) == 0
    assert audit_main([str(mixed)]) == 1
    assert audit_main([]) == 2


def test_chat_timeout_budget_fits_inside_the_worker_timeout(monkeypatch):
    """A fully retried chat call should finish before Gunicorn kills the worker."""
    _clear_llm_env(monkeypatch)
    for name in (
        "GUNICORN_WORKERS",
        "GUNICORN_THREADS",
        "GUNICORN_TIMEOUT",
        "GUNICORN_GRACEFUL_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    limits = chat_limit_kwargs()
    config = _load_gunicorn_config()
    worst_case = limits["timeout"] * (limits["max_retries"] + 1)
    assert worst_case < config.timeout
    assert config.timeout <= config.graceful_timeout


def test_reasoning_retry_omits_temperature_and_sends_completion_tokens(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm_providers, "AzureChatOpenAI", _FakeChat)
    monkeypatch.setattr(Config, "AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        Config, "AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com"
    )
    monkeypatch.setattr(Config, "AZURE_OPENAI_DEPLOYMENT", "advising")
    monkeypatch.setattr(Config, "AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

    llm = LLMFactory.create_llm(
        "azure_openai", model="o3-mini", temperature=llm_providers.REASONING_RETRY
    )

    assert llm.kwargs["azure_deployment"] == "o3-mini"
    assert "temperature" not in llm.kwargs
    assert "max_tokens" not in llm.kwargs
    assert llm.kwargs["max_completion_tokens"] == 512
    assert llm.kwargs["max_retries"] == 0
    assert llm.kwargs["timeout"] == 45.0


def test_real_azure_client_reasoning_retry_payload_drops_temperature(monkeypatch):
    """The installed client must send max_completion_tokens and no temperature."""
    pytest.importorskip("langchain_openai")
    from langchain_openai import AzureChatOpenAI

    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(llm_providers, "AzureChatOpenAI", AzureChatOpenAI)
    monkeypatch.setattr(Config, "AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        Config, "AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com"
    )
    monkeypatch.setattr(Config, "AZURE_OPENAI_DEPLOYMENT", "advising")
    monkeypatch.setattr(Config, "AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

    llm = LLMFactory.create_llm(
        "azure_openai", model="o3-mini", temperature=llm_providers.REASONING_RETRY
    )

    params = llm._default_params
    assert "temperature" not in params
    assert params.get("max_completion_tokens") == 512
    assert "max_tokens" not in params


def test_gunicorn_warm_start_is_a_noop_under_pytest():
    config = _load_gunicorn_config()
    started = []
    config._start_chatbot_warm_start = lambda: started.append(True)
    config.post_worker_init(object())
    assert started == []
    assert config._should_warm_start() is False


def test_warm_chatbot_calls_get_chatbot_and_swallows_errors(monkeypatch):
    import app as app_module

    config = _load_gunicorn_config()
    calls = []
    monkeypatch.setattr(app_module, "get_chatbot", lambda: calls.append("ok"))
    config._warm_chatbot()
    assert calls == ["ok"]

    def explode():
        raise RuntimeError("index down")

    monkeypatch.setattr(app_module, "get_chatbot", explode)
    config._warm_chatbot()
