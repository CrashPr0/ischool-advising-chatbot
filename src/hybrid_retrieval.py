"""BM25 + vector retrieval fused with reciprocal rank fusion.

``HYBRID_RETRIEVAL`` defaults to on. Set it to false for vector search only.
The lexical side is local Okapi BM25 (no extra package, no API calls). Course
codes are rewritten so ``ISDA 150`` and ``ISDA150`` share one token. Requisite
labels share one token too, so ``Prerequisite(s)`` matches ``prerequisites``.
"""

from __future__ import annotations

import math
import os
import re
from typing import Optional, Sequence

from langchain_core.documents import Document

# Cormack, Clarke, and Buettcher, SIGIR 2009. Rank is 1-based.
# Vector neighbors use the paper's constant. The lexical list uses a smaller
# constant so an exact course-code hit is not pushed out of the chat top-k by
# hashing neighbors that only share the word "prerequisites".
VECTOR_RRF_K = 60
LEXICAL_RRF_K = 20
RRF_K = VECTOR_RRF_K
# Candidate depth for each list. The advising index is small; this is enough
# for a course-code chunk that hashing ranks well below the chat top-k.
CANDIDATE_POOL = 20

_TRUE_VALUES = {"1", "true", "yes", "on"}
# Same question words the hashing embedder drops, so BM25 spends its weight
# on course codes and advising terms instead of "what" / "the" / "for".
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
# Department letters, optional space, then 2–3 digits and an optional suffix
# letter (ISDA 150, ISDA150, ISDA 20A). The lookaround stops the match from
# eating a longer token such as a year or a two-letter suffix (100WB).
_COURSE_CODE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{2,6})\s*(\d{2,3}[A-Za-z]?)(?![A-Za-z0-9])"
)
_TOKEN = re.compile(r"[a-z0-9]+")
# Longer forms first. Parenthetical plurals such as Prerequisite(s) are one
# label, not the stem plus a stray "s". pre/corequisite is its own label.
_REQUISITE_FORM = re.compile(
    r"(?<![A-Za-z])(?:"
    r"pre\s*/\s*corequisite(?:s|\(s\))?"
    r"|pre\s*/\s*coreq(?:s|\(s\))?"
    r"|corequisite(?:s|\(s\))?"
    r"|coreq(?:s|\(s\))?"
    r"|prerequisite(?:s|\(s\))?"
    r"|prereq(?:s|\(s\))?"
    r")(?![A-Za-z])",
    re.IGNORECASE,
)


def hybrid_retrieval_enabled() -> bool:
    """Return whether this process should fuse BM25 with vector search.

    Unset enables fusion. Empty and any value other than true/yes/on/1 keep
    the vector-only path. Read on each call so tests and operators can change
    the flag without restarting the interpreter.
    """
    raw = os.getenv("HYBRID_RETRIEVAL", "true")
    return raw.strip().lower() in _TRUE_VALUES


def canonical_course_code(department: str, number: str) -> str:
    """Return the single token used for a department-plus-number code."""
    return f"{department.casefold()}{number.casefold()}"


def compact_course_codes(text: str) -> str:
    """Collapse spaced and compact course codes onto one token."""

    def replace(match: re.Match) -> str:
        return canonical_course_code(match.group(1), match.group(2))

    return _COURSE_CODE.sub(replace, text)


def expand_course_codes(text: str) -> str:
    """Spell compact codes with a space so hashing sees the corpus tokens.

    Feature hashing splits on ``[a-z0-9]+``, so a stored ``ISDA 150`` becomes
    ``isda`` and ``150``. A query written ``ISDA150`` would otherwise hash a
    token the index never stored.
    """

    def replace(match: re.Match) -> str:
        return f"{match.group(1)} {match.group(2)}"

    return _COURSE_CODE.sub(replace, text)


def _canonical_requisite(match: re.Match) -> str:
    """Map one requisite label onto the token its family shares."""
    raw = re.sub(r"[\s()]", "", match.group(0).casefold())
    if raw.startswith("pre/"):
        return "precorequisite"
    if raw.startswith("core"):
        return "corequisite"
    return "prerequisite"


def normalize_requisite_words(text: str) -> str:
    """Rewrite requisite spellings before the token split."""
    return _REQUISITE_FORM.sub(_canonical_requisite, text)


