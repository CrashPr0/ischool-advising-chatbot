"""Hosted embedding rebuilds must not publish a partial or mismatched index."""

import json
import threading

import pytest
from langchain_core.documents import Document

import app as app_module
from src.config import Config
from src.document_processor import (
    DescribedEmbeddings,
    DocumentProcessor,
    HashingEmbeddings,
    _create_embeddings,
    _is_rate_limit,
    _retry_delay_seconds,
    pack_embedding_batches,
    promote_collection,
    restore_previous_collection,
)
from src.durable_storage import assess_index_readiness


class _Headers:
    def __init__(self, retry_after):
        self._retry_after = retry_after

    def get(self, name):
        if name.lower() == "retry-after":
            return self._retry_after
        return None


class _RateLimit(Exception):
    def __init__(self, retry_after=None):
        super().__init__("429 rate limit")
        self.status_code = 429
        self.response = type(
            "Response", (), {"status_code": 429, "headers": _Headers(retry_after)}
        )()


class _Inner:
    def __init__(self, failures=0, retry_after=None):
        self.failures = failures
        self.retry_after = retry_after
        self.calls = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.failures:
            self.failures -= 1
            raise _RateLimit(self.retry_after)
        return [[float(len(text)), 0.0] for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


def _identity(**overrides):
    identity = {
        "embedding_provider": "azure_openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 1536,
    }
    identity.update(overrides)
    return identity


def _azure_config(monkeypatch, deployment="text-embedding-3-small"):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "azure_openai")
    monkeypatch.setattr(Config, "AZURE_OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setattr(
        Config, "AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/"
    )
    monkeypatch.setattr(Config, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", deployment)
    monkeypatch.setattr(Config, "AZURE_OPENAI_API_VERSION", "2024-02-15-preview")


def test_resolved_embedding_names_each_provider(monkeypatch):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    assert Config.resolved_embedding() == {
        "embedding_provider": "hashing",
        "embedding_model": "hashing",
        "embedding_dimension": 2048,
    }

    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "local")
    local = Config.resolved_embedding()
    assert local["embedding_provider"] == "local"
    assert local["embedding_model"] == "all-MiniLM-L6-v2"
    assert local["embedding_dimension"] == 384

    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "unit-test-key")
    openai = Config.resolved_embedding()
    assert openai["embedding_provider"] == "openai"
    assert openai["embedding_model"] == "text-embedding-ada-002"
    assert openai["embedding_dimension"] == 1536

    _azure_config(monkeypatch)
    azure = Config.resolved_embedding()
    assert azure == {
        "embedding_provider": "azure_openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 1536,
    }

    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "auto")
    assert Config.resolved_embedding()["embedding_provider"] == "azure_openai"

    monkeypatch.setattr(Config, "AZURE_OPENAI_API_KEY", "your_azure_openai_key_here")
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "your_openai_api_key_here")
    assert Config.resolved_embedding()["embedding_provider"] == "local"


def test_hashing_embedder_is_the_query_embedder(monkeypatch):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    embeddings = _create_embeddings()
    assert embeddings.embedding_provider == "hashing"
    assert embeddings.embed_query(
        "ISDA scholarships"
    ) == HashingEmbeddings().embed_query("ISDA scholarships")


def test_azure_embeddings_require_endpoint_key_and_deployment(monkeypatch):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "azure_openai")
    monkeypatch.setattr(Config, "AZURE_OPENAI_API_KEY", "")
    monkeypatch.setattr(Config, "AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setattr(Config, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")
    with pytest.raises(ValueError, match="AZURE_OPENAI_EMBEDDING_DEPLOYMENT"):
        _create_embeddings()


def test_batches_stay_under_the_token_budget_and_retry_429():
    batches = pack_embedding_batches(["a", "b", "c", "d"], 250, lambda text: 100)
    assert batches == [["a", "b"], ["c", "d"]]

    sleeps = []
    inner = _Inner(failures=1, retry_after="9")
    embeddings = DescribedEmbeddings(
        inner,
        _identity(),
        count_tokens=lambda text: 100,
        sleep=sleeps.append,
        batch_tokens=250,
    )
    vectors = embeddings.embed_documents(["one", "two", "three"])
    assert [len(call) for call in inner.calls] == [2, 2, 1]
    assert sleeps == [9.0]
    assert len(vectors) == 3
    assert embeddings.measured_dimension == 2
    assert _is_rate_limit(_RateLimit())
    assert _retry_delay_seconds(_RateLimit("1"), 4.0) == 4.0
    assert _retry_delay_seconds(_RateLimit(None), 4.0) == 4.0


