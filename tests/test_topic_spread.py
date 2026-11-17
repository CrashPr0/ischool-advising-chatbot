"""TOPIC_SPREAD: topic assignment, the per-topic cap, backfill, and parity."""

import pytest

from langchain_core.documents import Document

from src.hybrid_retrieval import hybrid_retrieve, topic_spread_applies
from src.topic_clusters import (
    SUMMARY_BUCKET,
    chunk_bucket,
    is_broad_query,
    max_per_topic,
    page_topic,
    query_topics,
    spread_by_topic,
    topic_spread_enabled,
)


@pytest.fixture(autouse=True)
def flags_off(monkeypatch):
    for name in ("TOPIC_SPREAD", "TOPIC_SPREAD_MAX_PER_TOPIC", "SUMMARY_CHUNKS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HYBRID_RETRIEVAL", "true")


@pytest.mark.parametrize(
    ("url", "title", "topic"),
    [
        ("https://www.sjsu.edu/registrar/graduation/application-central.php", "Apply", "graduation"),
        ("https://www.sjsu.edu/gape/deadlines/fall-graduation.php", "Fall Graduation Deadlines", "graduation"),
        ("https://www.sjsu.edu/registrar/calendar/fall-2026.php", "Fall 2026", "deadlines"),
        ("https://ischool.sjsu.edu/application-deadlines", "Application Deadlines", "deadlines"),
        ("https://www.sjsu.edu/bursar/payment-refunds/index.php", "Payment", "costs"),
        ("https://www.sjsu.edu/registrar/registration/registration-basics/add-a-class.php", "Add a Class", "registration"),
        ("https://ischool.sjsu.edu/mlis-career-pathways", "MLIS Career Pathways", "careers"),
        ("https://ischool.sjsu.edu/info-289-e-portfolio-faqs", "INFO 289 e-Portfolio FAQs", "requirements"),
        ("https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=203201", "ISDA 150", "courses"),
        ("https://ischool.sjsu.edu/bsisda-advising", "BSISDA Advising", "advising"),
        ("https://ischool.sjsu.edu/admissions", "Admissions", "admissions"),
        ("https://ischool.sjsu.edu/mlis-accreditation", "mlis accreditation", "overview"),
    ],
)
def test_page_topic_rules(url, title, topic):
    assert page_topic({"source_url": url, "title": title}) == topic


def test_page_topic_metadata_override_and_empty_metadata():
    assert page_topic({"source_url": "https://ischool.sjsu.edu/admissions", "topic_cluster": "Deadlines"}) == "deadlines"
    assert page_topic({}) == "overview"
    assert page_topic(None) == "overview"


def test_summaries_share_one_bucket():
    assert chunk_bucket({"is_summary": True, "topic_cluster": "advising"}) == SUMMARY_BUCKET
    assert chunk_bucket({"is_summary": "false", "source_url": "https://ischool.sjsu.edu/bsisda-advising"}) == "advising"


def test_flags_default_off_and_cap_parsing(monkeypatch):
    assert topic_spread_enabled() is False
    assert max_per_topic() == 3
    monkeypatch.setenv("TOPIC_SPREAD", "yes")
    monkeypatch.setenv("TOPIC_SPREAD_MAX_PER_TOPIC", "2")
    assert topic_spread_enabled() is True
    assert max_per_topic() == 2
    monkeypatch.setenv("TOPIC_SPREAD_MAX_PER_TOPIC", "lots")
    assert max_per_topic() == 3
    monkeypatch.setenv("TOPIC_SPREAD_MAX_PER_TOPIC", "0")
    assert max_per_topic() == 1


def test_cap_then_backfill_preserves_order():
    items = ["a1", "a2", "a3", "a4", "b1", "a5", "c1"]
    spread = spread_by_topic(items, 5, lambda item: item[0], cap=2)
    # a3 is backfilled (only b1 and c1 are other topics) and keeps its rank.
    assert spread[:5] == ["a1", "a2", "a3", "b1", "c1"]
    # Every item is kept; the deferred ones follow in their original order.
    assert sorted(spread) == sorted(items)
    assert spread[5:] == ["a4", "a5"]


def test_backfill_when_only_one_topic_exists():
    items = ["a1", "a2", "a3", "a4"]
    assert spread_by_topic(items, 3, lambda item: "a", cap=1) == items


def test_page_cap_spreads_slots_across_pages():
    items = [("adv", "faq"), ("adv", "faq"), ("adv", "faq"), ("adv", "advising"), ("cost", "fees")]
    spread = spread_by_topic(
        items, 3, lambda item: item[0], cap=3, page_of=lambda item: item[1], page_cap=2
    )
    assert spread[:3] == [("adv", "faq"), ("adv", "faq"), ("adv", "advising")]


def test_spread_edge_cases():
    assert spread_by_topic([], 3, str) == []
    assert spread_by_topic(["a"], 3, str) == ["a"]
    assert spread_by_topic(["a", "b"], 0, str) == ["a", "b"]


@pytest.mark.parametrize(
    ("query", "broad"),
    [
        ("What should I know before applying to the MLIS program?", True),
        ("I am a new student. Tell me about advising, registering, fees, and graduating.", True),
        ("What is the fee payment deadline?", False),
        ("How many elective units can MARA students take?", False),
        ("", False),
    ],
)
def test_broad_query_detection(query, broad):
    assert is_broad_query(query) is broad


