"""SUMMARY_CHUNKS: generation (mocked model), cache, failures, citations, parity."""

import json

import pytest
import yaml

from langchain_core.documents import Document

from src import summary_chunks
from src.catalog_recency import document_heading
from src.config import Config
from src.document_processor import DocumentProcessor
from src.hybrid_retrieval import hybrid_retrieve, without_hidden_summaries
from src.summary_chunks import (
    MIN_PAGE_CHARS_FOR_SUMMARY,
    PAGE_LEVEL,
    TOPIC_LEVEL,
    SummaryCache,
    build_summary_documents,
    is_summary_metadata,
    optional_summary_documents,
    summary_chunks_enabled,
    summary_source_urls,
)

LONG = "Advising appointments are booked online through the iSchool portal. " * 40
FAQ_URL = "https://ischool.sjsu.edu/bsisda-faqs"
ADVISING_URL = "https://ischool.sjsu.edu/bsisda-advising"
FEES_URL = "https://ischool.sjsu.edu/bsisda-fees-and-scholarships"


@pytest.fixture(autouse=True)
def flags_off(monkeypatch):
    for name in ("SUMMARY_CHUNKS", "TOPIC_SPREAD", "SUMMARY_CACHE_PATH"):
        monkeypatch.delenv(name, raising=False)


class _Model:
    """Mock summarizer that records prompts."""

    def __init__(self, fail=False):
        self.prompts = []
        self.fail = fail

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("429 Too Many Requests")
        kind = "page" if "Page text:" in prompt else "topic"
        return f"  {kind} summary   number {len(self.prompts)} "


def _page(url, text, title, programs=("ISDA",)):
    return Document(
        page_content=text,
        metadata={
            "source_url": url,
            "title": title,
            "programs": list(programs),
            "source": f"/docs/{title}.txt",
        },
    )


def _corpus():
    return [
        _page(FAQ_URL, LONG, "BSISDA FAQs"),
        _page(ADVISING_URL, "Short advising page with the change-of-major steps.", "BSISDA Advising"),
        _page(FEES_URL, "Fees for ISDA.", "BSISDA Fees", programs=("ISDA", "MLIS")),
    ]


def test_flag_defaults_off(monkeypatch):
    assert summary_chunks_enabled() is False
    monkeypatch.setenv("SUMMARY_CHUNKS", "true")
    assert summary_chunks_enabled() is True


def test_page_and_topic_summaries_carry_citation_metadata():
    model = _Model()
    summaries = build_summary_documents(_corpus(), summarize=model, cache=SummaryCache(None))

    pages = [doc for doc in summaries if doc.metadata["summary_level"] == PAGE_LEVEL]
    topics = [doc for doc in summaries if doc.metadata["summary_level"] == TOPIC_LEVEL]
    # Only the long page gets its own summary; short pages are already chunk-sized.
    assert len(pages) == 1
    page = pages[0]
    assert len(LONG) >= MIN_PAGE_CHARS_FOR_SUMMARY
    assert page.page_content == "page summary number 1"
    assert page.metadata["source_url"] == FAQ_URL
    assert page.metadata["summary_source_urls"] == [FAQ_URL]
    assert page.metadata["programs"] == ["ISDA"]
    assert page.metadata["topic_cluster"] == "advising"
    assert page.metadata["title"] == "Summary: BSISDA FAQs"
    assert is_summary_metadata(page.metadata)

    # FAQ and Advising share the advising topic for ISDA; fees is alone.
    assert len(topics) == 1
    topic = topics[0]
    assert topic.metadata["topic_cluster"] == "advising"
    assert topic.metadata["programs"] == ["ISDA"]
    assert set(topic.metadata["summary_source_urls"]) == {FAQ_URL, ADVISING_URL}
    # The longest page anchors source_url, so the citation is a real page.
    assert topic.metadata["source_url"] == FAQ_URL
    assert summary_source_urls(topic.metadata)[0] == FAQ_URL
    # The topic prompt is built from the page summary, not the raw long page.
    assert "page summary number 1" in model.prompts[-1]
    assert LONG[:200] not in model.prompts[-1]


def test_cache_skips_unchanged_pages_and_regenerates_changed_ones(tmp_path):
    path = tmp_path / "summary_cache.json"
    first = _Model()
    cache = SummaryCache(path)
    build_summary_documents(_corpus(), summarize=first, cache=cache)
    cache.save()
    assert len(first.prompts) == 2

    second = _Model()
    again = SummaryCache(path)
    rebuilt = build_summary_documents(_corpus(), summarize=second, cache=again)
    assert second.prompts == []
    assert again.hits == 2
    assert [doc.page_content for doc in rebuilt] == ["page summary number 1", "topic summary number 2"]

    changed = _corpus()
    changed[0].page_content = LONG + " New registration rule."
    third = _Model()
    build_summary_documents(changed, summarize=third, cache=SummaryCache(path))
    # Same mock text for the changed page, so the cluster input is unchanged
    # and the topic overview stays cached.
    assert len(third.prompts) == 1 and "New registration rule." in third.prompts[0]

    def distinct(prompt):
        return f"summary of {len(prompt)} characters"

    calls = []
    build_summary_documents(
        changed,
        summarize=lambda prompt: calls.append(prompt) or distinct(prompt),
        cache=SummaryCache(path),
    )
    # A new page summary changes the cluster input: page + cluster regenerate.
    assert len(calls) == 2