def _lighten_plural(token: str) -> str:
    """Drop one trailing plural s from an alphabetic token.

    Tokens that contain a digit are course codes (``isda20a``, ``isda150``)
    and are left unchanged, including a code whose number ends in s.
    """
    if any(character.isdigit() for character in token):
        return token
    if (
        len(token) >= 5
        and token.endswith("s")
        and not token.endswith(("ss", "us", "is"))
    ):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Tokenize text after requisite and course-code normalization."""
    if not isinstance(text, str):
        return []
    normalized = compact_course_codes(normalize_requisite_words(text)).casefold()
    return [
        _lighten_plural(token)
        for token in _TOKEN.findall(normalized)
        if token not in _STOPWORDS
    ]


class BM25Index:
    """Okapi BM25 over an in-memory tokenized corpus.

    IDF is the non-negative Lucene form
    ``ln(1 + (N - df + 0.5) / (df + 0.5))``. Query term frequency is binary.
    """

    def __init__(
        self,
        tokenized_corpus: Sequence[Sequence[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(tokenized_corpus)
        self.doc_len = [len(doc) for doc in tokenized_corpus]
        total = sum(self.doc_len)
        self.avgdl = (total / self.corpus_size) if self.corpus_size else 0.0
        self.doc_freqs: list[dict[str, int]] = []
        document_frequency: dict[str, int] = {}
        for doc in tokenized_corpus:
            counts: dict[str, int] = {}
            for token in doc:
                counts[token] = counts.get(token, 0) + 1
            self.doc_freqs.append(counts)
            for token in counts:
                document_frequency[token] = document_frequency.get(token, 0) + 1
        self.idf = {
            token: math.log(1.0 + (self.corpus_size - freq + 0.5) / (freq + 0.5))
            for token, freq in document_frequency.items()
        }

    def scores(self, query_tokens: Sequence[str]) -> list[float]:
        """Return one BM25 score per corpus document."""
        scores = [0.0] * self.corpus_size
        if not self.corpus_size or self.avgdl <= 0:
            return scores
        seen: set[str] = set()
        for token in query_tokens:
            if token in seen:
                continue
            seen.add(token)
            idf = self.idf.get(token)
            if not idf:
                continue
            for index, counts in enumerate(self.doc_freqs):
                freq = counts.get(token, 0)
                if not freq:
                    continue
                length_norm = 1.0 - self.b + self.b * self.doc_len[index] / self.avgdl
                denominator = freq + self.k1 * length_norm
                scores[index] += idf * (freq * (self.k1 + 1.0)) / denominator
        return scores

    def top_indices(self, query_tokens: Sequence[str], limit: int) -> list[int]:
        """Return document indexes with a positive score, best first."""
        if limit <= 0:
            return []
        ranked = sorted(
            (
                (score, index)
                for index, score in enumerate(self.scores(query_tokens))
                if score > 0
            ),
            key=lambda item: (-item[0], item[1]),
        )
        return [index for _score, index in ranked[:limit]]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    *,
    rrf_k: int = RRF_K,
    rrf_ks: Optional[Sequence[int]] = None,
    limit: int,
    tie_break: Optional[Sequence[str]] = None,
) -> list[str]:
    """Fuse ranked id lists with reciprocal rank fusion.

    Sort key is the RRF score, then the best rank in any list, then the
    position in ``tie_break`` (the BM25 order), then the id. The lexical
    tie-break only matters when two chunks have the same RRF score, which is
    what happens when each retriever's top hit is absent from the other list.
    ``rrf_ks`` sets a constant per list (vector, then BM25).
    """
    if limit <= 0:
        return []
    constants = list(rrf_ks) if rrf_ks is not None else [rrf_k] * len(rankings)
    if len(constants) != len(rankings):
        raise ValueError("rrf_ks must provide one constant per ranking")
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for ranking, constant in zip(rankings, constants):
        seen: set[str] = set()
        for rank, doc_id in enumerate(ranking, start=1):
            if not doc_id or doc_id in seen:
                continue
            seen.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (constant + rank)
            previous = best_rank.get(doc_id)
            if previous is None or rank < previous:
                best_rank[doc_id] = rank
    lexical = {doc_id: rank for rank, doc_id in enumerate(tie_break or [], start=1)}
    ordered = sorted(
        scores,
        key=lambda doc_id: (
            -scores[doc_id],
            best_rank[doc_id],
            lexical.get(doc_id, 10**9),
            doc_id,
        ),
    )
    return ordered[:limit]


def _clamp_k(k: int) -> int:
    try:
        value = int(k)
    except (TypeError, ValueError):
        value = 3
    return max(1, min(value, 20))


def _collection_cache_key(vector_store, collection_id: Optional[str]) -> Optional[str]:
    """Return the live collection id, which changes when a staging swap publishes."""
    if collection_id is not None and str(collection_id).strip():
        return str(collection_id).strip()
    collection = getattr(vector_store, "_collection", None)
    value = getattr(collection, "id", None) if collection is not None else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_collection_records(collection) -> list[tuple[str, str, dict]]:
    """Read chunk id, text, and metadata from one Chroma collection."""
    payload = collection.get(include=["documents", "metadatas"])
    ids = payload.get("ids") or []
    documents = payload.get("documents") or []
    metadatas = payload.get("metadatas") or []
    records: list[tuple[str, str, dict]] = []
    for doc_id, content, metadata in zip(ids, documents, metadatas):
        if isinstance(content, str) and content.strip():
            records.append((str(doc_id), content, dict(metadata or {})))
    return records


def _collection_records(
    vector_store, collection_id: Optional[str] = None
) -> list[tuple[str, str, dict]]:
    """Return chunk rows, reusing them until the collection id changes.

    Chat calls this for every question. The advising index is a couple of
    hundred chunks; fetching them on every request dominated nothing next to
    a 429, but it is needless local work and it races a staging rename.
    The cache lives on the open store and is keyed by the collection id the
    processor snapshotted under the index lock. A successful swap publishes
    a new id, so the next request reads the new chunks.
    """
    collection = getattr(vector_store, "_collection", None)
    if collection is None:
        return []
    cache_key = _collection_cache_key(vector_store, collection_id)
    cached = getattr(vector_store, "_collection_records_cache", None)
    if (
        cache_key
        and isinstance(cached, tuple)
        and len(cached) == 2
        and cached[0] == cache_key
        and isinstance(cached[1], list)
    ):
        return cached[1]
    records = _read_collection_records(collection)
    if cache_key:
        try:
            setattr(vector_store, "_collection_records_cache", (cache_key, records))
        except (AttributeError, TypeError):
            pass
    return records


def prime_collection_records(vector_store, collection_id: Optional[str] = None) -> None:
    """Load chunk text once for this collection id while the index lock is held."""
    _collection_records(vector_store, collection_id)


def _documents_for_ids(doc_ids, by_id, limit: int) -> list[Document]:
    documents: list[Document] = []
    for doc_id in doc_ids:
        row = by_id.get(doc_id)
        if row is None:
            continue
        content, metadata = row
        documents.append(Document(page_content=content, metadata=metadata))
        if len(documents) >= limit:
            break
    return documents


def _bm25_for(vector_store, records: Sequence[tuple[str, str, dict]]) -> BM25Index:
    """Reuse the BM25 index until the collection's ids change."""
    signature = tuple(doc_id for doc_id, _content, _metadata in records)
    cached = getattr(vector_store, "_hybrid_bm25_cache", None)
    if (
        isinstance(cached, tuple)
        and len(cached) == 2
        and cached[0] == signature
        and isinstance(cached[1], BM25Index)
    ):
        return cached[1]
    index = BM25Index([tokenize(content) for _doc_id, content, _metadata in records])
    try:
        setattr(vector_store, "_hybrid_bm25_cache", (signature, index))
    except (AttributeError, TypeError):
        pass
    return index


