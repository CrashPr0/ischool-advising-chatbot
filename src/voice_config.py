"""Configuration for the browser-native voice experience.

The default voice mode deliberately runs in the browser.  This keeps audio
out of the chatbot API and means a local checkout can try voice input/output
without a paid speech provider.  A future server-side STT/TTS adapter can
consume this same, provider-neutral configuration contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Dict, List, Tuple


DEFAULT_VOICE_LANGUAGES: Tuple[str, ...] = ("en-US", "zh-CN")

# These labels are only UI defaults.  A deployment can add any BCP-47 locale
# through VOICE_LANGUAGES even when it is not listed here.
VOICE_LANGUAGE_LABELS = {
    "en": "English",
    "en-us": "English (US)",
    "en-gb": "English (UK)",
    "zh": "中文",
    "zh-cn": "普通话（简体中文）",
    "zh-tw": "中文（繁體）",
    "es": "Español",
    "es-es": "Español (España)",
    "es-mx": "Español (México)",
}


def _env_bool(name: str, default: bool) -> bool:
    """Read a conventional boolean environment variable safely."""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_languages(raw: str | None) -> Tuple[str, ...]:
    """Parse a comma-separated locale list, preserving order and uniqueness."""

    if not raw:
        return DEFAULT_VOICE_LANGUAGES

    languages: List[str] = []
    for item in raw.split(","):
        locale = item.strip()
        if locale and locale.lower() not in {lang.lower() for lang in languages}:
            languages.append(locale)

    return tuple(languages) or DEFAULT_VOICE_LANGUAGES


def _language_label(locale: str) -> str:
    """Return a readable label while retaining support for arbitrary locales."""

    normalized = locale.lower()
    return VOICE_LANGUAGE_LABELS.get(
        normalized,
        VOICE_LANGUAGE_LABELS.get(normalized.split("-", 1)[0], locale),
    )


@dataclass(frozen=True)
class VoiceConfig:
    """Provider-neutral voice settings exposed to the web client."""

    enabled: bool = True
    mode: str = "browser"
    stt_enabled: bool = True
    tts_enabled: bool = True
    default_language: str = "en-US"
    languages: Tuple[str, ...] = DEFAULT_VOICE_LANGUAGES
    auto_send: bool = False
    max_utterance_chars: int = 1000

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        """Build a config snapshot from environment variables.

        ``VOICE_MODE`` is descriptive for now.  ``browser`` is the only
        built-in mode; unknown values fail closed to browser rather than
        causing a UI error.  The frontend still checks its own capabilities.
        """

        languages = _parse_languages(os.getenv("VOICE_LANGUAGES"))
        requested_default = os.getenv("VOICE_DEFAULT_LANGUAGE", languages[0]).strip()
        default_language = next(
            (locale for locale in languages if locale.lower() == requested_default.lower()),
            languages[0],
        )

        try:
            max_utterance_chars = int(os.getenv("VOICE_MAX_UTTERANCE_CHARS", "1000"))
        except (TypeError, ValueError):
            max_utterance_chars = 1000
        max_utterance_chars = max(100, min(max_utterance_chars, 10000))

        requested_mode = os.getenv("VOICE_MODE", "browser").strip().lower()
        mode = requested_mode if requested_mode in {"browser", "disabled"} else "browser"
        enabled = _env_bool("VOICE_ENABLED", True) and mode != "disabled"

        return cls(
            enabled=enabled,
            mode=mode,
            stt_enabled=_env_bool("VOICE_STT_ENABLED", True),
            tts_enabled=_env_bool("VOICE_TTS_ENABLED", True),
            default_language=default_language,
            languages=languages,
            auto_send=_env_bool("VOICE_AUTO_SEND", False),
            max_utterance_chars=max_utterance_chars,
        )

    def as_dict(self) -> Dict[str, Any]:
        """Serialize settings without exposing provider credentials."""

        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "stt_enabled": self.stt_enabled,
            "tts_enabled": self.tts_enabled,
            "default_language": self.default_language,
            "languages": [
                {"code": locale, "label": _language_label(locale)}
                for locale in self.languages
            ],
            "auto_send": self.auto_send,
            "max_utterance_chars": self.max_utterance_chars,
        }


def get_voice_config() -> VoiceConfig:
    """Return the current voice configuration.

    A function (rather than a module-level singleton) makes environment
    changes visible to tests and to long-running development processes.
    """

    return VoiceConfig.from_env()


__all__ = ["VoiceConfig", "get_voice_config"]
