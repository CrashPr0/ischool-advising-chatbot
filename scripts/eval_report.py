"""Evaluate retrieval against saved source pages without LLM or network calls.

Run from the repository root: python -m scripts.eval_report
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

from langchain_core.documents import Document

# Import the processor before Chroma so its sqlite compatibility shim runs first.
from src.document_processor import DocumentProcessor
from src.config import Config
from src.hybrid_retrieval import hybrid_retrieve

ROOT = Path(__file__).resolve().parents[1]


def source_key(url: str) -> str:
    """Normalize page identity, preserving query parameters such as catalog IDs."""
    if not isinstance(url, str) or any(char.isspace() for char in url):
        return ""
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.port not in {None, 80, 443}
        ):
            return ""
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/"),
                parts.query,
                "",
            )
        )
    except ValueError:
        return ""


def load_cases(questions_path: Path, expectations_path: Path) -> list[dict]:
    """Require one explicit source label or exclusion for every curated question."""
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    labels = json.loads(expectations_path.read_text(encoding="utf-8"))
    ids = [row["id"] for row in questions]
    if not ids or len(ids) != len(set(ids)) or set(ids) != set(labels):
        raise ValueError("Question IDs must be unique and match expectation IDs")
    cases = []
    for question in questions:
        label = labels[question["id"]]
        expected = label.get("expected_sources", [])
        reason = label.get("skip_reason", "")
        if (
            not isinstance(expected, list)
            or bool(expected) == bool(reason)
            or any(not source_key(url) for url in expected)
            or not isinstance(reason, str)
            or not isinstance(question.get("question"), str)
            or not question["question"].strip()
        ):
            raise ValueError(f"Invalid retrieval label/question: {question['id']}")
        cases.append({**question, **label})
    return cases


def evaluate(
    cases: Sequence[dict],
    retrieve: Callable[[str, int], Sequence[Document]],
    known_sources: set[str],
    k: int,
    clock: Callable[[], float] = time.perf_counter,
) -> dict:
    """Score distinct expected pages within the first k chunks; retain misses."""
    if not 1 <= k <= 20:
        raise ValueError("k must be between 1 and 20 (the production retrieval limit)")
    known = {source_key(url) for url in known_sources} - {""}
    if not cases or not any(case.get("expected_sources") for case in cases):
        raise ValueError("At least one scored question is required")
    for case in cases:
        missing = {source_key(url) for url in case.get("expected_sources", [])} - known
        if missing:
            raise ValueError(
                f"Expected sources absent from corpus for {case['id']}: {missing}"
            )
    rows = []
    for case in cases:
        start = clock()
        documents = list(retrieve(case["question"], k))[:k]
        elapsed = (clock() - start) * 1000
        sources = [doc.metadata.get("source_url", "") for doc in documents]
        retrieved = {source_key(url) for url in sources} - {""}
        expected = {source_key(url) for url in case.get("expected_sources", [])}
        matched = expected & retrieved
        rows.append(
            {
                "id": case["id"],
                "expected": sorted(expected),
                "retrieved": sorted(retrieved),
                "matched": sorted(matched),
                "skip_reason": case.get("skip_reason", ""),
                "hit": bool(matched) if expected else None,
                "recall": len(matched) / len(expected) if expected else None,
                "valid_links": sum(
                    bool(source_key(url)) and source_key(url) in known
                    for url in sources
                ),
                "link_count": len(sources),
                "latency_ms": elapsed,
            }
        )
    scored = [row for row in rows if row["recall"] is not None]
    latencies = sorted(row["latency_ms"] for row in rows)
    link_count = sum(row["link_count"] for row in rows)
    valid_links = sum(row["valid_links"] for row in rows)
    return {
        "k": k,
        "rows": rows,
        "scored": len(scored),
        "hits": sum(row["hit"] for row in scored),
        "hit_rate": statistics.mean(row["hit"] for row in scored),
        "recall": statistics.mean(row["recall"] for row in scored),
        "valid_links": valid_links,
        "link_count": link_count,
        "link_validity": valid_links / link_count if link_count else None,
        "mean_ms": statistics.mean(latencies),
        "p50_ms": statistics.median(latencies),
        "p95_ms": latencies[math.ceil(len(latencies) * 0.95) - 1],
    }


def render_report(result: dict, provenance: dict) -> str:
    """Render measurements and enough detail to audit every source match."""
    k = result["k"]
    validity = result["link_validity"]
    lines = [
        "# Offline retrieval evaluation",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "Command: `python -m scripts.eval_report --k " + str(k) + "`",
        "",
        "## Measured results",
        "",
        "| Metric | Result |",
        "| --- | --- |",
        f"| Questions retrieved | {len(result['rows'])} |",
        f"| Questions scored | {result['scored']} |",
        f"| Hit rate@{k} | {result['hit_rate']:.2%} "
        f"({result['hits']}/{result['scored']}) |",
        f"| Macro source recall@{k} | {result['recall']:.2%} |",
        "| Offline citation-link validity | "
        + (f"{validity:.2%}" if validity is not None else "N/A")
        + f" ({result['valid_links']}/{result['link_count']}) |",
        f"| Retrieval latency mean / p50 / p95 | {result['mean_ms']:.2f} / "
        f"{result['p50_ms']:.2f} / {result['p95_ms']:.2f} ms |",
        "",
        "## Method and limits",
        "",
        "The existing document loader and default 600-character / 150-overlap splitter "
        "feed a temporary Chroma index with deterministic 2048-dimensional hashing "
        "embeddings. The production hybrid_retrieve function fuses vector and BM25 "
        "rankings, with catalog recency preference enabled and no explicit "
        "program override. "
        "Catalog identity comes from the committed data/catalog_identity.json. "
        "No chat model, provider embeddings, scraping, model downloads, "
        "or HTTP link probes run. "
        "Chroma telemetry is disabled; the app's persistent index is never opened.",
        "",
        "Hit rate is the fraction of scored questions retrieving at least one expected "
        "page. Recall is the mean per-question fraction of distinct expected pages "
        "retrieved. All labeled pages count toward recall (they are not alternatives). "
        "k counts chunks, so duplicate chunks from one page consume slots but cannot "
        "increase recall. Labels were selected from the curated review guidance and "
        "saved source pages, independently of retrieval output.",
        "",
        "All curated questions run through retrieval. Boundary and distress cases "
        "are explicitly excluded from relevance scoring because their response "
        "behavior "
        "is outside this harness's scope; exclusions are shown below. Link validity "
        "and latency include every case. Each returned chunk must carry a well-formed "
        "HTTP(S) source_url matching a loaded corpus page; missing URLs fail. This is "
        "source-link integrity, not live reachability, generated-citation correctness, "
        "answer accuracy, or a safety evaluation. Empty retrieval has zero recall "
        "and no links to validate.",
        "",
        "Latency is one sequential retrieval per question, including the first cold "
        "query/cache build, excluding index construction and imports. p95 uses nearest "
        "rank; p50 uses the median. Timings depend on hardware and cache state. "
        "This small curated set is a regression baseline, not a generalization claim.",
        "",
        "## Provenance",
        "",
    ]
    lines.extend(f"- {key}: `{value}`" for key, value in provenance.items())
    lines.extend(["", "## Per-question evidence", ""])
    for row in result["rows"]:
        lines.extend([f"### {row['id']}", ""])
        if row["recall"] is None:
            lines.append(f"Excluded from relevance scoring: {row['skip_reason']}")
        else:
            lines.append(f"Hit: {row['hit']}; recall@{k}: {row['recall']:.2%}.")
            lines.append(
                "Expected: " + ", ".join(f"<{url}>" for url in row["expected"])
            )
        lines.append(
            "Retrieved: "
            + (", ".join(f"<{url}>" for url in row["retrieved"]) or "(none)")
        )
        lines.extend(
            [
                f"Links: {row['valid_links']}/{row['link_count']}; "
                f"latency: {row['latency_ms']:.2f} ms.",
                "",
            ]
        )
    return "\n".join(lines)


def run_report(docs: Path, questions: Path, expectations: Path, k: int) -> str:
    """Build an isolated index with offline settings scoped to this process."""
    import chromadb
    from chromadb.config import Settings
    from langchain_chroma import Chroma

    cases = load_cases(questions, expectations)
    if not 1 <= k <= 20:
        raise ValueError("k must be between 1 and 20")
    with (
        tempfile.TemporaryDirectory(prefix="advising-eval-") as directory,
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
        if not chunks:
            raise ValueError("Cannot evaluate an empty corpus")
        client = chromadb.PersistentClient(
            path=directory, settings=Settings(anonymized_telemetry=False)
        )
        store = Chroma(
            collection_name="offline-evaluation",
            client=client,
            embedding_function=processor.embeddings,
        )
        try:
            store.add_documents(chunks, ids=[str(i) for i in range(len(chunks))])
            result = evaluate(
                cases,
                lambda query, limit: hybrid_retrieve(store, query, k=limit),
                {doc.metadata.get("source_url", "") for doc in documents},
                k,
            )
            corpus = json.dumps(
                [
                    (
                        doc.page_content,
                        {
                            key: value
                            for key, value in doc.metadata.items()
                            if key != "source"
                        },
                    )
                    for doc in documents
                ],
                sort_keys=True,
            ).encode()
            provenance = {
                "Python": platform.python_version(),
                "Platform": platform.platform(),
                "Documents / chunks": f"{len(documents)} / {len(chunks)}",
                "Corpus content + metadata SHA-256": hashlib.sha256(corpus).hexdigest(),
                "Questions SHA-256": hashlib.sha256(questions.read_bytes()).hexdigest(),
                "Expectations SHA-256": hashlib.sha256(
                    expectations.read_bytes()
                ).hexdigest(),
                "Harness SHA-256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "Requirements SHA-256": hashlib.sha256(
                    (ROOT / "requirements.txt").read_bytes()
                ).hexdigest(),
                "Constraints SHA-256": hashlib.sha256(
                    (ROOT / "constraints.txt").read_bytes()
                ).hexdigest(),
                "Catalog identity SHA-256": hashlib.sha256(
                    (ROOT / "data/catalog_identity.json").read_bytes()
                ).hexdigest(),
            }
            return render_report(result, provenance)
        finally:
            store.delete_collection()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=Path, default=ROOT / "data/advising_docs")
    parser.add_argument(
        "--questions", type=Path, default=ROOT / "evals/advising_questions.json"
    )
    parser.add_argument(
        "--expectations", type=Path, default=ROOT / "evals/retrieval_expectations.json"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "docs/eval/latest.md")
    parser.add_argument("--k", type=int, choices=range(1, 21), default=4)
    args = parser.parse_args()
    report = run_report(args.docs, args.questions, args.expectations, args.k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
