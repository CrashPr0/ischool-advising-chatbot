"""Compatibility imports for the canonical package document processor."""

from src.document_processor import (
    DOCS_DIRECTORIES,
    DocumentProcessor,
    setup_sample_docs,
)

__all__ = ["DocumentProcessor", "setup_sample_docs", "DOCS_DIRECTORIES"]
