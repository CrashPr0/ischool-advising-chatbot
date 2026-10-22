"""Document-ingestion boundary tests."""

import pytest

from src.document_processor import DocumentProcessor, HashingEmbeddings


def bare_processor():
    """Construct a loader without initializing an embedding model."""
    return DocumentProcessor.__new__(DocumentProcessor)


def test_hashing_embeddings_ignore_single_digit_unit_markers():
    embeddings = HashingEmbeddings(dimensions=64)
    bare = embeddings.embed_query("ISDA core courses")
    marked = embeddings.embed_query("ISDA core courses (3) (2) (4)")
    numbered = embeddings.embed_query("ISDA core courses 122")

    assert bare == marked
    assert bare != numbered
    assert embeddings.embed_query("3 2 4") == [0.0] * 64


def test_hashing_embeddings_are_deterministic_and_normalized():
    embeddings = HashingEmbeddings(dimensions=64)

    first = embeddings.embed_query("ISDA scholarships and financial aid")
    second = embeddings.embed_query("ISDA scholarships and financial aid")

    assert first == second
    assert len(first) == 64
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9


def test_loader_excludes_scraper_control_files(tmp_path):
    docs_root = tmp_path / "advising_docs"
    (docs_root / "txt").mkdir(parents=True)
    (docs_root / "scrape").mkdir()
    (docs_root / "txt" / "approved.txt").write_text(
        "Approved ISDA advising evidence.", encoding="utf-8"
    )
    (docs_root / "scrape" / "seed_urls.txt").write_text(
        "https://example.edu/not-evidence", encoding="utf-8"
    )
    (docs_root / "scrape" / "syllabi.txt").write_text(
        "Unreviewed syllabus control data.", encoding="utf-8"
    )

    documents = bare_processor()._load_from_directory(str(docs_root))

    assert [document.page_content for document in documents] == [
        "Approved ISDA advising evidence."
    ]


def test_source_scan_excludes_scraper_control_files(tmp_path):
    docs_root = tmp_path / "advising_docs"
    (docs_root / "txt").mkdir(parents=True)
    (docs_root / "pdfs").mkdir()
    (docs_root / "scrape").mkdir()
    (docs_root / "txt" / "approved.txt").write_text("content", encoding="utf-8")
    (docs_root / "pdfs" / "approved.pdf").write_bytes(b"%PDF-test")
    (docs_root / "scrape" / "seed_urls.txt").write_text("control", encoding="utf-8")

    processor = bare_processor()
    processor._search_directories = lambda: [str(docs_root)]

    assert processor.scan_source_files() == {
        "roots": [str(docs_root)],
        "txt_files": 1,
        "pdf_files": 1,
        "total_files": 2,
    }


@pytest.mark.parametrize("candidate_index", range(4))
def test_companion_metadata_uses_first_available_file(tmp_path, candidate_index):
    document_path = tmp_path / "txt" / "approved.txt"
    document_path.parent.mkdir()
    document_path.write_text("Approved advising evidence.", encoding="utf-8")
    (tmp_path / "meta").mkdir()
    candidates = [
        document_path.with_suffix(".yml"),
        document_path.with_suffix(".yaml"),
        tmp_path / "meta" / "approved.yml",
        tmp_path / "meta" / "approved.yaml",
    ]
    for index, candidate in enumerate(candidates[candidate_index:], candidate_index):
        candidate.write_text(
            f"title: Companion {index}\n"
            "source: untrusted-source.txt\n"
            "source_url: https://www.sjsu.edu/example\n",
            encoding="utf-8",
        )

    documents = bare_processor()._load_from_directory(str(tmp_path))

    assert len(documents) == 1
    assert documents[0].page_content == "Approved advising evidence."
    assert documents[0].metadata["title"] == f"Companion {candidate_index}"
    assert documents[0].metadata["source_url"] == "https://www.sjsu.edu/example"
    assert documents[0].metadata["source"] == str(document_path.resolve())


@pytest.mark.parametrize(
    "metadata_bytes",
    [b"title: [unterminated", b"\xff", b"- not a mapping", b"", b"null\n"],
    ids=["malformed-yaml", "invalid-utf8", "sequence", "empty", "null"],
)
def test_invalid_companion_keeps_document_without_using_lower_priority_metadata(
    tmp_path, metadata_bytes
):
    document_path = tmp_path / "approved.txt"
    document_path.write_text("Approved advising evidence.", encoding="utf-8")
    document_path.with_suffix(".yml").write_bytes(metadata_bytes)
    document_path.with_suffix(".yaml").write_text(
        "title: Lower priority metadata\n", encoding="utf-8"
    )

    documents = bare_processor()._load_from_directory(str(tmp_path))

    assert len(documents) == 1
    assert documents[0].page_content == "Approved advising evidence."
    assert documents[0].metadata == {"source": str(document_path.resolve())}
