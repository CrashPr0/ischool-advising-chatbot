"""Tests for provider-neutral browser voice configuration."""

import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from voice_config import VoiceConfig, get_voice_config


class TestVoiceConfig:
    def test_defaults_include_english_and_mandarin(self, monkeypatch):
        for name in (
            "VOICE_ENABLED",
            "VOICE_MODE",
            "VOICE_STT_ENABLED",
            "VOICE_TTS_ENABLED",
            "VOICE_LANGUAGES",
            "VOICE_DEFAULT_LANGUAGE",
            "VOICE_AUTO_SEND",
            "VOICE_MAX_UTTERANCE_CHARS",
        ):
            monkeypatch.delenv(name, raising=False)

        config = get_voice_config()

        assert config.enabled is True
        assert config.mode == "browser"
        assert config.languages == ("en-US", "zh-CN")
        assert config.default_language == "en-US"
        assert config.auto_send is False

    def test_language_list_is_deduplicated_and_default_is_safe(self, monkeypatch):
        monkeypatch.setenv("VOICE_LANGUAGES", "zh-CN, en-US, zh-CN, es-MX")
        monkeypatch.setenv("VOICE_DEFAULT_LANGUAGE", "fr-FR")

        config = VoiceConfig.from_env()

        assert config.languages == ("zh-CN", "en-US", "es-MX")
        assert config.default_language == "zh-CN"
        payload = config.as_dict()
        assert payload["languages"][0] == {
            "code": "zh-CN",
            "label": "普通话（简体中文）",
        }

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
    def test_boolean_flags_accept_common_true_values(self, monkeypatch, value):
        monkeypatch.setenv("VOICE_ENABLED", value)
        monkeypatch.setenv("VOICE_AUTO_SEND", value)

        config = VoiceConfig.from_env()

        assert config.enabled is True
        assert config.auto_send is True

    def test_disabled_mode_wins_over_enabled_flag(self, monkeypatch):
        monkeypatch.setenv("VOICE_ENABLED", "true")
        monkeypatch.setenv("VOICE_MODE", "disabled")

        config = VoiceConfig.from_env()

        assert config.enabled is False
        assert config.mode == "disabled"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("not-a-number", 1000), ("5", 100), ("999999", 10000)],
    )
    def test_utterance_limit_is_bounded(self, monkeypatch, value, expected):
        monkeypatch.setenv("VOICE_MAX_UTTERANCE_CHARS", value)

        assert VoiceConfig.from_env().max_utterance_chars == expected
