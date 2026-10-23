"""Exercise the index CLI's filesystem boundaries and reported results."""

from unittest.mock import Mock

import pytest

from src import build_index
from src.document_processor import DocumentProcessor


@pytest.fixture
def index_path(monkeypatch, tmp_path):
    path = tmp_path / "chroma"
    monkeypatch.setattr(build_index.Config, "VECTOR_STORE_PATH", str(path))
    return path


@pytest.mark.parametrize("explicit_docs", [False, True])
def test_cli_indexes_the_selected_local_corpus(
    monkeypatch, tmp_path, index_path, capsys, explicit_docs
):
    configured = tmp_path / "configured"
    override = tmp_path / "override"
    for directory, text in [
        (configured, "Scholarship applications close in April."),
        (override, "Registration opens in August."),
    ]:
        directory.mkdir()
        (directory / "advising.txt").write_text(text, encoding="utf-8")
    monkeypatch.setattr(build_index.Config, "DOCS_PATH", str(configured))
    monkeypatch.setattr(build_index.Config, "EMBEDDING_PROVIDER", "hashing")

    args = ["--docs", str(override)] if explicit_docs else []
    assert build_index.main(args) == 0
    assert "Indexed 1 chunks." in capsys.readouterr().out

    # Reopen the persisted index to verify usable evidence, not just a message.
    reader = DocumentProcessor(str(configured))
    assert reader.load_vector_store() is not None
    hits = reader.retrieve_relevant_docs("advising dates", k=5)
    expected = (
        "Registration opens in August."
        if explicit_docs
        else "Scholarship applications close in April."
    )
    assert [hit.page_content for hit in hits] == [expected]


def test_empty_corpus_returns_failure_without_creating_an_index(
    monkeypatch, tmp_path, index_path, capsys
):
    docs = tmp_path / "empty"
    docs.mkdir()
    monkeypatch.setattr(build_index.Config, "EMBEDDING_PROVIDER", "hashing")

    assert build_index.main(["--docs", str(docs)]) == 1
    assert "No documents were indexed." in capsys.readouterr().out
    assert not index_path.exists()


@pytest.mark.parametrize("clean", [False, True], ids=["preserve", "clean"])
def test_cleanup_is_opt_in_and_only_removes_the_configured_store(
    monkeypatch, tmp_path, index_path, clean
):
    index_path.mkdir()
    nested = index_path / "old-collection"
    nested.mkdir()
    marker = nested / "vectors.bin"
    marker.write_bytes(b"old index")
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "advising.txt"
    source.write_text("Official advising evidence.", encoding="utf-8")
    sibling = tmp_path / "reviews.sqlite3"
    sibling.write_bytes(b"unrelated storage")
    processor = Mock()
    processor.vector_store_count.return_value = 4

    def construct(selected_docs):
        assert selected_docs == str(docs)
        # Cleanup must finish before the processor opens the store.
        assert index_path.exists() is (not clean)
        return processor

    monkeypatch.setattr(build_index, "DocumentProcessor", construct)
    args = ["--docs", str(docs)] + (["--clean"] if clean else [])
    assert build_index.main(args) == 0
    processor.process_and_index.assert_called_once_with()
    assert marker.exists() is (not clean)
    assert source.read_text(encoding="utf-8") == "Official advising evidence."
    assert sibling.read_bytes() == b"unrelated storage"


def test_clean_accepts_a_missing_store_and_reports_an_unknown_count_as_zero(
    monkeypatch, index_path, capsys
):
    processor = Mock()
    processor.vector_store_count.return_value = None
    factory = Mock(return_value=processor)
    monkeypatch.setattr(build_index, "DocumentProcessor", factory)

    assert build_index.main(["--clean"]) == 0
    factory.assert_called_once_with(None)
    processor.process_and_index.assert_called_once_with()
    assert "Indexed 0 chunks." in capsys.readouterr().out


def test_clean_refuses_a_file_without_deleting_it_or_starting_ingestion(
    monkeypatch, index_path
):
    index_path.write_bytes(b"not a vector-store directory")
    factory = Mock()
    monkeypatch.setattr(build_index, "DocumentProcessor", factory)

    with pytest.raises(RuntimeError, match="path is not a directory"):
        build_index.main(["--clean"])

    assert index_path.read_bytes() == b"not a vector-store directory"
    factory.assert_not_called()


def test_indexing_error_propagates_without_printing_success(
    monkeypatch, index_path, capsys
):
    processor = Mock()
    processor.process_and_index.side_effect = RuntimeError("embedding offline")
    factory = Mock(return_value=processor)
    monkeypatch.setattr(build_index, "DocumentProcessor", factory)

    with pytest.raises(RuntimeError, match="embedding offline"):
        build_index.main([])

    processor.vector_store_count.assert_not_called()
    assert "Indexed" not in capsys.readouterr().out


def test_invalid_arguments_leave_an_existing_store_untouched(
    monkeypatch, index_path, capsys
):
    index_path.mkdir()
    marker = index_path / "keep.bin"
    marker.write_bytes(b"existing index")
    factory = Mock()
    monkeypatch.setattr(build_index, "DocumentProcessor", factory)

    with pytest.raises(SystemExit) as error:
        build_index.main(["--clean", "--docs"])

    assert error.value.code == 2
    assert "expected one argument" in capsys.readouterr().err
    assert marker.read_bytes() == b"existing index"
    factory.assert_not_called()
