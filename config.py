"""Compatibility import for the canonical package configuration.

The application lives under :mod:`src`; this module keeps legacy commands that
import ``config`` from the repository root working without maintaining a second
implementation.
"""

from src.config import Config, LLMProvider

__all__ = ["Config", "LLMProvider"]
