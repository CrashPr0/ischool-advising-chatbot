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
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_text_atomic(path: Path, value: str) -> None:
    """Replace a text artifact atomically so readers never see partial data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


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


def _exception_headers(exc: Exception) -> dict[str, str]:
    """Return normalized provider response headers, including wrapped errors."""
    current: Optional[BaseException] = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        headers = getattr(response, "headers", None)
        if headers:
            try:
                return {str(key).lower(): str(value) for key, value in headers.items()}
            except (AttributeError, TypeError, ValueError):
                pass
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )
    return {}


def _duration_seconds(value: str) -> Optional[float]:
    """Parse numeric seconds, HTTP dates, or provider durations such as 1m30s."""
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    try:
        return max(0.0, float(normalized))
    except ValueError:
        pass

    units = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)", normalized)
    if matches and "".join(f"{amount}{unit}" for amount, unit in matches) == re.sub(
        r"\s+", "", normalized
    ):
        return sum(float(amount) * units[unit] for amount, unit in matches)

    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _provider_retry_delay(
    exc: Exception,
    fallback_seconds: float,
    *,
    max_delay_seconds: float,
) -> float:
    """Prefer the provider's retry/reset headers over guessed backoff."""
    headers = _exception_headers(exc)
    candidates: list[float] = []
    retry_after_ms = headers.get("retry-after-ms", "").strip().lower()
    if retry_after_ms:
        try:
            candidates.append(max(0.0, float(retry_after_ms)) / 1000.0)
        except ValueError:
            parsed = _duration_seconds(retry_after_ms)
            if parsed is not None:
                candidates.append(parsed)
    for name in (
        "retry-after",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
    ):
        parsed = _duration_seconds(headers.get(name, ""))
        if parsed is not None:
            candidates.append(parsed)
    requested = max(candidates) + 1.0 if candidates else fallback_seconds
    return min(max(0.0, requested), max_delay_seconds)


def invoke_model(
    model: Any,
    prompt: str,
    *,
    max_attempts: int = 6,
    retry_delay_seconds: float = 30.0,
    max_retry_delay_seconds: float = 300.0,
) -> dict[str, Any]:
    """Invoke one model with bounded retries for transient provider failures."""
    started = time.perf_counter()
    retry_delays: list[float] = []
    for attempt in range(1, max_attempts + 1):
        try:
            response = model.invoke(prompt)
            return {
                "answer": _response_text(response),
                "latency_seconds": round(time.perf_counter() - started, 3),
                "usage": _usage(response),
                "attempts": attempt,
                "retry_delays_seconds": retry_delays,
                "error": None,
            }
        except Exception as exc:  # benchmark should preserve the other model's run
            if attempt < max_attempts and _retryable_model_error(exc):
                fallback = retry_delay_seconds * (2 ** (attempt - 1))
                delay = _provider_retry_delay(
                    exc,
                    fallback,
                    max_delay_seconds=max_retry_delay_seconds,
                )
                retry_delays.append(round(delay, 3))
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
                "retry_delays_seconds": retry_delays,
                "error": f"{type(exc).__name__}: {exc}",
            }

    raise AssertionError("Model invocation loop exited unexpectedly")


def _display_source(source: str, project_root: Path) -> str:
    try:
        return Path(source).resolve().relative_to(project_root.resolve()).as_posix()
    except (OSError, ValueError):
        return Path(source).name or "unknown"