def test_cache_save_prunes_unused_entries_and_survives_corruption(tmp_path):
    path = tmp_path / "summary_cache.json"
    cache = SummaryCache(path)
    cache.put("old", "stale", "page")
    cache.save()
    fresh = SummaryCache(path)
    fresh.save()
    assert json.loads(path.read_text())["entries"] == {}

    path.write_text("{not json", encoding="utf-8")
    assert SummaryCache(path).get("anything") is None


def test_failures_skip_summaries_and_stop_after_three():
    model = _Model(fail=True)
    corpus = _corpus() + [
        _page(f"https://ischool.sjsu.edu/long-{i}", LONG, f"Long {i}") for i in range(5)
    ]
    assert build_summary_documents(corpus, summarize=model, cache=SummaryCache(None)) == []
    assert len(model.prompts) == 3


def test_optional_summaries_off_never_calls_the_model(tmp_path):
    model = _Model()
    assert optional_summary_documents(_corpus(), summarize=model, cache_path=tmp_path / "c.json") == []
    assert model.prompts == []


def test_optional_summaries_swallow_unexpected_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("SUMMARY_CHUNKS", "true")

    def broken(*_args, **_kwargs):
        raise ValueError("bad corpus")

    monkeypatch.setattr(summary_chunks, "build_summary_documents", broken)
    assert optional_summary_documents(_corpus(), summarize=_Model(), cache_path=tmp_path / "c.json") == []


def test_default_summarizer_uses_provider_and_retries_reasoning(monkeypatch):
    from src import llm_providers

    created = []

    class _Response:
        def __init__(self, text):
            self.content = text

    class _LLM:
        def __init__(self, temperature):
            self.temperature = temperature

        def invoke(self, prompt):
            if self.temperature is not llm_providers.REASONING_RETRY:
                raise ValueError("Unsupported parameter: 'temperature'")
            return _Response([{"text": "A summary."}])

    def create_llm(provider=None, **kwargs):
        created.append(kwargs)
        return _LLM(kwargs.get("temperature"))

    monkeypatch.setattr(llm_providers.LLMFactory, "create_llm", staticmethod(create_llm))
    summarize = summary_chunks.default_summarizer()
    assert summarize("prompt") == "A summary."
    assert created == [{}, {"temperature": llm_providers.REASONING_RETRY}]


# --- index build and retrieval ---------------------------------------------


def _write_corpus(root):
    (root / "txt").mkdir(parents=True)
    (root / "meta").mkdir()
    pages = [
        ("faqs", FAQ_URL, "BSISDA FAQs - SJSU", LONG),
        ("advising", ADVISING_URL, "BSISDA Advising - SJSU", "Advising: email the BSISDA coordinator to change major."),
    ]
    for name, url, title, text in pages:
        (root / "txt" / f"{name}.txt").write_text(text, encoding="utf-8")
        (root / "meta" / f"{name}.yml").write_text(
            yaml.safe_dump({"title": title, "source_url": url, "programs": ["ISDA"]}),
            encoding="utf-8",
        )