def test_query_embedding_caps_attempts_and_does_not_sleep_out_retry_after():
    sleeps = []
    inner = _Inner(failures=5, retry_after="30")
    embeddings = DescribedEmbeddings(inner, _identity(), sleep=sleeps.append)
    with pytest.raises(_RateLimit):
        embeddings.embed_query("ISDA scholarships")
    assert len(inner.calls) == 2
    assert sleeps == [2.0]

    recovering = _Inner(failures=1, retry_after="30")
    sleeps.clear()
    recovered = DescribedEmbeddings(recovering, _identity(), sleep=sleeps.append)
    vector = recovered.embed_query("ISDA scholarships")
    assert vector == [float(len("ISDA scholarships")), 0.0]
    assert len(recovering.calls) == 2
    assert sleeps == [2.0]


def test_query_embedding_records_request_metrics():
    from src.document_processor import chat_request_metrics

    bucket = {"embedding_ms": 0.0, "rate_limit_count": 0}
    token = chat_request_metrics.set(bucket)
    try:
        inner = _Inner(failures=1, retry_after="1")
        embeddings = DescribedEmbeddings(
            inner, _identity(), sleep=lambda _seconds: None
        )
        embeddings.embed_query("hi")
    finally:
        chat_request_metrics.reset(token)
    assert bucket["rate_limit_count"] == 1
    assert bucket["embedding_ms"] >= 0.0


def test_query_embedding_does_not_retry_a_non_rate_limit():
    class Boom:
        def embed_documents(self, texts):
            raise RuntimeError("disk full")

        def embed_query(self, text):
            raise AssertionError("inner embed_query should not run")

    embeddings = DescribedEmbeddings(Boom(), _identity(), sleep=lambda _seconds: None)
    with pytest.raises(RuntimeError, match="disk full"):
        embeddings.embed_query("only")


def test_query_embedder_uses_a_short_timeout_and_index_client_does_not(monkeypatch):
    _azure_config(monkeypatch)
    embeddings = _create_embeddings()
    assert embeddings.inner.max_retries == 0
    assert embeddings.inner.request_timeout is None
    query = embeddings._query_embedder()
    assert query is not embeddings.inner
    assert query.max_retries == 0
    assert query.request_timeout == 8.0
    assert query.client._client.timeout == 8.0
    assert query.client._client.max_retries == 0


def test_non_rate_limit_errors_are_not_retried():
    class Boom:
        def embed_documents(self, texts):
            raise RuntimeError("disk full")

        def embed_query(self, text):
            raise AssertionError("query should not run")

    embeddings = DescribedEmbeddings(Boom(), _identity(), sleep=lambda _seconds: None)
    with pytest.raises(RuntimeError, match="disk full"):
        embeddings.embed_documents(["only"])


class _Collection:
    def __init__(self, name, collection_id, client):
        self.name = name
        self.id = collection_id
        self._client = client

    def modify(self, name=None, metadata=None, configuration=None):
        if not name or name == self.name:
            return
        self._client.collections.pop(self.name, None)
        self.name = name
        self._client.collections[name] = self


class _Client:
    def __init__(self):
        self.collections = {}
        self.deleted = []

    def add(self, name, collection_id):
        collection = _Collection(name, collection_id, self)
        self.collections[name] = collection
        return collection

    def get_collection(self, name, embedding_function=None):
        if name not in self.collections:
            raise RuntimeError(f"Collection [{name}] does not exist")
        return self.collections[name]

    def delete_collection(self, name):
        if name not in self.collections:
            raise RuntimeError(f"Collection [{name}] does not exist")
        self.deleted.append(name)
        del self.collections[name]


def test_promote_restores_the_live_collection_when_publish_fails():
    client = _Client()
    live = client.add("advising_documents", "live-id")
    staging = client.add("advising_documents__staging", "new-id")

    def fail_modify(name=None, metadata=None, configuration=None):
        raise RuntimeError("rename failed")

    staging.modify = fail_modify
    with pytest.raises(RuntimeError, match="rename failed"):
        promote_collection(client, "advising_documents__staging", "advising_documents")
    assert live.name == "advising_documents"
    assert client.collections["advising_documents"] is live


