"""Document processing for RAG (Retrieval Augmented Generation)."""

try:
    __import__("pysqlite3")
    import sys

    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except ImportError:
    pass

import contextvars
import os
import hashlib
import json
import math
import re
import threading
import time
from typing import Callable, List, Optional, Sequence
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_core.documents import Document

try:
    from langchain_chroma import Chroma
except ImportError:  # pragma: no cover - compatibility with older installs
    from langchain_community.vectorstores import Chroma
import yaml
from langchain_core.embeddings import Embeddings

try:
    from .config import Config
except ImportError:  # pragma: no cover
    from config import Config


class HashingEmbeddings(Embeddings):
    """Small deterministic offline embedder for quota-free indexing.

    Feature hashing is lexical rather than generative, but it is a strong fit
    for a focused advising corpus: identical academic terms, acronyms, course
    codes, and URL text map consistently without downloading a model or making
    an external API call.
    """

    _STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "many",
        "of",
        "on",
        "or",
        "that",
        "the",
        "there",
        "to",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }

    def __init__(self, dimensions: int = 2048):
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        # A one-digit unit marker such as "(3)" would otherwise dominate the
        # vector and hide the course list. Course numbers stay; they are longer.
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", text.casefold())
            if token not in self._STOPWORDS and not (token.isdigit() and len(token) == 1)
        ]
        if "bsisda" in tokens:
            tokens.extend(["bs", "isda", "information", "science", "data", "analytics"])
        elif "isda" in tokens:
            tokens.extend(["information", "science", "data", "analytics"])
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimensions
            vector[index] += 1.0
        magnitude = math.sqrt(sum(component * component for component in vector))
        if magnitude:
            vector = [component / magnitude for component in vector]
        return vector

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text)


# One request must fit in a 1000 token-per-minute Azure deployment. A larger
# request is rejected with 429 on every retry. 800 leaves room for a small
# difference between our token count and Azure's.
EMBEDDING_BATCH_TOKEN_BUDGET = 800
EMBEDDING_RATE_LIMIT_ATTEMPTS = 8
_RETRY_BASE_SECONDS = 2.0
_RETRY_MAX_SECONDS = 60.0
# Chat-path query embeddings only. Index builds keep the 8-attempt backoff
# above. A student question must not sleep through a long Retry-After.
QUERY_EMBED_ATTEMPTS = 2
QUERY_EMBED_TIMEOUT_SECONDS = 8.0
QUERY_EMBED_BACKOFF_BUDGET_SECONDS = 2.0
# chat() installs a dict for the current request. Index builds leave it unset
# so their retries are not counted as student latency.
chat_request_metrics: contextvars.ContextVar = contextvars.ContextVar(
    "chat_request_metrics", default=None
)
_STAGING_SUFFIX = "__staging"
_PREVIOUS_SUFFIX = "__previous"
_HOSTED_EMBEDDING_PROVIDERS = {"azure_openai", "openai"}
_EMBEDDING_PROVIDERS = {"auto", "hashing", "local", "azure_openai", "openai"}


def staging_collection_name(live_name: str) -> str:
    return f"{live_name}{_STAGING_SUFFIX}"


def previous_collection_name(live_name: str) -> str:
    return f"{live_name}{_PREVIOUS_SUFFIX}"


def estimate_embedding_tokens(text: str) -> int:
    """Count cl100k tokens, or overestimate when tiktoken is unavailable.

    chars/4 can undercount. An undercount makes one batch larger than the
    deployment's tokens-per-minute cap, and that request never succeeds.
    """
    return max(1, _embedding_token_counter()(text))


