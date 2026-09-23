"""Reproducible side-by-side benchmark for advising models.

The benchmark retrieves one shared context per question and sends the exact
same prompt to both models. This keeps retrieval variance out of the A/B test.
Results are written as JSON for analysis and Markdown for human review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from langchain_core.prompts import PromptTemplate

try:
    from .config import Config
    from .document_processor import DocumentProcessor
    from .llm_providers import LLMFactory
    from .rag_chatbot import RAGAdvisingChatbot
except ImportError:  # pragma: no cover - supports direct script execution
    from config import Config
    from document_processor import DocumentProcessor
    from llm_providers import LLMFactory
    from rag_chatbot import RAGAdvisingChatbot


DEFAULT_QUESTIONS = Path("evals/advising_questions.json")
DEFAULT_JSON_OUTPUT = Path(os.getenv("EVAL_RESULTS_PATH", "evals/results/latest.json"))
DEFAULT_MARKDOWN_OUTPUT = Path("evals/results/latest.md")
DEFAULT_VECTOR_STORE = Path("evals/vector_store")


def load_questions(path: Path) -> list[dict[str, str]]:
    """Load and validate the benchmark question set."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Question file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Question file is not valid JSON: {path}") from exc

    if not isinstance(payload, list) or not payload:
        raise ValueError("Question file must contain a non-empty JSON array")

    questions: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Question {index} must be an object")
        question_id = str(item.get("id", "")).strip()
        question = str(item.get("question", "")).strip()
        if not question_id or not question:
            raise ValueError(f"Question {index} requires non-empty id and question")
        if question_id in seen_ids:
            raise ValueError(f"Duplicate question id: {question_id}")
        seen_ids.add(question_id)
        questions.append(
            {
                "id": question_id,
                "question": question,
                "review_focus": str(item.get("review_focus", "")).strip(),
            }
        )
    return questions