def test_promote_publishes_staging_and_drops_the_previous_collection():
    client = _Client()
    client.add("advising_documents", "live-id")
    staging = client.add("advising_documents__staging", "new-id")
    assert (
        promote_collection(client, "advising_documents__staging", "advising_documents")
        == "new-id"
    )
    assert client.collections["advising_documents"] is staging
    assert staging.name == "advising_documents"
    assert "advising_documents__previous" not in client.collections
    assert client.deleted == ["advising_documents__previous"]


def test_restore_renames_previous_back_when_live_is_missing():
    client = _Client()
    previous = client.add("advising_documents__previous", "live-id")
    restore_previous_collection(client, "advising_documents")
    assert previous.name == "advising_documents"
    assert "advising_documents" in client.collections


def test_embedding_mismatch_is_stale_and_legacy_hashing_stays_ready(tmp_path):
    (tmp_path / "index_build.json").write_text(
        json.dumps(
            {
                "commit": "deploy-sha",
                "embedding_provider": "hashing",
                "embedding_model": "hashing",
                "embedding_dimension": 2048,
            }
        ),
        encoding="utf-8",
    )
    expected = {
        "embedding_provider": "azure_openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 1536,
    }
    stale = assess_index_readiness(
        str(tmp_path), expected_commit="deploy-sha", expected_embedding=expected
    )
    assert stale["ready"] is False
    assert stale["status"] == "stale"
    assert "hashing" in stale["detail"]

    (tmp_path / "index_build.json").write_text(
        json.dumps(
            {
                "commit": "deploy-sha",
                "embedding_provider": "azure_openai",
                "embedding_model": "text-embedding-3-large",
                "embedding_dimension": 3072,
            }
        ),
        encoding="utf-8",
    )
    model_mismatch = assess_index_readiness(
        str(tmp_path), expected_commit="deploy-sha", expected_embedding=expected
    )
    assert model_mismatch["status"] == "stale"
    assert "text-embedding-3-large" in model_mismatch["detail"]

    (tmp_path / "index_build.json").write_text(
        json.dumps(
            {
                "commit": "deploy-sha",
                "embedding_provider": "azure_openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 3072,
            }
        ),
        encoding="utf-8",
    )
    dimension_mismatch = assess_index_readiness(
        str(tmp_path), expected_commit="deploy-sha", expected_embedding=expected
    )
    assert dimension_mismatch["status"] == "stale"
    assert "3072" in dimension_mismatch["detail"]

    (tmp_path / "index_build.json").write_text(
        json.dumps({"commit": "deploy-sha", "source_files": 20}),
        encoding="utf-8",
    )
    legacy = assess_index_readiness(
        str(tmp_path),
        expected_commit="deploy-sha",
        expected_embedding={
            "embedding_provider": "hashing",
            "embedding_model": "hashing",
            "embedding_dimension": 2048,
        },
    )
    assert legacy["ready"] is True
    hosted_legacy = assess_index_readiness(
        str(tmp_path), expected_commit="deploy-sha", expected_embedding=expected
    )
    assert hosted_legacy["status"] == "stale"
    assert hosted_legacy["ready"] is False