def _embedding_token_counter() -> Callable[[str], int]:
    counter = getattr(_embedding_token_counter, "_counter", None)
    if counter is not None:
        return counter
    try:
        import tiktoken

        encoder = tiktoken.get_encoding("cl100k_base")

        def count(text: str) -> int:
            return len(encoder.encode(text))

    except Exception:

        def count(text: str) -> int:
            return max(1, (len(text) + 2) // 3)

    setattr(_embedding_token_counter, "_counter", count)
    return count


def pack_embedding_batches(
    texts: Sequence[str], budget: int, count_tokens: Callable[[str], int]
) -> List[List[str]]:
    """Group texts so each batch stays within ``budget`` tokens.

    A single text larger than the budget is its own batch. Advising chunks
    are far smaller than the budget.
    """
    batches: List[List[str]] = []
    current: List[str] = []
    current_tokens = 0
    limit = max(1, int(budget))
    for text in texts:
        tokens = max(1, int(count_tokens(text)))
        if current and current_tokens + tokens > limit:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(text)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


def _is_rate_limit(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "status_code", None) == 429:
            return True
        response = getattr(current, "response", None)
        if getattr(response, "status_code", None) == 429:
            return True
        if "ratelimit" in type(current).__name__.lower():
            return True
        cause = getattr(current, "__cause__", None)
        current = cause if isinstance(cause, BaseException) else None
    text = str(exc).lower()
    return "error code: 429" in text or "rate limit" in text or "ratelimit" in text


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    candidates = [exc, getattr(exc, "__cause__", None)]
    for candidate in candidates:
        if candidate is None:
            continue
        response = getattr(candidate, "response", None)
        headers = getattr(response, "headers", None) or getattr(
            candidate, "headers", None
        )
        if not headers:
            continue
        try:
            raw = headers.get("retry-after")
        except Exception:
            raw = None
        if raw is None:
            continue
        try:
            return max(0.0, float(str(raw).strip()))
        except (TypeError, ValueError):
            return None
    return None


def _retry_delay_seconds(exc: BaseException, backoff: float) -> float:
    """Honor Retry-After, but do not retry a tokens-per-minute 429 in 1 second."""
    header = _retry_after_seconds(exc)
    floor = min(max(backoff, 0.0), _RETRY_MAX_SECONDS)
    if header is None:
        return floor
    return min(max(header, floor), 120.0)


def _metrics_bucket() -> Optional[dict]:
    bucket = chat_request_metrics.get()
    return bucket if isinstance(bucket, dict) else None


def _note_embedding_ms(elapsed_ms: float) -> None:
    bucket = _metrics_bucket()
    if bucket is None:
        return
    bucket["embedding_ms"] = float(bucket.get("embedding_ms", 0.0)) + float(elapsed_ms)


def _note_rate_limit() -> None:
    bucket = _metrics_bucket()
    if bucket is None:
        return
    bucket["rate_limit_count"] = int(bucket.get("rate_limit_count", 0)) + 1


def _secret_value(value):
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return getter()
    return value


def _build_query_embedder(inner, timeout_seconds: float):
    """Copy a hosted embedder with a short HTTP timeout, or return None.

    Index builds keep ``inner``. Only ``embed_query`` uses this copy, so a
    student request cannot inherit the SDK's long default timeout. Test
    doubles and the hashing embedder return None and are called directly.
    """
    name = type(inner).__name__
    try:
        if name == "AzureOpenAIEmbeddings":
            return type(inner)(
                azure_deployment=inner.deployment,
                azure_endpoint=inner.azure_endpoint,
                api_key=_secret_value(inner.openai_api_key),
                api_version=inner.openai_api_version,
                max_retries=0,
                timeout=timeout_seconds,
            )
        if name == "OpenAIEmbeddings":
            return type(inner)(
                model=inner.model,
                api_key=_secret_value(inner.openai_api_key),
                max_retries=0,
                timeout=timeout_seconds,
            )
    except Exception:
        return None
    return None


class DescribedEmbeddings(Embeddings):
    """One embedder for indexing and queries, with receipt metadata.

    Hosted providers are split into token batches and retried on HTTP 429.
    Hashing and local models are called once; they have no rate limit.
    """

    def __init__(
        self,
        inner: Embeddings,
        identity: dict,
        *,
        count_tokens: Optional[Callable[[str], int]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        batch_tokens: int = EMBEDDING_BATCH_TOKEN_BUDGET,
    ):
        self.inner = inner
        self.embedding_provider = str(identity.get("embedding_provider") or "")
        self.embedding_model = str(identity.get("embedding_model") or "")
        self.embedding_dimension = identity.get("embedding_dimension")
        self.measured_dimension: Optional[int] = None
        self._count_tokens = count_tokens or estimate_embedding_tokens
        self._sleep = sleep or time.sleep
        self._batch_tokens = batch_tokens

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        items = list(texts)
        if not items:
            return []
        if self.embedding_provider not in _HOSTED_EMBEDDING_PROVIDERS:
            vectors = self.inner.embed_documents(items)
            self._remember(vectors)
            return vectors
        vectors: List[List[float]] = []
        for batch in pack_embedding_batches(
            items, self._batch_tokens, self._count_tokens
        ):
            vectors.extend(self._embed_with_retry(batch))
        return vectors

    def embed_query(self, text: str) -> List[float]:
        """Embed one student question with a short timeout and tiny backoff.

        Index builds call ``embed_documents``, which keeps the 8-attempt
        backoff. This path is the chat request: at most two attempts, about
        8 seconds per attempt, and at most about 2 seconds of sleep total.
        """
        started = time.perf_counter()
        try:
            if self.embedding_provider in _HOSTED_EMBEDDING_PROVIDERS:
                return self._embed_with_retry(
                    [text],
                    attempts=QUERY_EMBED_ATTEMPTS,
                    timeout_seconds=QUERY_EMBED_TIMEOUT_SECONDS,
                    backoff_budget_seconds=QUERY_EMBED_BACKOFF_BUDGET_SECONDS,
                )[0]
            vector = self.inner.embed_query(text)
            self._remember([vector])
            return vector
        finally:
            _note_embedding_ms((time.perf_counter() - started) * 1000.0)

    def _query_embedder(self):
        cached = getattr(self, "_query_embedder_instance", None)
        if cached is not None:
            return cached
        built = _build_query_embedder(self.inner, QUERY_EMBED_TIMEOUT_SECONDS)
        if built is None and type(self.inner).__name__ in {
            "AzureOpenAIEmbeddings",
            "OpenAIEmbeddings",
        }:
            print(
                "Query embedder could not set an 8s timeout; "
                "using the index embedding client"
            )
        embedder = self.inner if built is None else built
        self._query_embedder_instance = embedder
        return embedder

    def _invoke_embed(self, texts: List[str], timeout_seconds: Optional[float]):
        embedder = self.inner if timeout_seconds is None else self._query_embedder()
        return embedder.embed_documents(list(texts))

    def _next_embed_wait(
        self,
        exc: BaseException,
        delay: float,
        spent: float,
        backoff_budget_seconds: Optional[float],
    ) -> Optional[float]:
        """Return the next sleep, or None when the query budget is spent.

        Index builds pass no budget and honor Retry-After. Query embeddings
        cap the total sleep so a 30 second Retry-After cannot stall chat.
        """
        if backoff_budget_seconds is None:
            return _retry_delay_seconds(exc, delay)
        remaining = float(backoff_budget_seconds) - spent
        if remaining <= 0:
            return None
        header = _retry_after_seconds(exc)
        if header is None:
            return min(delay, remaining)
        return min(max(0.0, header), remaining)

    def _embed_with_retry(
        self,
        texts: List[str],
        *,
        attempts: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        backoff_budget_seconds: Optional[float] = None,
    ) -> List[List[float]]:
        attempt_limit = (
            EMBEDDING_RATE_LIMIT_ATTEMPTS if attempts is None else max(1, int(attempts))
        )
        delay = _RETRY_BASE_SECONDS
        spent = 0.0
        last_error: Optional[BaseException] = None
        for attempt in range(attempt_limit):
            try:
                vectors = self._invoke_embed(texts, timeout_seconds)
            except Exception as exc:
                last_error = exc
                if _is_rate_limit(exc):
                    _note_rate_limit()
                if not _is_rate_limit(exc) or attempt + 1 == attempt_limit:
                    raise
                wait = self._next_embed_wait(exc, delay, spent, backoff_budget_seconds)
                if wait is None:
                    raise
                delay = min(delay * 2, _RETRY_MAX_SECONDS)
                spent += wait
                print(
                    "Embedding request was rate limited; "
                    f"retrying {len(texts)} texts in {wait:.0f}s "
                    f"(attempt {attempt + 1} of {attempt_limit})"
                )
                self._sleep(wait)
                continue
            if len(vectors) != len(texts):
                raise RuntimeError(
                    "Embedding provider returned "
                    f"{len(vectors)} vectors for {len(texts)} texts"
                )
            self._remember(vectors)
            return vectors
        if last_error is not None:
            raise last_error
        raise RuntimeError("Embedding request failed")

    def _remember(self, vectors: List[List[float]]) -> None:
        if not vectors:
            return
        width = len(vectors[0])
        if any(len(vector) != width for vector in vectors):
            raise RuntimeError("Embedding provider returned mixed dimensions")
        self.measured_dimension = width


def _create_embeddings() -> DescribedEmbeddings:
    """Create the embeddings instance based on available config.

    The same object is stored on the document processor and passed to Chroma
    for both indexing and query-time search.
    """
    provider = getattr(Config, "EMBEDDING_PROVIDER", "hashing").strip().lower()
    if provider not in _EMBEDDING_PROVIDERS:
        raise ValueError(
            f"Unsupported EMBEDDING_PROVIDER '{provider}'. "
            f"Choose one of: {', '.join(sorted(_EMBEDDING_PROVIDERS))}"
        )

    identity = Config.resolved_embedding()
    resolved = identity["embedding_provider"]
    if resolved == "hashing":
        inner: Embeddings = HashingEmbeddings(
            dimensions=int(identity.get("embedding_dimension") or 2048)
        )
    elif resolved == "local":
        from langchain_huggingface import HuggingFaceEmbeddings

        inner = HuggingFaceEmbeddings(
            model_name=identity.get("embedding_model") or Config.LOCAL_EMBEDDING_MODEL
        )
    elif resolved == "azure_openai":
        if not Config._azure_embeddings_configured():
            raise ValueError(
                "EMBEDDING_PROVIDER=azure_openai requires AZURE_OPENAI_API_KEY, "
                "AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
            )
        from langchain_openai import AzureOpenAIEmbeddings

        # max_retries=0 lets the batch wrapper own 429 backoff. The SDK's
        # short retries return before a tokens-per-minute window resets.
        inner = AzureOpenAIEmbeddings(
            azure_deployment=Config.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_API_KEY,
            api_version=Config.AZURE_OPENAI_API_VERSION,
            max_retries=0,
        )
    elif resolved == "openai":
        if not Config._configured_secret(Config.OPENAI_API_KEY):
            raise ValueError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        from langchain_openai import OpenAIEmbeddings

        inner = OpenAIEmbeddings(
            api_key=Config.OPENAI_API_KEY,
            model=identity.get("embedding_model") or Config.OPENAI_EMBEDDING_MODEL,
            max_retries=0,
        )
    else:
        raise ValueError(
            f"Unsupported EMBEDDING_PROVIDER '{provider}'. "
            f"Choose one of: {', '.join(sorted(_EMBEDDING_PROVIDERS))}"
        )
    return DescribedEmbeddings(inner, identity)


def _is_missing_collection(exc: BaseException) -> bool:
    try:
        import chromadb.errors

        if isinstance(exc, chromadb.errors.NotFoundError):
            return True
    except Exception:
        pass
    text = str(exc).lower()
    return "does not exist" in text or "not found" in text


def _existing_collection(client, name: str):
    try:
        return client.get_collection(name, embedding_function=None)
    except Exception as exc:
        if _is_missing_collection(exc):
            return None
        raise


def _collection_identity(collection) -> Optional[str]:
    if collection is None:
        return None
    value = getattr(collection, "id", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def restore_previous_collection(client, live_name: str) -> None:
    """Put the pre-swap collection back if a crash landed between renames."""
    if _existing_collection(client, live_name) is not None:
        return
    previous = _existing_collection(client, previous_collection_name(live_name))
    if previous is None:
        return
    previous.modify(name=live_name)


def promote_collection(client, staging_name: str, live_name: str) -> str:
    """Point ``live_name`` at ``staging_name`` only after staging exists.

    The live collection is renamed aside first. If publishing staging fails,
    that previous collection is renamed back. It is deleted only after the
    new collection is serving ``live_name``.
    """
    staging = _existing_collection(client, staging_name)
    if staging is None:
        raise RuntimeError(f"Staging collection {staging_name} is missing")
    previous_name = previous_collection_name(live_name)
    if _existing_collection(client, previous_name) is not None:
        client.delete_collection(previous_name)
    live = _existing_collection(client, live_name)
    moved_live = False
    try:
        if live is not None:
            live.modify(name=previous_name)
            moved_live = True
        staging.modify(name=live_name)
    except Exception:
        if moved_live and _existing_collection(client, live_name) is None:
            restored = _existing_collection(client, previous_name)
            if restored is not None:
                restored.modify(name=live_name)
        raise
    if moved_live:
        try:
            client.delete_collection(previous_name)
        except Exception as error:
            print(f"Promoted {live_name} but could not delete {previous_name}: {error}")
    promoted = _existing_collection(client, live_name)
    identity = _collection_identity(promoted)
    if not identity:
        raise RuntimeError(f"Promoted collection {live_name} has no id")
    return identity


def _first_existing_dir(candidates: list[str], fallback: str) -> str:
    for d in candidates:
        try:
            if d and os.path.isdir(d):
                return d
        except Exception:
            pass
    return fallback


DEFAULT_VECTOR_COLLECTION = "advising_documents"
NON_DOCUMENT_DIRECTORIES = {"meta", "scrape", "__pycache__"}


class DocumentProcessor:
    """Handles loading and processing advising documents."""

    def __init__(self, docs_path: Optional[str] = None):
        fallback = "./data/advising_docs"
        configured_docs_path = docs_path or getattr(Config, "DOCS_PATH", None)
        self._explicit_docs_path = bool(configured_docs_path)
        if self._explicit_docs_path:
            # An explicit path is authoritative, even when it does not exist
            # yet. Silently falling back to a different corpus makes a typo
            # dangerous during a production index rebuild.
            self.docs_path = str(configured_docs_path)
        else:
            release_docs = str(
                Path(__file__).resolve().parent.parent / "data" / "advising_docs"
            )
            self.docs_path = _first_existing_dir(
                [
                    release_docs,
                    os.path.join(os.getcwd(), "data", "advising_docs"),
                    "/home/site/wwwroot/data/advising_docs",
                    fallback,
                ],
                fallback,
            )
        self.docs_path = self._canonical_path(self.docs_path)
        self.embeddings = _create_embeddings()
        self.vector_store = None
        self.active_collection_id: Optional[str] = None
        self.loaded_documents: List[Document] = []
        self.collection_name = os.getenv(
            "VECTOR_COLLECTION_NAME",
            getattr(Config, "VECTOR_COLLECTION_NAME", DEFAULT_VECTOR_COLLECTION),
        )
        self._index_lock = threading.RLock()

    @staticmethod
    def _canonical_path(path: str) -> str:
        """Return a stable absolute path for file and directory comparisons."""
        try:
            return str(Path(path).expanduser().resolve())
        except (OSError, RuntimeError, TypeError):
            return os.path.abspath(os.path.expanduser(str(path)))

    def _search_directories(self) -> List[str]:
        """Return existing, canonical document roots in deterministic order."""
        candidates = [self.docs_path]

        roots: List[str] = []
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            canonical = self._canonical_path(candidate)
            if canonical in seen or not os.path.isdir(canonical):
                continue
            seen.add(canonical)
            roots.append(canonical)
        return roots

    def scan_source_files(self) -> dict:
        """Count files on disk (pre-index), useful for debugging deployments."""
        roots = self._search_directories()
        txt = 0
        pdf = 0
        for root in roots:
            try:
                if not os.path.isdir(root):
                    continue
                txt += sum(
                    1
                    for path in Path(root).rglob("*.txt")
                    if self._is_document_path(path, root)
                )
                pdf += sum(
                    1
                    for path in Path(root).rglob("*.pdf")
                    if self._is_document_path(path, root)
                )
            except Exception:
                continue
        return {
            "roots": roots,
            "txt_files": txt,
            "pdf_files": pdf,
            "total_files": txt + pdf,
        }

    @staticmethod
    def _is_document_path(path: Path, root: str) -> bool:
        """Exclude crawler controls, metadata, caches, and other non-evidence."""
        try:
            relative = path.relative_to(root)
        except ValueError:
            return False
        return not any(
            part.casefold() in NON_DOCUMENT_DIRECTORIES for part in relative.parts[:-1]
        )

    def vector_store_count(self) -> Optional[int]:
        """Return number of indexed chunks in Chroma, if available."""
        with self._index_lock:
            try:
                self._align_open_collection()
                if not self.vector_store:
                    return None
                # Chroma's underlying collection supports count()
                collection = self.vector_store._collection  # type: ignore[attr-defined]
                return int(collection.count())
            except Exception:
                return None

    def _load_from_directory(self, directory: str) -> List[Document]:
        """Load .txt and .pdf files from a single directory."""
        if not os.path.isdir(directory):
            return []

        documents: List[Document] = []

        files = sorted(
            (
                path
                for path in Path(directory).rglob("*")
                if path.is_file()
                and path.suffix.casefold() in {".txt", ".pdf"}
                and self._is_document_path(path, directory)
            ),
            key=lambda path: path.relative_to(directory).as_posix().casefold(),
        )
        for path in files:
            # This legacy fixture describes the MLIS program, not the ISDA
            # program served by this application. Keep the fixture available
            # for explicitly requested local demos, but never index it as
            # advising evidence.
            if path.name == "degree_requirements.txt":
                try:
                    preview = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    preview = ""
                if "Master's of Library and Information Science (MLIS)" in preview:
                    print(f"Skipping legacy non-ISDA sample document: {path}")
                    continue

            source = self._canonical_path(str(path))
            try:
                if path.suffix.casefold() == ".txt":
                    try:
                        loader = TextLoader(
                            source, encoding="utf-8", autodetect_encoding=True
                        )
                    except TypeError:  # compatibility with older langchain versions
                        loader = TextLoader(source, encoding="utf-8")
                else:
                    loader = PyPDFLoader(source)

                for document in loader.load():
                    document.metadata = dict(document.metadata or {})
                    companion = self._load_companion_metadata(path, directory)
                    if companion:
                        document.metadata.update(companion)
                    document.metadata["source"] = source
                    documents.append(document)
            except Exception as e:
                print(f"Error loading {source}: {e}")

        return documents

    @staticmethod
    def _load_companion_metadata(path: Path, root: str) -> dict:
        """Load optional YAML metadata beside a document or under ``meta/``."""
        candidates = [
            path.with_suffix(".yml"),
            path.with_suffix(".yaml"),
            Path(root) / "meta" / f"{path.stem}.yml",
            Path(root) / "meta" / f"{path.stem}.yaml",
        ]
        for metadata_path in candidates:
            if not metadata_path.is_file():
                continue
            try:
                loaded = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError) as error:
                print(f"Error loading metadata {metadata_path}: {error}")
                return {}
            return loaded if isinstance(loaded, dict) else {}
        return {}

    def load_documents(self) -> List[Document]:
        """Load documents from all configured directories."""
        documents: List[Document] = []
        seen_documents = set()

        for directory in self._search_directories():
            loaded = self._load_from_directory(directory)
            if loaded:
                print(f"Loaded {len(loaded)} documents from {directory}")
                for document in loaded:
                    metadata = document.metadata or {}
                    identity = (
                        self._canonical_path(str(metadata.get("source", ""))),
                        str(metadata.get("page", "")),
                        document.page_content,
                    )
                    if identity in seen_documents:
                        continue
                    seen_documents.add(identity)
                    documents.append(document)

        if not documents:
            print("No documents found in any directory.")

        self.loaded_documents = documents
        return documents

    def split_documents(
        self, documents: List[Document], chunk_size: int = 600, chunk_overlap: int = 150
    ) -> List[Document]:
        """Split documents into chunks optimised for advising-style Q&A retrieval."""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        split_docs = splitter.split_documents(documents)
        print(
            f"Split into {len(split_docs)} chunks "
            f"(size={chunk_size}, overlap={chunk_overlap})"
        )
        return split_docs

    def _document_id(self, document: Document) -> str:
        """Generate a stable ID so a rebuild has deterministic chunk keys."""
        metadata = {
            str(key): str(value)
            for key, value in sorted(
                (document.metadata or {}).items(), key=lambda item: str(item[0])
            )
        }
        payload = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        payload = f"{payload}\0{document.page_content}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _restore_live_collection(self, persist_directory: str, live_name: str) -> None:
        import chromadb

        restore_previous_collection(
            chromadb.PersistentClient(path=persist_directory), live_name
        )

    def _discard_named_collection(self, persist_directory: str, name: str) -> None:
        """Delete one collection. A missing collection is not an error."""
        store = Chroma(
            collection_name=name,
            persist_directory=persist_directory,
            embedding_function=self.embeddings,
        )
        try:
            store.delete_collection()
        except Exception as error:
            if _is_missing_collection(error):
                return
            print(f"Could not discard collection {name}: {error}")
            raise

    def create_vector_store(self, documents: List[Document]) -> Chroma:
        """Publish a new collection only after every embedding call succeeds.

        Embeddings are written to a staging collection. The live collection
        stays in place until staging is renamed over it. A 429 or any other
        failure deletes staging and leaves the previous collection queryable.
        """
        if not documents:
            raise ValueError("Cannot create a vector store without documents")

        persist_directory = Config.VECTOR_STORE_PATH
        os.makedirs(persist_directory, exist_ok=True)
        live_name = self.collection_name
        staging_name = staging_collection_name(live_name)
        self._restore_live_collection(persist_directory, live_name)
        self._discard_named_collection(persist_directory, staging_name)

        unique_documents = []
        unique_ids = []
        seen_ids = set()
        for document in documents:
            document_id = self._document_id(document)
            if document_id in seen_ids:
                continue
            seen_ids.add(document_id)
            unique_documents.append(document)
            unique_ids.append(document_id)

        staging_store = Chroma(
            collection_name=staging_name,
            persist_directory=persist_directory,
            embedding_function=self.embeddings,
        )
        try:
            staging_store.add_documents(unique_documents, ids=unique_ids)
            promote_collection(staging_store._client, staging_name, live_name)
        except Exception:
            self._discard_named_collection(persist_directory, staging_name)
            raise

        self.vector_store = Chroma(
            collection_name=live_name,
            persist_directory=persist_directory,
            embedding_function=self.embeddings,
        )
        self.active_collection_id = _collection_identity(
            getattr(self.vector_store, "_collection", None)
        )
        persist = getattr(self.vector_store, "persist", None)
        if callable(persist):
            persist()
        print(f"Vector store created at {persist_directory}")
        return self.vector_store

    def load_vector_store(self) -> Optional[Chroma]:
        """
        Load an existing vector store.

        Returns:
            Chroma vector store if it exists, None otherwise
        """
        with self._index_lock:
            persist_directory = Config.VECTOR_STORE_PATH

            if not os.path.exists(persist_directory):
                return None

            try:
                self._restore_live_collection(persist_directory, self.collection_name)
                self.vector_store = Chroma(
                    collection_name=self.collection_name,
                    persist_directory=persist_directory,
                    embedding_function=self.embeddings,
                )
                self.active_collection_id = _collection_identity(
                    getattr(self.vector_store, "_collection", None)
                )
                print(f"Loaded vector store from {persist_directory}")
                return self.vector_store
            except Exception as e:
                print(f"Error loading vector store: {e}")
                return None

    def _align_open_collection(self) -> None:
        """Reload when another worker has published a new collection.

        The receipt is written only after a successful swap. Until it names a
        collection id, keep the store already open (including a legacy index).
        """
        try:
            from .durable_storage import receipt_collection_id
        except ImportError:  # pragma: no cover - src/ on sys.path
            from durable_storage import receipt_collection_id

        expected = receipt_collection_id(Config.VECTOR_STORE_PATH)
        current = getattr(self, "active_collection_id", None)
        if self.vector_store is not None and (not expected or expected == current):
            return
        self.load_vector_store()

    def _snapshot_aligned_store(self):
        """Align to the live collection, then release the lock.

        Staging swap (promote / restore) and this snapshot share
        ``_index_lock``. The network embedding call happens later, on the
        returned store, so a 429 cannot block the swap and the swap cannot
        make the student wait out embedding retries. Hybrid chunk text is
        cached under the lock by collection id before that call.
        """
        with self._index_lock:
            self._align_open_collection()
            store = self.vector_store
            collection_id = getattr(self, "active_collection_id", None)
            if store is not None:
                _prime_hybrid_records(store, collection_id)
            return store, collection_id

    def retrieve_relevant_docs(self, query: str, k: int = 3) -> List[Document]:
        """
        Retrieve relevant documents for a query.

        Args:
            query: Search query
            k: Number of documents to retrieve

        Returns:
            List of relevant documents
        """
        if not isinstance(query, str) or not query.strip():
            return []
        store, collection_id = self._snapshot_aligned_store()
        if not store:
            return []

        try:
            k = max(1, min(int(k), 20))
            # None keeps vector-only search. That is the production path
            # when HYBRID_RETRIEVAL is unset or false.
            hybrid_hits = _optional_hybrid_retrieve(
                store, query, k, collection_id=collection_id
            )
            if hybrid_hits is not None:
                return hybrid_hits
            results = store.similarity_search(query, k=k)
            return results
        except Exception as e:
            print(f"Error retrieving documents: {e}")
            return []

    def process_and_index(self) -> Optional[Chroma]:
        """Load, split, and atomically replace the advising index."""
        with self._index_lock:
            print(f"Processing documents from {self.docs_path}...")
            documents = self.load_documents()
            if not documents:
                print("No documents found. Proceeding without document retrieval.")
                return None
            split_docs = self.split_documents(documents)
            return self.create_vector_store(split_docs)


def setup_sample_docs() -> None:
    """Create the legacy demo fixture only when explicitly enabled.

    The fixture is intentionally not part of the production advising corpus;
    set ``ALLOW_SAMPLE_DOCS=true`` for a local demo only.
    """
    if os.getenv("ALLOW_SAMPLE_DOCS", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        print("Sample documents disabled; use official advising documents instead.")
        return

    docs_dir = os.getenv(
        "DOCS_PATH",
        str(Path(__file__).resolve().parent.parent / "data" / "advising_docs"),
    )
    os.makedirs(docs_dir, exist_ok=True)

    sample_content = """# iSchool Degree Requirements

## Master's of Library and Information Science (MLIS)

### Core Courses (36 units)
- INFO 200: Foundations of Information and Library Science
- INFO 203: Organization of Information
- INFO 210: Information Policy and Management
- INFO 211: Research Methods
- INFO 212: Knowledge Management
- INFO 213: Digital Information Services
- INFO 230: Advanced Information Architecture
- INFO 240: User Experience Research
- INFO 250: Capstone Project (6 units)

### Electives (18 units)
Students choose from:
- INFO 260: Digital Libraries
- INFO 265: Data Curation and Preservation
- INFO 270: Archives and Records Management
- INFO 275: Special Collections
- INFO 280: Information Organizations
- INFO 285: Information Law and Ethics

### Prerequisites
- Bachelor's degree from accredited institution
- Minimum 2.75 undergraduate GPA
- TOEFL (if international)

### Timeline
Most students complete MLIS in 2 years (full-time) or 3-4 years (part-time)

## Graduation Requirements

1. Complete all required core courses
2. Complete 18 units of electives
3. Maintain 3.0 GPA in graduate courses
4. Submit graduation petition in final semester
5. Complete capstone project with passing grade

## Course Scheduling Tips

- Take INFO 200 first (prerequisite for most courses)
- Spread out core courses across semesters
- Take 2-3 courses per semester for full-time students
- Part-time students can take 1-2 courses per semester
- Check prerequisites before registering

## Advising

Contact the iSchool Advising Office:
- Email: advising@sjsu.edu
- Phone: (408) 924-2470
- Office: Washington Square Hall, Room 108
- Hours: Monday-Friday, 9am-4pm
"""

    sample_file = os.path.join(docs_dir, "degree_requirements.txt")

    if not os.path.exists(sample_file):
        with open(sample_file, "w", encoding="utf-8") as f:
            f.write(sample_content)
        print(f"Created sample document: {sample_file}")


def _hybrid_api():
    """Import hybrid helpers lazily so vector-only chat can skip the module."""
    try:
        from .hybrid_retrieval import (
            hybrid_retrieval_enabled,
            hybrid_retrieve,
            prime_collection_records,
        )
    except ImportError:  # pragma: no cover - src/ on sys.path
        from hybrid_retrieval import (
            hybrid_retrieval_enabled,
            hybrid_retrieve,
            prime_collection_records,
        )
    return hybrid_retrieval_enabled, hybrid_retrieve, prime_collection_records


def _prime_hybrid_records(vector_store, collection_id) -> None:
    """Fill the chunk cache while the index lock is still held.

    A miss here is not fatal: hybrid retrieval reads the collection again
    after the lock is released. The cache key is the live collection id, so
    a staging swap that publishes a new id does not reuse these chunks.
    """
    try:
        hybrid_retrieval_enabled, _hybrid_retrieve, prime_collection_records = (
            _hybrid_api()
        )
    except Exception as error:
        print(f"Hybrid retrieval unavailable: {error}")
        return
    try:
        if not hybrid_retrieval_enabled():
            return
        prime_collection_records(vector_store, collection_id)
    except Exception as error:
        print(f"Hybrid record cache skipped: {error}")


def _optional_hybrid_retrieve(vector_store, query: str, k: int, collection_id=None):
    """Return fused hits when hybrid retrieval is on, otherwise None.

    The import stays inside the helper so the vector-only path does not
    require this module at process start. A missing helper leaves retrieval
    on similarity_search.
    """
    try:
        hybrid_retrieval_enabled, hybrid_retrieve, _prime = _hybrid_api()
    except Exception as error:
        print(f"Hybrid retrieval unavailable: {error}")
        return None
    if not hybrid_retrieval_enabled():
        return None
    return hybrid_retrieve(vector_store, query, k, collection_id=collection_id)