def benchmark_fingerprint(
    *,
    questions: list[dict[str, str]],
    left_spec: dict[str, str],
    right_spec: dict[str, str],
    top_k: int,
    embedding_provider: str,
) -> str:
    """Identify every input that makes a checkpoint safe to resume."""
    payload = {
        "questions": questions,
        "left_model": {
            "provider": left_spec.get("provider"),
            "model": left_spec.get("model"),
        },
        "right_model": {
            "provider": right_spec.get("provider"),
            "model": right_spec.get("model"),
        },
        "top_k": top_k,
        "embedding_provider": embedding_provider,
        "system_prompt_sha256": hashlib.sha256(
            RAGAdvisingChatbot.SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _successful_response(response: Any) -> bool:
    return bool(
        isinstance(response, dict)
        and not response.get("error")
        and str(response.get("answer", "")).strip()
    )


def _update_run_progress(result: dict[str, Any], total_questions: int) -> None:
    results = result.get("results", [])
    successful_responses = sum(
        _successful_response(item.get(side))
        for item in results
        for side in ("left", "right")
    )
    completed_questions = sum(
        _successful_response(item.get("left"))
        and _successful_response(item.get("right"))
        for item in results
    )
    failed_responses = sum(
        isinstance(item.get(side), dict) and bool(item[side].get("error"))
        for item in results
        for side in ("left", "right")
    )
    result.update(
        {
            "updated_at": _utc_now(),
            "status": (
                "complete" if completed_questions == total_questions else "in_progress"
            ),
            "completed_questions": completed_questions,
            "total_questions": total_questions,
            "successful_responses": successful_responses,
            "total_responses": total_questions * 2,
            "failed_responses": failed_responses,
        }
    )


def _format_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _response_progress(response: dict[str, Any]) -> str:
    usage = response.get("usage") or {}
    tokens = usage.get("total_tokens")
    token_text = str(tokens) if isinstance(tokens, int) else "unreported"
    if response.get("error"):
        error_name = str(response["error"]).split(":", 1)[0]
        outcome = f"error={error_name}"
    else:
        outcome = "ok"
    return (
        f"{outcome}, latency={response.get('latency_seconds', 'n/a')}s, "
        f"tokens={token_text}, attempts={response.get('attempts', 'n/a')}"
    )


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
    existing_result: Optional[dict[str, Any]] = None,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    max_attempts: int = 6,
    retry_delay_seconds: float = 30.0,
    max_retry_delay_seconds: float = 300.0,
) -> dict[str, Any]:
    """Run or resume a fair A/B test using one retrieval operation per question."""
    root = project_root or Path.cwd()
    embedding_provider = getattr(Config, "EMBEDDING_PROVIDER", "auto")
    fingerprint = benchmark_fingerprint(
        questions=questions,
        left_spec=left_spec,
        right_spec=right_spec,
        top_k=top_k,
        embedding_provider=embedding_provider,
    )
    if existing_result and existing_result.get("run_fingerprint") != fingerprint:
        raise ValueError(
            "Checkpoint inputs do not match this benchmark. Start a fresh run."
        )

    prompt_template = PromptTemplate(
        input_variables=["context", "question"],
        template=(
            f"{RAGAdvisingChatbot.SYSTEM_PROMPT}\n\n"
            "{context}\n\n"
            "Student Question: {question}\n\n"
            "Assistant Response:"
        ),
    )
    result: dict[str, Any] = {
        "created_at": (
            existing_result.get("created_at") if existing_result else _utc_now()
        ),
        "method": (
            "Shared retrieval context and identical prompt; invocation order "
            "alternates by question. Successful responses are checkpointed and "
            "resumed without another model call."
        ),
        "run_fingerprint": fingerprint,
        "system_prompt_sha256": hashlib.sha256(
            RAGAdvisingChatbot.SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "embedding_provider": embedding_provider,
        "top_k": top_k,
        "left_model": left_spec,
        "right_model": right_spec,
        "results": [],
    }
    existing_by_id = {
        str(item.get("id")): item
        for item in (existing_result or {}).get("results", [])
        if isinstance(item, dict) and item.get("id")
    }
    results_by_id = dict(existing_by_id)
    question_ids = [item["id"] for item in questions]

    def checkpoint() -> None:
        result["results"] = [
            results_by_id[question_id]
            for question_id in question_ids
            if question_id in results_by_id
        ]
        _update_run_progress(result, len(questions))
        if progress_callback:
            progress_callback(result)

    checkpoint()
    run_started = time.perf_counter()
    worked_questions = 0
    for index, item in enumerate(questions):
        documents = document_processor.retrieve_relevant_docs(item["question"], k=top_k)
        context = build_context(documents)
        prompt = prompt_template.format(
            context=context,
            question=item["question"],
        )
        context_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        sources = []
        for document in documents:
            metadata = getattr(document, "metadata", {}) or {}
            source = _display_source(str(metadata.get("source", "unknown")), root)
            if source not in sources:
                sources.append(source)

        previous = results_by_id.get(item["id"], {})
        same_context = (
            previous.get("question") == item["question"]
            and previous.get("context_sha256") == context_sha256
        )
        current = {
            **item,
            "sources": sources,
            "context_sha256": context_sha256,
            "left": previous.get("left") if same_context else None,
            "right": previous.get("right") if same_context else None,
        }
        results_by_id[item["id"]] = current
        needed_sides = [
            side
            for side in ("left", "right")
            if not _successful_response(current.get(side))
        ]
        if not needed_sides:
            print(
                f"[{index + 1}/{len(questions)}] {item['id']}: resumed both responses"
            )
            checkpoint()
            continue

        worked_questions += 1
        ordered_sides = ("left", "right") if index % 2 == 0 else ("right", "left")
        models = {"left": left_model, "right": right_model}

        # Alternate invocation order so one model is not always advantaged by
        # being called first (connection warming or transient load).
        for side in ordered_sides:
            if side not in needed_sides:
                continue
            model_label = result[f"{side}_model"]["label"]
            print(
                f"[{index + 1}/{len(questions)}] {item['id']}: requesting "
                f"{model_label}"
            )
            current[side] = invoke_model(
                models[side],
                prompt,
                max_attempts=max_attempts,
                retry_delay_seconds=retry_delay_seconds,
                max_retry_delay_seconds=max_retry_delay_seconds,
            )
            checkpoint()
            print(
                f"[{index + 1}/{len(questions)}] {item['id']}: {model_label}: "
                f"{_response_progress(current[side])}"
            )

        elapsed = time.perf_counter() - run_started
        remaining = max(0, len(questions) - result["completed_questions"])
        average = elapsed / worked_questions if worked_questions else 0
        eta = average * remaining
        outcome = (
            "complete"
            if all(
                _successful_response(current.get(side)) for side in ("left", "right")
            )
            else "needs retry"
        )
        print(
            f"[{index + 1}/{len(questions)}] {item['id']}: {outcome}; "
            f"{result['successful_responses']}/{result['total_responses']} responses; "
            f"ETA {_format_duration(eta)}"
        )

        if request_delay_seconds and index < len(questions) - 1 and needed_sides:
            time.sleep(request_delay_seconds)

    checkpoint()
    return result


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


def _default_checkpoint_path(output_path: Path) -> Path:
    suffix = output_path.suffix or ".json"
    return output_path.with_name(f"{output_path.stem}.partial{suffix}")


def _load_checkpoint(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Checkpoint is unreadable: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError(f"Checkpoint is not a benchmark result: {path}")
    return payload


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
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Retrieved document chunks shared by both models (default: 3)",
    )
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
        "--max-attempts",
        type=int,
        default=6,
        help="Maximum attempts for each model response (default: 6)",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=30.0,
        help="Fallback initial retry delay when the provider omits one (default: 30)",
    )
    parser.add_argument(
        "--max-retry-delay",
        type=float,
        default=300.0,
        help="Maximum provider-directed or exponential retry delay (default: 300)",
    )
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
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="Partial-run checkpoint (default: <output stem>.partial.json)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Discard the matching partial checkpoint and start a new run",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    args.left_provider = Config.normalize_provider(args.left_provider)
    args.right_provider = Config.normalize_provider(args.right_provider)
    if not 1 <= args.top_k <= 20:
        raise SystemExit("--top-k must be between 1 and 20")
    if not 0 <= args.request_delay <= 3600:
        raise SystemExit("--request-delay must be between 0 and 3600 seconds")
    if not 1 <= args.max_attempts <= 20:
        raise SystemExit("--max-attempts must be between 1 and 20")
    if not 0 <= args.retry_delay <= 3600:
        raise SystemExit("--retry-delay must be between 0 and 3600 seconds")
    if not 0 <= args.max_retry_delay <= 3600:
        raise SystemExit("--max-retry-delay must be between 0 and 3600 seconds")

    for provider in (args.left_provider, args.right_provider):
        if not Config.validate_provider(provider):
            raise SystemExit(
                f"Provider '{provider}' is not configured. {_provider_error(provider)}"
            )

    questions = load_questions(args.questions)
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be at least 1")
        if args.output.resolve() == DEFAULT_JSON_OUTPUT.resolve():
            raise SystemExit(
                "--limit cannot publish to the default evaluation dataset. "
                "Provide --output and --report paths for the smoke run."
            )
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

    checkpoint_path = args.checkpoint or _default_checkpoint_path(args.output)
    if args.fresh:
        checkpoint_path.unlink(missing_ok=True)
    try:
        existing_result = _load_checkpoint(checkpoint_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    expected_fingerprint = benchmark_fingerprint(
        questions=questions,
        left_spec=left_spec,
        right_spec=right_spec,
        top_k=args.top_k,
        embedding_provider=Config.EMBEDDING_PROVIDER,
    )
    if existing_result:
        if existing_result.get("run_fingerprint") != expected_fingerprint:
            raise SystemExit(
                f"Checkpoint {checkpoint_path} belongs to different benchmark "
                "inputs. Use --fresh to start over or choose --checkpoint PATH."
            )
        print(
            f"Resuming {checkpoint_path}: "
            f"{existing_result.get('completed_questions', 0)}/{len(questions)} "
            "questions complete"
        )

    def save_checkpoint(progress: dict[str, Any]) -> None:
        _write_json_atomic(checkpoint_path, progress)

    try:
        result = run_benchmark(
            questions=questions,
            left_model=left_model,
            right_model=right_model,
            left_spec=left_spec,
            right_spec=right_spec,
            document_processor=processor,
            top_k=args.top_k,
            request_delay_seconds=args.request_delay,
            existing_result=existing_result,
            progress_callback=save_checkpoint,
            max_attempts=args.max_attempts,
            retry_delay_seconds=args.retry_delay,
            max_retry_delay_seconds=args.max_retry_delay,
        )
    except KeyboardInterrupt:
        print(
            f"Benchmark interrupted; resume data remains at {checkpoint_path}",
            file=sys.stderr,
        )
        return 130

    if result["status"] != "complete":
        _write_json_atomic(checkpoint_path, result)
        print(
            f"Benchmark is incomplete: {result['completed_questions']}/"
            f"{result['total_questions']} questions and "
            f"{result['successful_responses']}/{result['total_responses']} "
            f"responses succeeded. Resume with the same command. Checkpoint: "
            f"{checkpoint_path}",
            file=sys.stderr,
        )
        return 2

    _write_json_atomic(args.output, result)
    _write_text_atomic(args.report, render_markdown(result))
    checkpoint_path.unlink(missing_ok=True)
    print(f"Wrote machine-readable results to {args.output}")
    print(f"Wrote review report to {args.report}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