def test_readyz_and_chat_refuse_a_provider_mismatch_without_loading_the_model(
    monkeypatch, tmp_path
):
    # app.py imports Config after putting src/ on sys.path, so this is the
    # class /readyz actually reads. src.config.Config is a second copy.
    from config import Config as AppConfig

    store = tmp_path / "chroma"
    store.mkdir()
    (tmp_path / "deployment_version.txt").write_text("deploy-sha\n", encoding="utf-8")
    (store / "index_build.json").write_text(
        json.dumps(
            {
                "commit": "deploy-sha",
                "embedding_provider": "hashing",
                "embedding_model": "hashing",
                "embedding_dimension": 2048,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(AppConfig, "VECTOR_STORE_PATH", str(store))
    monkeypatch.setattr(AppConfig, "EMBEDDING_PROVIDER", "azure_openai")
    monkeypatch.setattr(AppConfig, "AZURE_OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setattr(
        AppConfig, "AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/"
    )
    monkeypatch.setattr(
        AppConfig, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small"
    )
    monkeypatch.setattr(app_module, "chatbot", None)
    monkeypatch.setattr(
        app_module, "_rate_limiter", app_module._FixedWindowRateLimiter()
    )

    def fail_if_loaded():
        raise AssertionError("model must not load while embeddings are stale")

    monkeypatch.setattr(app_module, "get_chatbot", fail_if_loaded)
    client = app_module.app.test_client()

    readyz = client.get("/readyz")
    assert readyz.status_code == 503
    body = readyz.get_json()
    assert body["status"] == "stale"
    assert body["ready"] is False
    assert "hashing" in body["detail"]
    assert body["expected_embedding_provider"] == "azure_openai"

    refused = client.post("/api/chat", json={"message": "What courses are required?"})
    assert refused.status_code == 503
    assert refused.get_json() == {"error": app_module._INDEX_NOT_READY_MESSAGE}
    assert client.get("/healthz").status_code == 200
    assert app_module.chatbot is None


def test_failed_azure_style_rebuild_keeps_the_previous_collection(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path / "chroma"))
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "keep.txt").write_text(
        "The ISDA advising office keeps this fact about prerequisites.",
        encoding="utf-8",
    )
    processor = DocumentProcessor(str(docs))
    assert processor.process_and_index() is not None
    original = processor.retrieve_relevant_docs("ISDA advising office", k=1)
    assert original and "keeps this fact" in original[0].page_content

    class Boom(HashingEmbeddings):
        def embed_documents(self, texts):
            raise RuntimeError("429 rate limit")

    processor.embeddings = DescribedEmbeddings(
        Boom(dimensions=2048),
        {
            "embedding_provider": "hashing",
            "embedding_model": "hashing",
            "embedding_dimension": 2048,
        },
    )
    (docs / "keep.txt").write_text(
        "This replacement must not be published.", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="rate limit"):
        processor.process_and_index()

    kept = processor.retrieve_relevant_docs("ISDA advising office prerequisites", k=1)
    assert kept and "keeps this fact" in kept[0].page_content
    assert all("must not be published" not in hit.page_content for hit in kept)


def test_successful_rebuild_swaps_in_the_new_collection(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    store = tmp_path / "chroma"
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(store))
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.txt").write_text(
        "Alpha advising fact about the ISDA catalog.", encoding="utf-8"
    )
    processor = DocumentProcessor(str(docs))
    assert processor.process_and_index() is not None
    first_id = processor.active_collection_id
    assert first_id

    (docs / "note.txt").write_text(
        "Beta advising fact about registration deadlines.", encoding="utf-8"
    )
    assert processor.process_and_index() is not None
    assert processor.active_collection_id
    assert processor.active_collection_id != first_id
    hits = processor.retrieve_relevant_docs("registration deadlines", k=2)
    assert hits
    assert any("Beta advising fact" in hit.page_content for hit in hits)
    assert all("Alpha advising fact" not in hit.page_content for hit in hits)

    import chromadb

    names = [
        collection.name
        for collection in chromadb.PersistentClient(path=str(store)).list_collections()
    ]
    assert names == ["advising_documents"]


def test_load_restores_the_previous_collection_after_a_crashed_swap(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    store = tmp_path / "chroma"
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(store))
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "keep.txt").write_text(
        "Crash recovery keeps the ISDA advising fact.", encoding="utf-8"
    )
    built = DocumentProcessor(str(docs))
    assert built.process_and_index() is not None

    import chromadb

    client = chromadb.PersistentClient(path=str(store))
    client.get_collection("advising_documents", embedding_function=None).modify(
        name="advising_documents__previous"
    )
    reloaded = DocumentProcessor(str(docs))
    assert reloaded.load_vector_store() is not None
    hits = reloaded.retrieve_relevant_docs("ISDA advising fact", k=1)
    assert hits and "Crash recovery" in hits[0].page_content


def test_retrieve_reloads_when_the_receipt_names_another_collection(
    monkeypatch, tmp_path
):
    store = tmp_path / "chroma"
    store.mkdir()
    (store / "index_build.json").write_text(
        json.dumps({"commit": "deploy-sha", "collection_id": "new-id"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(store))
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.collection_name = "advising_documents"
    processor._index_lock = threading.RLock()
    processor.vector_store = object()
    processor.active_collection_id = "old-id"

    def load_and_arm():
        replacement = type("Store", (), {})()
        replacement.similarity_search = lambda query, k: [
            Document(page_content="reloaded", metadata={})
        ]
        processor.vector_store = replacement
        processor.active_collection_id = "new-id"
        return replacement

    processor.load_vector_store = load_and_arm
    hits = processor.retrieve_relevant_docs("hello", k=1)
    assert hits[0].page_content == "reloaded"
    assert processor.active_collection_id == "new-id"