def _vector_ids(
    vector_store,
    query: str,
    pool: int,
    by_content: dict[str, list[str]],
) -> list[str]:
    vector_docs = vector_store.similarity_search(expand_course_codes(query), k=pool)
    chosen: list[str] = []
    for document in vector_docs:
        content = getattr(document, "page_content", "")
        for doc_id in by_content.get(content, []):
            if doc_id not in chosen:
                chosen.append(doc_id)
    return chosen


def hybrid_retrieve(
    vector_store, query: str, k: int = 3, collection_id: Optional[str] = None
) -> list[Document]:
    """Return the fused top-k chunks for one query.

    Falls back to vector search if the lexical index cannot be read, so a
    hybrid failure does not turn into an empty answer. If the query
    embedding itself fails (429, timeout), return the BM25 ranking instead
    of failing the request.
    """
    k = _clamp_k(k)
    if vector_store is None or not isinstance(query, str) or not query.strip():
        return []
    try:
        records = _collection_records(vector_store, collection_id)
        if not records:
            return list(vector_store.similarity_search(query, k=k))
        pool = min(len(records), max(k, CANDIDATE_POOL))
        index = _bm25_for(vector_store, records)
        bm25_positions = index.top_indices(tokenize(query), pool)
        bm25_ids = [records[position][0] for position in bm25_positions]
        by_content: dict[str, list[str]] = {}
        by_id: dict[str, tuple[str, dict]] = {}
        for doc_id, content, metadata in records:
            by_content.setdefault(content, []).append(doc_id)
            by_id[doc_id] = (content, metadata)
        try:
            vector_ids = _vector_ids(vector_store, query, pool, by_content)
        except Exception as error:
            print(f"Query embedding failed; using BM25 only: {error}")
            return _documents_for_ids(bm25_ids, by_id, k)
        fused = reciprocal_rank_fusion(
            [vector_ids, bm25_ids],
            rrf_ks=[VECTOR_RRF_K, LEXICAL_RRF_K],
            limit=k,
            tie_break=bm25_ids,
        )
        return [
            Document(page_content=by_id[doc_id][0], metadata=by_id[doc_id][1])
            for doc_id in fused
            if doc_id in by_id
        ]
    except Exception as error:
        print(f"Hybrid retrieval failed; using vector search: {error}")
        try:
            return list(vector_store.similarity_search(query, k=k))
        except Exception as inner:
            print(f"Error retrieving documents: {inner}")
            return []
