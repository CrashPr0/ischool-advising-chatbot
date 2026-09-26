"""Compatibility import for the canonical package configuration.

The application lives under :mod:`src`; this module keeps legacy commands that
import ``config`` from the repository root working without maintaining a second
implementation.
"""

from src.config import Config, LLMProvider, resolve_vector_store_path
from src.durable_storage import (
    assess_index_readiness,
    prepare_eval_db_path,
    resolve_eval_db_path,
)

__all__ = [
    "Config",
    "LLMProvider",
    "assess_index_readiness",
    "prepare_eval_db_path",
    "resolve_eval_db_path",
    "resolve_vector_store_path",
]
