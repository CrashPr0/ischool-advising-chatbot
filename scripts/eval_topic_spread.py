"""Compare retrieval with SUMMARY_CHUNKS / TOPIC_SPREAD off and on.

Run from the repository root: python -m scripts.eval_topic_spread

Builds two temporary hashing-embedding indexes from the saved corpus: page
chunks only, and page chunks plus summary chunks. Every curated question in
evals/ (the scored set used by scripts.eval_report plus the specific and
broad questions in evals/broad_questions.json) is retrieved under four
settings at k = RAG_TOP_K (8):

* baseline: both flags off, chunk-only index
* spread: TOPIC_SPREAD on
* summaries: SUMMARY_CHUNKS on, summary index
* both: both flags on, summary index

``--summarizer offline`` (the default) writes summaries without any model:
the first sentences of each page, and a title list per topic. That measures
retrieval plumbing, not summary quality. ``--summarizer llm`` uses the
configured chat provider (DEFAULT_LLM_PROVIDER) and its credentials, with
the summary cache at SUMMARY_CACHE_PATH (or ``--cache``).

The report also checks flags-off parity: the summary index with both flags
off must return exactly what the chunk-only index returns. Vector search is
exact (brute-force L2 over the stored vectors) here, because Chroma's HNSW
search is approximate: two indexes with identical contents already disagree
on about one curated question in 33, which would hide real differences.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import tempfile
from pathlib import Path
from unittest.mock import patch

from langchain_core.documents import Document

# Import the processor before Chroma so its sqlite compatibility shim runs first.
from src.document_processor import DocumentProcessor
from src.config import Config
from src.hybrid_retrieval import hybrid_retrieve, topic_spread_applies
from src.summary_chunks import (
    SummaryCache,
    build_summary_documents,
    default_summarizer,
    is_summary_metadata,
    summary_cache_path,
    summary_source_urls,
)
from src.topic_clusters import max_per_topic, page_topic

from scripts.eval_report import ROOT, load_cases, source_key

SETTINGS = (
    ("baseline", "chunks", {"SUMMARY_CHUNKS": "false", "TOPIC_SPREAD": "false"}),
    ("spread", "chunks", {"SUMMARY_CHUNKS": "false", "TOPIC_SPREAD": "true"}),
    ("summaries", "summaries", {"SUMMARY_CHUNKS": "true", "TOPIC_SPREAD": "false"}),
    ("both", "summaries", {"SUMMARY_CHUNKS": "true", "TOPIC_SPREAD": "true"}),
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def offline_summarizer(prompt: str) -> str:
    """Deterministic stand-in for the model: extractive, no network."""
    if "Page text:\n" in prompt:
        body = prompt.split("Page text:\n", 1)[1].rsplit("\n\nSummary:", 1)[0]
        title = re.search(r"^Title: (.*)$", prompt, re.MULTILINE)
        lines = [
            line.strip()
            for line in body.splitlines()
            if len(line.strip()) > 40 and not line.lower().startswith("source url")
        ]
        sentences = _SENTENCE.split(" ".join(lines))
        text = " ".join(sentences[:4])[:600]
        return f"{title.group(1) if title else ''}. {text}".strip()
    titles = re.findall(r"^Page: (.*)$", prompt, re.MULTILINE)
    topic = re.search(r'about "([^"]+)" for ([^.]+)\.', prompt)
    label = f"{topic.group(1)} for {topic.group(2)}" if topic else "this topic"
    return f"Official pages about {label}: " + "; ".join(titles) + "."


class ExactSearchStore:
    """A Chroma store whose similarity_search is exhaustive and deterministic."""

    def __init__(self, store):
        import numpy

        self._store = store
        self._collection = store._collection
        payload = store._collection.get(include=["embeddings", "documents", "metadatas"])
        self._ids = [int(value) for value in payload["ids"]]
        order = sorted(range(len(self._ids)), key=self._ids.__getitem__)
        self._vectors = numpy.asarray(payload["embeddings"], dtype=float)[order]
        self._documents = [
            Document(page_content=payload["documents"][i], metadata=dict(payload["metadatas"][i] or {}))
            for i in order
        ]
        self._embed = store.embeddings.embed_query

    def similarity_search(self, query: str, k: int = 4):
        import numpy

        vector = numpy.asarray(self._embed(query), dtype=float)
        distances = ((self._vectors - vector) ** 2).sum(axis=1)
        ranked = sorted(range(len(distances)), key=lambda i: (distances[i], i))
        return [self._documents[i] for i in ranked[:k]]


def load_broad(path: Path) -> tuple[list[dict], list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("specific", [])), list(payload.get("broad", []))


def _doc_topic(document) -> str:
    return page_topic(document.metadata or {})


def score(case: dict, documents) -> dict:
    sources = [doc.metadata.get("source_url", "") for doc in documents]
    retrieved = {source_key(url) for url in sources} - {""}
    covered = set(retrieved)
    for doc in documents:
        if is_summary_metadata(doc.metadata):
            covered.update(source_key(url) for url in summary_source_urls(doc.metadata))
    covered.discard("")
    expected = {source_key(url) for url in case.get("expected_sources", [])}
    topics = [_doc_topic(doc) for doc in documents]
    wanted_topics = set(case.get("expected_topics", []))
    return {
        "id": case["id"],
        "recall": len(expected & retrieved) / len(expected) if expected else None,
        "covered_recall": len(expected & covered) / len(expected) if expected else None,
        "hit": bool(expected & retrieved) if expected else None,
        "topics": sorted(set(topics)),
        "topic_recall": (
            len(wanted_topics & set(topics)) / len(wanted_topics) if wanted_topics else None
        ),
        "summaries": sum(is_summary_metadata(doc.metadata) for doc in documents),
        "signature": [(doc.metadata.get("source_url", ""), doc.page_content) for doc in documents],
    }


def _mean(values) -> float:
    values = [value for value in values if value is not None]
    return statistics.mean(values) if values else 0.0


def run(
    docs: Path,
    questions: Path,
    expectations: Path,
    broad: Path,
    k: int,
    summarizer: str,
    cache: Path | None,
) -> str:
    import chromadb
    from chromadb.config import Settings
    from langchain_chroma import Chroma

    curated = [case for case in load_cases(questions, expectations) if case.get("expected_sources")]
    specific, broad_cases = load_broad(broad)
    exact_cases = curated + specific

    with (
        tempfile.TemporaryDirectory(prefix="advising-spread-eval-") as directory,
        patch.object(Config, "EMBEDDING_PROVIDER", "hashing"),
        patch.dict(
            "os.environ",
            {
                "CATALOG_RECENCY_RERANK": "true",
                "CURRENT_CATALOG_CATOID": "",
                "CURRENT_CATALOG_YEAR": "",
                "LANGSMITH_TRACING": "false",
                "LANGCHAIN_TRACING_V2": "false",
            },
        ),
    ):
        processor = DocumentProcessor(docs_path=str(docs.resolve()))
        documents = processor.load_documents()
        chunks = processor.split_documents(documents)
        if summarizer == "llm":
            summarize = default_summarizer()
            summary_cache = SummaryCache(cache or summary_cache_path())
        else:
            summarize = offline_summarizer
            summary_cache = SummaryCache(None)
        summaries = build_summary_documents(documents, summarize=summarize, cache=summary_cache)
        if summarizer == "llm":
            summary_cache.save()
        client = chromadb.PersistentClient(
            path=directory, settings=Settings(anonymized_telemetry=False)
        )
        stores = {}
        try:
            for name, contents in (("chunks", chunks), ("summaries", chunks + summaries)):
                store = Chroma(
                    collection_name=f"spread-eval-{name}",
                    client=client,
                    embedding_function=processor.embeddings,
                )
                store.add_documents(contents, ids=[str(i) for i in range(len(contents))])
                stores[name] = store
            exact = {name: ExactSearchStore(store) for name, store in stores.items()}

            results: dict[str, dict[str, dict]] = {}
            for setting, store_name, env in SETTINGS:
                with patch.dict(os.environ, env):
                    rows = {}
                    for case in exact_cases + broad_cases:
                        hits = hybrid_retrieve(exact[store_name], case["question"], k=k)
                        rows[case["id"]] = score(case, hits)
                    results[setting] = rows

            with patch.dict(os.environ, {"SUMMARY_CHUNKS": "false", "TOPIC_SPREAD": "false"}):
                parity_breaks = [
                    case["id"]
                    for case in exact_cases + broad_cases
                    if score(
                        case, hybrid_retrieve(exact["summaries"], case["question"], k=k)
                    )["signature"]
                    != results["baseline"][case["id"]]["signature"]
                ]
        finally:
            for store in stores.values():
                store.delete_collection()

    return render(
        results,
        exact_cases,
        broad_cases,
        parity_breaks,
        k=k,
        summarizer=summarizer,
        documents=len(documents),
        chunks=len(chunks),
        summaries=summaries,
    )


def render(results, exact_cases, broad_cases, parity_breaks, *, k, summarizer, documents, chunks, summaries) -> str:
    with patch.dict(os.environ, {"TOPIC_SPREAD": "true"}):
        spread_ids = [case["id"] for case in exact_cases if topic_spread_applies(case["question"])]
    page_level = sum(doc.metadata.get("summary_level") == 1 for doc in summaries)
    lines = [
        "# Summary chunks and topic spread: retrieval comparison",
        "",
        f"Command: `python -m scripts.eval_topic_spread --k {k} --summarizer {summarizer}`",
        "",
        f"Corpus: {documents} source documents, {chunks} page chunks, "
        f"{len(summaries)} summary chunks ({page_level} page, "
        f"{len(summaries) - page_level} topic). k = {k}; "
        f"TOPIC_SPREAD_MAX_PER_TOPIC = {max_per_topic()}. Hashing embeddings + BM25 "
        "through the production hybrid_retrieve, with exact (brute-force) vector "
        "search so runs are deterministic; no rag_chatbot pinning, no answer "
        "generation.",
        "",
    ]
    if summarizer == "offline":
        lines += [
            "**Summaries are an offline extractive stand-in (first sentences of each "
            "page; a title list per topic), not model output.** They test the plumbing "
            "(storage, filtering, citations, spread). Real summary quality needs "
            "`--summarizer llm` with provider credentials.",
            "",
        ]
    lines += [
        f"Flags-off parity (summary index, both flags off, vs chunk-only baseline): "
        + ("**identical for every question**" if not parity_breaks else f"**differs for {', '.join(parity_breaks)}**"),
        "",
        "## Exact-answer questions (curated scored set + specific)",
        "",
        f"Spread applies (broad question, no course code) to {len(spread_ids)} of "
        f"{len(exact_cases)}: {', '.join(spread_ids) or 'none'}. The rest keep the "
        "fused order under TOPIC_SPREAD.",
        "",
        "| Setting | Hit rate | Mean recall | Regressed vs baseline |",
        "| --- | --- | --- | --- |",
    ]
    base = results["baseline"]
    for setting, rows in results.items():
        exact = [rows[case["id"]] for case in exact_cases]
        regressed = [
            case["id"]
            for case in exact_cases
            if rows[case["id"]]["recall"] < base[case["id"]]["recall"]
        ]
        lines.append(
            f"| {setting} | {_mean(row['hit'] for row in exact):.2%} | "
            f"{_mean(row['recall'] for row in exact):.2%} | "
            f"{', '.join(regressed) if regressed else 'none'} |"
        )
    lines += [
        "",
        "## Broad multi-topic questions",
        "",
        "Topic recall: share of the labeled topics present in the top k. Page recall "
        "counts a page only when a returned chunk's own source_url is that page; "
        "covered recall also counts pages listed by a returned topic summary.",
        "",
        "| Setting | Topic recall | Distinct topics | Page recall | Covered recall | Summary chunks in top k |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for setting, rows in results.items():
        broad = [rows[case["id"]] for case in broad_cases]
        lines.append(
            f"| {setting} | {_mean(row['topic_recall'] for row in broad):.2%} | "
            f"{_mean(len(row['topics']) for row in broad):.1f} | "
            f"{_mean(row['recall'] for row in broad):.2%} | "
            f"{_mean(row['covered_recall'] for row in broad):.2%} | "
            f"{_mean(row['summaries'] for row in broad):.1f} |"
        )
    lines += ["", "## Per-question detail", ""]
    for case in exact_cases + broad_cases:
        lines.append(f"### {case['id']}")
        lines.append("")
        lines.append(f"Question: {case['question']}")
        lines.append("")
        for setting, rows in results.items():
            row = rows[case["id"]]
            extra = (
                f", topic recall {row['topic_recall']:.0%}"
                if row["topic_recall"] is not None
                else ""
            )
            lines.append(
                f"- {setting}: recall {row['recall']:.0%}{extra}; "
                f"topics {', '.join(row['topics'])}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs", type=Path, default=ROOT / "data/advising_docs")
    parser.add_argument("--questions", type=Path, default=ROOT / "evals/advising_questions.json")
    parser.add_argument(
        "--expectations", type=Path, default=ROOT / "evals/retrieval_expectations.json"
    )
    parser.add_argument("--broad", type=Path, default=ROOT / "evals/broad_questions.json")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/eval/topic-spread.md")
    parser.add_argument("--k", type=int, choices=range(1, 21), default=8)
    parser.add_argument("--summarizer", choices=("offline", "llm"), default="offline")
    parser.add_argument("--cache", type=Path, default=None)
    args = parser.parse_args()
    report = run(
        args.docs,
        args.questions,
        args.expectations,
        args.broad,
        args.k,
        args.summarizer,
        args.cache,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