def build_context(documents: Iterable[Any]) -> str:
    """Format retrieved documents exactly once for both models."""
    documents = list(documents)
    if not documents:
        return (
            "No relevant documents were found for this question. Tell the "
            "student you don't have enough information and suggest they "
            "contact the iSchool advising team.\n"
        )

    parts = ["--- Retrieved advising documents ---\n"]
    for index, document in enumerate(documents, 1):
        metadata = getattr(document, "metadata", {}) or {}
        source = metadata.get("source", "unknown")
        content = str(getattr(document, "page_content", "")).strip()
        parts.append(f"[Document {index} — {source}]\n{content}\n")
    parts.append("--- End of documents ---")
    return "\n".join(parts)


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
                text_parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                text_parts.append(item)
        return "\n".join(part for part in text_parts if part).strip()
    return str(content).strip()


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        metadata = getattr(response, "response_metadata", {}) or {}
        usage = metadata.get("token_usage") or metadata.get("usage") or {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    normalized: dict[str, int] = {}
    for target, names in aliases.items():
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else None
            if isinstance(value, int):
                normalized[target] = value
                break
    if "total_tokens" not in normalized and {
        "input_tokens",
        "output_tokens",
    }.issubset(normalized):
        normalized["total_tokens"] = (
            normalized["input_tokens"] + normalized["output_tokens"]
        )
    return normalized


def _retryable_model_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {429, 500, 502, 503, 504}:
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return any(
        marker in name or marker in message
        for marker in ("ratelimit", "rate_limit", "timeout", "temporarily unavailable")
    )


def invoke_model(
    model: Any,
    prompt: str,
    *,
    max_attempts: int = 3,
    retry_delay_seconds: float = 30.0,
) -> dict[str, Any]:
    """Invoke one model with bounded retries for transient provider failures."""
    started = time.perf_counter()
    for attempt in range(1, max_attempts + 1):
        try:
            response = model.invoke(prompt)
            return {
                "answer": _response_text(response),
                "latency_seconds": round(time.perf_counter() - started, 3),
                "usage": _usage(response),
                "attempts": attempt,
                "error": None,
            }
        except Exception as exc:  # benchmark should preserve the other model's run
            if attempt < max_attempts and _retryable_model_error(exc):
                delay = retry_delay_seconds * (2 ** (attempt - 1))
                print(
                    f"Transient {type(exc).__name__}; retrying in {delay:g}s "
                    f"({attempt}/{max_attempts})",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            return {
                "answer": "",
                "latency_seconds": round(time.perf_counter() - started, 3),
                "usage": {},
                "attempts": attempt,
                "error": f"{type(exc).__name__}: {exc}",
            }

    raise AssertionError("Model invocation loop exited unexpectedly")


def _display_source(source: str, project_root: Path) -> str:
    try:
        return Path(source).resolve().relative_to(project_root.resolve()).as_posix()
    except (OSError, ValueError):
        return Path(source).name or "unknown"


def run_benchmark(
    *,
    questions: list[dict[str, str]],
    left_model: Any,
    right_model: Any,
    left_spec: dict[str, str],
    right_spec: dict[str, str],
    document_processor: Any,
    top_k: int = 5,
    project_root: Optional[Path] = None,
    request_delay_seconds: float = 0.0,
) -> dict[str, Any]:
    """Run a fair A/B test using one retrieval operation per question."""
    root = project_root or Path.cwd()
    prompt_template = PromptTemplate(
        input_variables=["context", "question"],
        template=(
            f"{RAGAdvisingChatbot.SYSTEM_PROMPT}\n\n"
            "{context}\n\n"
            "Student Question: {question}\n\n"
            "Assistant Response:"
        ),
    )
    results = []
    for index, item in enumerate(questions):
        documents = document_processor.retrieve_relevant_docs(item["question"], k=top_k)
        context = build_context(documents)
        prompt = prompt_template.format(
            context=context,
            question=item["question"],
        )

        # Alternate invocation order so one model is not always advantaged by
        # being called first (connection warming or transient load).
        if index % 2 == 0:
            left = invoke_model(left_model, prompt)
            right = invoke_model(right_model, prompt)
        else:
            right = invoke_model(right_model, prompt)
            left = invoke_model(left_model, prompt)

        sources = []
        for document in documents:
            metadata = getattr(document, "metadata", {}) or {}
            source = _display_source(str(metadata.get("source", "unknown")), root)
            if source not in sources:
                sources.append(source)
        results.append(
            {
                **item,
                "sources": sources,
                "context_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "left": left,
                "right": right,
            }
        )
        if request_delay_seconds and index < len(questions) - 1:
            time.sleep(request_delay_seconds)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": (
            "Shared retrieval context and identical prompt; invocation order "
            "alternates by question."
        ),
        "system_prompt_sha256": hashlib.sha256(
            RAGAdvisingChatbot.SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "embedding_provider": getattr(Config, "EMBEDDING_PROVIDER", "auto"),
        "top_k": top_k,
        "left_model": left_spec,
        "right_model": right_spec,
        "results": results,
    }


def _quote_markdown(value: str) -> str:
    value = value.strip() or "_(No answer returned.)_"
    return "\n".join(f"> {line}" if line else ">" for line in value.splitlines())


def _summary(result: dict[str, Any], side: str) -> dict[str, Any]:
    runs = [item[side] for item in result["results"]]
    successful = [run for run in runs if not run["error"]]
    latencies = [run["latency_seconds"] for run in successful]
    token_totals = [
        run["usage"].get("total_tokens")
        for run in successful
        if isinstance(run["usage"].get("total_tokens"), int)
    ]
    return {
        "successful": len(successful),
        "total": len(runs),
        "median_latency": round(statistics.median(latencies), 3) if latencies else None,
        "total_tokens": sum(token_totals) if token_totals else None,
    }


def render_markdown(result: dict[str, Any]) -> str:
    """Render a human-review report; factual quality remains human-scored."""
    left = result["left_model"]
    right = result["right_model"]
    left_summary = _summary(result, "left")
    right_summary = _summary(result, "right")

    lines = [
        "# Advising model comparison",
        "",
        f"Generated: {result['created_at']}",
        "",
        f"Method: {result['method']}",
        "",
        "## Run summary",
        "",
        "| Model | Successful | Median latency | Reported tokens |",
        "| --- | ---: | ---: | ---: |",
        (
            f"| {left['label']} | {left_summary['successful']}/{left_summary['total']} "
            f"| {left_summary['median_latency'] or 'n/a'} s "
            f"| {left_summary['total_tokens'] or 'n/a'} |"
        ),
        (
            f"| {right['label']} | "
            f"{right_summary['successful']}/{right_summary['total']} "
            f"| {right_summary['median_latency'] or 'n/a'} s "
            f"| {right_summary['total_tokens'] or 'n/a'} |"
        ),
        "",
        "Use the rubric after each question. Score each answer 1–7 for groundedness, "
        "completeness, clarity, and appropriate uncertainty; then select a "
        "preferred answer.",
        "",
    ]

    for number, item in enumerate(result["results"], 1):
        lines.extend(
            [
                f"## {number}. {item['id']}",
                "",
                f"**Question:** {item['question']}",
                "",
                "**Review focus:** "
                f"{item.get('review_focus') or 'General advising quality.'}",
                "",
                "**Retrieved sources:** "
                + (", ".join(f"`{source}`" for source in item["sources"]) or "None"),
                "",
                f"### {left['label']}",
                "",
                (
                    f"Error: `{item['left']['error']}`"
                    if item["left"]["error"]
                    else _quote_markdown(item["left"]["answer"])
                ),
                "",
                f"Latency: {item['left']['latency_seconds']} s; usage: "
                f"`{json.dumps(item['left']['usage'], sort_keys=True)}`",
                "",
                f"### {right['label']}",
                "",
                (
                    f"Error: `{item['right']['error']}`"
                    if item["right"]["error"]
                    else _quote_markdown(item["right"]["answer"])
                ),
                "",
                f"Latency: {item['right']['latency_seconds']} s; usage: "
                f"`{json.dumps(item['right']['usage'], sort_keys=True)}`",
                "",
                "| Review | Left | Right |",
                "| --- | ---: | ---: |",
                "| Grounded in retrieved evidence (1–7) |  |  |",
                "| Complete (1–7) |  |  |",
                "| Clear and actionable (1–7) |  |  |",
                "| Appropriate uncertainty (1–7) |  |  |",
                "| Preferred answer |  |  |",
                "",
                "Notes:",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _model_label(provider: str, model: Optional[str]) -> str:
    if provider == "azure_openai":
        name = model or Config.AZURE_OPENAI_DEPLOYMENT or "configured deployment"
        return f"Azure OpenAI — {name}"
    if provider == "openai":
        return f"OpenAI — {model or Config.OPENAI_MODEL}"
    return f"{provider} — {model or 'configured model'}"


def _configured_model(provider: str, override: Optional[str]) -> Optional[str]:
    """Resolve the effective model name shown in benchmark artifacts."""
    if override:
        return override
    if provider == "azure_openai":
        return Config.AZURE_OPENAI_DEPLOYMENT
    if provider == "openai":
        return Config.OPENAI_MODEL
    try:
        return Config.get_openai_compatible_config(provider).get("model")
    except ValueError:
        return None


def _provider_error(provider: str) -> str:
    if provider == "openai":
        return "Add OPENAI_API_KEY to .env (this is separate from the Azure key)."
    if provider == "azure_openai":
        return (
            "Set AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, and "
            "AZURE_OPENAI_DEPLOYMENT in .env."
        )
    return f"Complete the .env settings for provider '{provider}'."


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two advising models using identical RAG context."
    )
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--left-provider", default="azure_openai")
    parser.add_argument("--left-model", default=Config.AZURE_BENCHMARK_LEFT_DEPLOYMENT)
    parser.add_argument("--left-label", default=None)
    parser.add_argument("--right-provider", default="azure_openai")
    parser.add_argument(
        "--right-model", default=Config.AZURE_BENCHMARK_RIGHT_DEPLOYMENT
    )
    parser.add_argument("--right-label", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--request-delay",
        type=float,
        default=float(os.getenv("BENCHMARK_REQUEST_DELAY_SECONDS", "60")),
        help=(
            "Seconds to pause between question pairs to reduce provider throttling "
            "(default: 60 or BENCHMARK_REQUEST_DELAY_SECONDS)"
        ),
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--build-index", action="store_true")
    parser.add_argument(
        "--embedding-provider",
        choices=("hashing", "local", "auto", "azure_openai", "openai"),
        default="hashing",
        help="Embedding backend for retrieval (default: offline hashing)",
    )
    parser.add_argument(
        "--vector-store-path",
        type=Path,
        default=DEFAULT_VECTOR_STORE,
        help="Dedicated benchmark vector store (default: evals/vector_store)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    args.left_provider = Config.normalize_provider(args.left_provider)
    args.right_provider = Config.normalize_provider(args.right_provider)
    if not 1 <= args.top_k <= 20:
        raise SystemExit("--top-k must be between 1 and 20")
    if not 0 <= args.request_delay <= 60:
        raise SystemExit("--request-delay must be between 0 and 60 seconds")

    for provider in (args.left_provider, args.right_provider):
        if not Config.validate_provider(provider):
            raise SystemExit(
                f"Provider '{provider}' is not configured. {_provider_error(provider)}"
            )

    questions = load_questions(args.questions)
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be at least 1")
        questions = questions[: args.limit]

    # Keep benchmark retrieval separate from the app's production index. Local
    # embeddings also avoid consuming Azure/OpenAI embedding quota during A/B
    # tests; the answer-generation models are still the configured providers.
    Config.EMBEDDING_PROVIDER = args.embedding_provider
    Config.VECTOR_STORE_PATH = str(args.vector_store_path)
    processor = DocumentProcessor()
    if args.build_index:
        vector_store = processor.process_and_index()
    else:
        vector_store = processor.load_vector_store()
    if not vector_store:
        raise SystemExit(
            "No advising vector index is available. Re-run with --build-index "
            "or run: python -m src.build_index --docs ./data/advising_docs --clean"
        )

    # None omits temperature so each provider can use its supported default;
    # some reasoning models do not accept a custom sampling temperature.
    left_model = LLMFactory.create_llm(
        args.left_provider, model=args.left_model, temperature=None
    )
    right_model = LLMFactory.create_llm(
        args.right_provider, model=args.right_model, temperature=None
    )
    left_spec = {
        "provider": args.left_provider,
        "model": _configured_model(args.left_provider, args.left_model),
        "label": args.left_label or _model_label(args.left_provider, args.left_model),
    }
    right_spec = {
        "provider": args.right_provider,
        "model": _configured_model(args.right_provider, args.right_model),
        "label": args.right_label
        or _model_label(args.right_provider, args.right_model),
    }

    result = run_benchmark(
        questions=questions,
        left_model=left_model,
        right_model=right_model,
        left_spec=left_spec,
        right_spec=right_spec,
        document_processor=processor,
        top_k=args.top_k,
        request_delay_seconds=args.request_delay,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.report.write_text(render_markdown(result), encoding="utf-8")
    print(f"Wrote machine-readable results to {args.output}")
    print(f"Wrote review report to {args.report}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
