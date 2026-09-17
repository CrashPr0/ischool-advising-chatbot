"""Build the persistent advising-document index from the command line.

This module is intentionally offline: documents are read from a local
directory and the resulting Chroma collection is replaced deterministically.
Use ``--clean`` only when an operator wants to remove the complete persisted
vector-store directory before rebuilding.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

try:
    from .config import Config
    from .document_processor import DocumentProcessor
except ImportError:  # pragma: no cover - supports ``src/`` on sys.path
    from config import Config
    from document_processor import DocumentProcessor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docs",
        default=None,
        help="Document root (defaults to DOCS_PATH or data/advising_docs)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the persisted vector-store directory before rebuilding",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    persist_directory = Path(Config.VECTOR_STORE_PATH).expanduser()

    if args.clean and persist_directory.exists():
        if not persist_directory.is_dir():
            raise RuntimeError(f"Vector-store path is not a directory: {persist_directory}")
        shutil.rmtree(persist_directory)

    processor = DocumentProcessor(args.docs)
    vector_store = processor.process_and_index()
    if vector_store is None:
        print("No documents were indexed.")
        return 1

    print(f"Indexed {processor.vector_store_count() or 0} chunks.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

