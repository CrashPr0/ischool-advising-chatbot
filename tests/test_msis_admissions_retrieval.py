"""MSIS admission questions reach the shared iSchool graduate admission pages.

Both official snapshots name "MS in Informatics" among the graduate programs
they cover. Selecting MSIS filters retrieval to MSIS-tagged chunks, so the
pages must carry the MSIS label. Offline: hashing embeddings, no network.
"""

import json
from pathlib import Path

import pytest
import yaml

from src.config import Config
from src.document_processor import DocumentProcessor

DOCS = Path(__file__).resolve().parents[1] / "data" / "advising_docs"
ADMISSIONS = "https://ischool.sjsu.edu/admissions"
APPLICATION = "https://ischool.sjsu.edu/graduate-application"
GRADUATE_PROGRAMS_LINE = (
    "MLIS, Teacher Librarian, MARA, MS in Informatics, MSIDT, "
    "Digital Assets Certificate & Post-Master’s Certificate"
)


@pytest.fixture(scope="module")
def corpus_processor(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    patch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path_factory.mktemp("msis-admissions")))
    patch.setenv("HYBRID_RETRIEVAL", "true")
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    yield processor
    patch.undo()


def _meta(url):
    for path in (DOCS / "meta").glob("*.yml"):
        meta = yaml.safe_load(path.read_text(encoding="utf-8"))
        if meta.get("source_url") == url:
            return meta
    raise AssertionError(f"{url} is not ingested")


@pytest.mark.parametrize("url", [ADMISSIONS, APPLICATION])
def test_shared_graduate_pages_are_labeled_for_msis(url):
    meta = _meta(url)
    text = (DOCS / "txt" / f"{meta['doc_id']}.txt").read_text(encoding="utf-8")
    # The label rests on the official page naming the program, not on inference.
    assert GRADUATE_PROGRAMS_LINE in text
    assert "MSIS" in meta["programs"]
    manifest = json.loads((DOCS / "scrape" / "manifest.json").read_text(encoding="utf-8"))
    entry = next(e for e in manifest["documents"] if e["source_url"] == url)
    assert entry["programs"] == meta["programs"]


@pytest.mark.parametrize("question,url", [
    ("What are the admission requirements for the MS in Informatics?", ADMISSIONS),
    ("Do I need the GRE to apply to the MS Informatics program?", ADMISSIONS),
    ("How do I apply to the MS Informatics program?", APPLICATION),
])
def test_msis_selection_retrieves_graduate_admission_pages(corpus_processor, question, url):
    hits = corpus_processor.retrieve_relevant_docs(question, k=8, program="msis")
    assert hits
    assert all("MSIS" in hit.metadata["programs"] for hit in hits)
    assert url in {hit.metadata.get("source_url") for hit in hits}
