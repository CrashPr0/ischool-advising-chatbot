"""Document-ingestion boundary tests."""

from src.document_processor import DocumentProcessor, HashingEmbeddings


def bare_processor():
    """Construct a loader without initializing an embedding model."""
    return DocumentProcessor.__new__(DocumentProcessor)


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