@pytest.fixture
def corpus_root(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("SUMMARY_CACHE_PATH", str(tmp_path / "summary_cache.json"))
    monkeypatch.setenv("HYBRID_RETRIEVAL", "true")
    root = tmp_path / "docs"
    _write_corpus(root)
    return root


def _summaries_in(processor):
    payload = processor.vector_store._collection.get(include=["metadatas"])
    return [meta for meta in payload["metadatas"] if is_summary_metadata(meta)]


def test_build_without_flag_adds_no_summaries(corpus_root, monkeypatch):
    def unexpected():
        raise AssertionError("model must not be created")

    monkeypatch.setattr(summary_chunks, "default_summarizer", unexpected)
    processor = DocumentProcessor(str(corpus_root))
    assert processor.process_and_index() is not None
    assert _summaries_in(processor) == []


def test_build_succeeds_when_summary_model_fails(corpus_root, monkeypatch):
    monkeypatch.setenv("SUMMARY_CHUNKS", "true")
    monkeypatch.setattr(summary_chunks, "default_summarizer", lambda: _Model(fail=True))
    processor = DocumentProcessor(str(corpus_root))
    assert processor.process_and_index() is not None
    assert processor.vector_store_count() > 0
    assert _summaries_in(processor) == []


def test_built_summaries_are_hidden_when_flag_off_and_never_pinned(corpus_root, monkeypatch):
    monkeypatch.setenv("SUMMARY_CHUNKS", "true")
    monkeypatch.setattr(summary_chunks, "default_summarizer", lambda: _Model())
    processor = DocumentProcessor(str(corpus_root))
    processor.process_and_index()
    stored = _summaries_in(processor)
    assert {meta["summary_level"] for meta in stored} == {PAGE_LEVEL, TOPIC_LEVEL}
    # Lists survive the Chroma round trip, so citations still resolve.
    topic = next(meta for meta in stored if meta["summary_level"] == TOPIC_LEVEL)
    assert set(summary_source_urls(topic)) == {FAQ_URL, ADVISING_URL}

    query = "advising summary"
    with_summaries = processor.retrieve_relevant_docs(query, k=8)
    assert any(is_summary_metadata(doc.metadata) for doc in with_summaries)

    monkeypatch.setenv("SUMMARY_CHUNKS", "false")
    without = processor.retrieve_relevant_docs(query, k=8)
    assert without and not any(is_summary_metadata(doc.metadata) for doc in without)
    monkeypatch.setenv("HYBRID_RETRIEVAL", "false")
    vector_only = processor.retrieve_relevant_docs(query, k=8)
    assert vector_only and not any(is_summary_metadata(doc.metadata) for doc in vector_only)

    # Pinning (Application Central, MLIS required courses) reads published text only.
    monkeypatch.setenv("SUMMARY_CHUNKS", "true")
    monkeypatch.setenv("HYBRID_RETRIEVAL", "true")
    pinned = processor.chunks_matching_source("bsisda-faqs")
    assert pinned and not any(is_summary_metadata(doc.metadata) for doc in pinned)


class _Collection:
    id = "parity"

    def __init__(self, records):
        self._records = records

    def get(self, include=None):
        return {
            "ids": [r[0] for r in self._records],
            "documents": [r[1] for r in self._records],
            "metadatas": [r[2] for r in self._records],
        }


class _Store:
    def __init__(self, records):
        self._collection = _Collection(records)
        self._records = records
        self.calls = []

    def similarity_search(self, query, k=3):
        self.calls.append(k)
        return [Document(page_content=r[1], metadata=dict(r[2])) for r in self._records[:k]]


def _page_records():
    return [
        (f"p{i}", f"Advising page chunk {i} about appointments and fees.", {"source_url": f"https://ischool.sjsu.edu/p{i}", "title": f"p{i}"})
        for i in range(25)
    ]


def test_flags_off_parity_with_summaries_in_the_index():
    pages = _page_records()
    summaries = [
        (f"s{i}", f"Summary {i} of advising appointments and fees.", {"source_url": "https://ischool.sjsu.edu/p0", "title": f"s{i}", "is_summary": True})
        for i in range(3)
    ]
    query = "advising appointments fees"
    plain_store = _Store(pages)
    mixed_store = _Store(summaries + pages)
    plain = hybrid_retrieve(plain_store, query, k=8)
    mixed = hybrid_retrieve(mixed_store, query, k=8)
    assert [d.page_content for d in plain] == [d.page_content for d in mixed]
    # The vector side fetched past the three hidden summaries.
    assert plain_store.calls == [20]
    assert mixed_store.calls == [23]
    kept, hidden = without_hidden_summaries(mixed_store._records)
    assert hidden == 3 and len(kept) == 25


# --- citations in the chat prompt ------------------------------------------


def test_summary_heading_and_context_cite_covered_pages():
    from src.rag_chatbot import RAGAdvisingChatbot, _allowed_url_keys, _url_key

    summary = Document(
        page_content="Overview of advising pages.",
        metadata={
            "is_summary": True,
            "summary_level": TOPIC_LEVEL,
            "source": "summary:advising:isda",
            "source_url": FAQ_URL,
            "summary_source_urls": [FAQ_URL, ADVISING_URL],
            # A summary of a catalog page is still not the catalog.
            "catalog_year": "2026-2027",
        },
    )
    heading = document_heading(1, summary.metadata)
    assert "generated summary" in heading
    assert "Catalog year" not in heading

    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    context = bot._build_context("advising", [summary])
    assert f"Summarizes source URLs: {FAQ_URL}, {ADVISING_URL}" in context

    keys = _allowed_url_keys([summary])
    assert _url_key(ADVISING_URL) in keys and _url_key(FAQ_URL) in keys


def test_ordinary_chunk_heading_and_links_unchanged():
    from src.rag_chatbot import _allowed_url_keys, _url_key

    metadata = {"source": "a.txt", "source_url": FEES_URL}
    assert document_heading(2, metadata).endswith("(not the official catalog)]")
    assert _url_key(FEES_URL) in _allowed_url_keys([Document(page_content="Fees.", metadata=metadata)])


def test_fast_paths_do_not_retrieve_with_flags_on(monkeypatch):
    from tests.test_rag_chatbot import _bare_bot

    monkeypatch.setenv("SUMMARY_CHUNKS", "true")
    monkeypatch.setenv("TOPIC_SPREAD", "true")
    bot = _bare_bot()
    answer = bot.chat("What are the prerequisites for ISDA 105?")
    assert answer["source"] == "catalog_lookup"
    assert bot.doc_processor.queries == []
