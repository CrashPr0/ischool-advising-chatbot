"""Index rebuilds preserve existing files unless cleanup is requested."""

import pytest

from src import build_index


@pytest.fixture
def index_run(monkeypatch, tmp_path):
    store = tmp_path / "vectors"
    monkeypatch.setattr(build_index.Config, "VECTOR_STORE_PATH", str(store))
    calls = []

    class Processor:
        def __init__(self, docs):
            calls.append(("open", docs, store.exists()))

        def process_and_index(self):
            calls.append(("index",))
            return object()

        def vector_store_count(self):
            calls.append(("count",))
            return 7

    monkeypatch.setattr(build_index, "DocumentProcessor", Processor)
    return store, calls, Processor


def test_default_rebuild_preserves_store_and_uses_configured_docs(index_run, capsys):
    store, calls, _ = index_run
    store.mkdir()
    existing = store / "existing.sqlite3"
    existing.write_bytes(b"existing index")

    assert build_index.main([]) == 0

    assert calls == [("open", None, True), ("index",), ("count",)]
    assert existing.read_bytes() == b"existing index"
    assert capsys.readouterr().out == "Indexed 7 chunks.\n"


def test_clean_removes_only_vector_store_before_opening_processor(index_run, capsys):
    store, calls, _ = index_run
    nested = store / "nested"
    nested.mkdir(parents=True)
    (nested / "old.sqlite3").write_bytes(b"old index")
    docs = store.parent / "advising docs"
    docs.mkdir()
    source = docs / "advising.txt"
    source.write_text("Course requirements", encoding="utf-8")

    assert build_index.main(["--clean", "--docs", str(docs)]) == 0

    assert calls == [("open", str(docs), False), ("index",), ("count",)]
    assert not store.exists()
    assert source.read_text(encoding="utf-8") == "Course requirements"
    assert capsys.readouterr().out == "Indexed 7 chunks.\n"


def test_clean_allows_first_build_without_an_existing_store(index_run):
    store, calls, _ = index_run

    assert build_index.main(["--clean"]) == 0

    assert calls == [("open", None, False), ("index",), ("count",)]
    assert not store.exists()


def test_clean_refuses_a_file_without_deleting_it_or_starting_ingestion(index_run):
    store, calls, _ = index_run
    store.write_bytes(b"keep this file")

    with pytest.raises(RuntimeError, match="Vector-store path is not a directory"):
        build_index.main(["--clean"])

    assert store.read_bytes() == b"keep this file"
    assert calls == []


def test_empty_document_set_returns_failure_without_querying_count(
    index_run, monkeypatch, capsys
):
    _, calls, processor = index_run
    monkeypatch.setattr(processor, "process_and_index", lambda self: None)

    assert build_index.main(["--docs", "empty-docs"]) == 1

    assert calls == [("open", "empty-docs", False)]
    assert capsys.readouterr().out == "No documents were indexed.\n"


def test_missing_count_is_reported_as_zero(index_run, monkeypatch, capsys):
    _, _, processor = index_run
    monkeypatch.setattr(processor, "vector_store_count", lambda self: None)

    assert build_index.main([]) == 0

    assert capsys.readouterr().out == "Indexed 0 chunks.\n"


def test_ingestion_error_propagates_without_success_message(
    index_run, monkeypatch, capsys
):
    store, calls, processor = index_run
    store.mkdir()
    existing = store / "old.sqlite3"
    existing.write_bytes(b"old index")

    def fail_index(self):
        raise OSError("cannot write index")

    monkeypatch.setattr(processor, "process_and_index", fail_index)

    with pytest.raises(OSError, match="cannot write index"):
        build_index.main([])

    assert calls == [("open", None, True)]
    assert existing.read_bytes() == b"old index"
    assert capsys.readouterr().out == ""


def test_invalid_option_exits_before_touching_store(index_run, capsys):
    store, calls, _ = index_run
    store.mkdir()
    existing = store / "old.sqlite3"
    existing.write_bytes(b"old index")

    with pytest.raises(SystemExit) as error:
        build_index.main(["--clean", "--unknown-option"])

    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert calls == []
    assert existing.read_bytes() == b"old index"