def test_query_topics_names_topics():
    assert query_topics("advising, fees and graduation") == {"advising", "costs", "graduation"}


def test_spread_applies_only_when_on_and_broad(monkeypatch):
    broad = "What should I know before applying to the BS ISDA program?"
    assert topic_spread_applies(broad) is False
    monkeypatch.setenv("TOPIC_SPREAD", "true")
    assert topic_spread_applies(broad) is True
    assert topic_spread_applies("What should I know before taking ISDA 150?") is False
    assert topic_spread_applies("What is the fee payment deadline?") is False


# --- hybrid_retrieve with a fixed fake store -------------------------------


class _Collection:
    id = "fake-collection"

    def __init__(self, records):
        self._records = records

    def get(self, include=None):
        return {
            "ids": [doc_id for doc_id, _c, _m in self._records],
            "documents": [content for _d, content, _m in self._records],
            "metadatas": [metadata for _d, _c, metadata in self._records],
        }


class _Store:
    def __init__(self, records):
        self._collection = _Collection(records)
        self._records = records
        self.calls = []

    def similarity_search(self, query, k=3):
        self.calls.append(k)
        return [
            Document(page_content=content, metadata=dict(metadata))
            for _id, content, metadata in self._records[:k]
        ]


FAQ = "https://ischool.sjsu.edu/bsisda-faqs"
ADVISING = "https://ischool.sjsu.edu/bsisda-advising"
APPLY = "https://ischool.sjsu.edu/admissions"
FEES = "https://ischool.sjsu.edu/bsisda-fees-and-scholarships"
DEADLINES = "https://ischool.sjsu.edu/application-deadlines"


def _record(doc_id, url, text, programs=("ISDA",)):
    return (doc_id, text, {"source_url": url, "title": doc_id, "programs": list(programs)})


def _records():
    # Advising pages dominate both lists for this wording.
    rows = [
        _record(f"faq{i}", FAQ, f"ISDA program applying know advising question {i} applying know program")
        for i in range(5)
    ]
    rows += [
        _record(f"adv{i}", ADVISING, f"ISDA advising applying know program appointment {i}")
        for i in range(3)
    ]
    rows += [
        _record("apply", APPLY, "Admissions: applying to the ISDA program requires transcripts."),
        _record("fees", FEES, "ISDA program fees and scholarships before applying."),
        _record("deadlines", DEADLINES, "Application deadlines for the program.", programs=("MLIS",)),
    ]
    return rows


BROAD = "What should I know before applying to the ISDA program?"


def _ids(documents):
    return [doc.metadata["title"] for doc in documents]


def test_flags_off_is_unchanged_and_spread_diversifies(monkeypatch):
    off = _ids(hybrid_retrieve(_Store(_records()), BROAD, k=8))
    monkeypatch.setenv("TOPIC_SPREAD", "false")
    assert _ids(hybrid_retrieve(_Store(_records()), BROAD, k=8)) == off

    monkeypatch.setenv("TOPIC_SPREAD", "true")
    on = _ids(hybrid_retrieve(_Store(_records()), BROAD, k=8))
    assert off == ["fees", "faq0", "faq1", "faq2", "faq3", "faq4", "adv0", "adv1"]
    # Admissions and deadlines enter; advising is backfilled only because no
    # other topic has a candidate left.
    assert on == ["fees", "faq0", "faq1", "faq2", "faq3", "adv0", "apply", "deadlines"]


def test_spread_keeps_program_filter(monkeypatch):
    monkeypatch.setenv("TOPIC_SPREAD", "true")
    hits = hybrid_retrieve(_Store(_records()), BROAD, k=8, program="isda")
    assert _ids(hits) == ["fees", "faq0", "faq1", "faq2", "faq3", "faq4", "adv0", "apply"]
    assert all("ISDA" in doc.metadata["programs"] for doc in hits)


def test_narrow_question_keeps_fused_order_with_spread_on(monkeypatch):
    query = "What is the ISDA advising appointment?"
    off = _ids(hybrid_retrieve(_Store(_records()), query, k=6))
    monkeypatch.setenv("TOPIC_SPREAD", "true")
    assert _ids(hybrid_retrieve(_Store(_records()), query, k=6)) == off


def test_vector_only_path_spreads_and_is_unchanged_when_off(monkeypatch, tmp_path):
    from src.config import Config
    from src.document_processor import DocumentProcessor

    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("HYBRID_RETRIEVAL", "false")
    processor = DocumentProcessor(str(tmp_path))
    store = _Store(_records())
    monkeypatch.setattr(processor, "_snapshot_aligned_store", lambda: (store, None))
    off = _ids(processor.retrieve_relevant_docs(BROAD, k=4))
    assert off == ["faq0", "faq1", "faq2", "faq3"]
    assert store.calls == [4]
    monkeypatch.setenv("TOPIC_SPREAD", "true")
    on = _ids(processor.retrieve_relevant_docs(BROAD, k=4))
    # A wider candidate pool, then at most two chunks per page.
    assert on == ["faq0", "faq1", "adv0", "apply"]
    assert store.calls[-1] == 20
