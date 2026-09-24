import json
import re
from pathlib import Path

import pytest
from langchain_core.documents import Document

from src.model_benchmark import (
    invoke_model,
    load_questions,
    main,
    parse_args,
    render_markdown,
    run_benchmark,
)


class FakeResponse:
    def __init__(self, content, input_tokens=10, output_tokens=5):
        self.content = content
        self.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


class FakeModel:
    def __init__(self, name):
        self.name = name
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return FakeResponse(f"{self.name} answer")


class FakeProcessor:
    def __init__(self):
        self.calls = []

    def retrieve_relevant_docs(self, question, k):
        self.calls.append((question, k))
        return [
            Document(
                page_content="Use the waitlist when a class is full.",
                metadata={"source": "/project/data/advising_docs/registration.txt"},
            )
        ]


class FakeRateLimitError(Exception):
    status_code = 429


class FakeFlakyModel:
    def __init__(self):
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        if self.calls == 1:
            raise FakeRateLimitError("rate limit exceeded")
        return FakeResponse("recovered answer")


class FakeFailureModel:
    def __init__(self, message="permanent failure"):
        self.message = message
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        raise ValueError(self.message)


class FakeHeadersResponse:
    headers = {"retry-after": "7"}


class FakeRetryAfterError(Exception):
    status_code = 429
    response = FakeHeadersResponse()


class FakeProviderDirectedModel:
    def __init__(self):
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        if self.calls == 1:
            raise FakeRetryAfterError("slow down")
        return FakeResponse("provider-directed recovery")


def test_benchmark_uses_one_shared_prompt_per_question(tmp_path):
    left = FakeModel("left")
    right = FakeModel("right")
    processor = FakeProcessor()
    questions = [
        {
            "id": "full-class",
            "question": "What if my class is full?",
            "review_focus": "Waitlist guidance",
        }
    ]

    result = run_benchmark(
        questions=questions,
        left_model=left,
        right_model=right,
        left_spec={
            "provider": "azure_openai",
            "model": "gpt-4o-mini",
            "label": "Azure",
        },
        right_spec={"provider": "openai", "model": "gpt-5.6-luna", "label": "Luna"},
        document_processor=processor,
        top_k=4,
        project_root=tmp_path,
    )

    assert processor.calls == [("What if my class is full?", 4)]
    assert left.prompts == right.prompts
    assert "Use the waitlist" in left.prompts[0]
    assert result["results"][0]["left"]["usage"]["total_tokens"] == 15
    assert result["results"][0]["right"]["answer"] == "right answer"
    assert result["status"] == "complete"
    assert result["completed_questions"] == 1

    report = render_markdown(result)
    assert "Azure" in report
    assert "Luna" in report
    assert "Appropriate uncertainty" in report


def test_load_questions_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            [
                {"id": "same", "question": "First"},
                {"id": "same", "question": "Second"},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate question id"):
        load_questions(path)


def test_benchmark_defaults_compare_both_azure_deployments():
    args = parse_args([])

    assert args.left_provider == "azure_openai"
    assert args.left_model == "gpt-4o-mini"
    assert args.right_provider == "azure_openai"
    assert args.right_model == "gpt-5.6-luna"
    assert args.embedding_provider == "hashing"
    assert args.top_k == 3
    assert args.request_delay == 60
    assert args.max_attempts == 6


def test_benchmark_question_set_matches_chat_presets():
    project_root = Path(__file__).resolve().parents[1]
    page = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    preset_questions = []
    for payload in re.findall(r"data-samples='([^']+)'", page):
        preset_questions.extend(json.loads(payload))

    benchmark = load_questions(project_root / "evals" / "advising_questions.json")

    assert len(preset_questions) == 24
    assert [item["question"] for item in benchmark] == preset_questions


def test_model_invocation_retries_transient_rate_limits(monkeypatch):
    model = FakeFlakyModel()
    delays = []
    monkeypatch.setattr("src.model_benchmark.time.sleep", delays.append)

    result = invoke_model(model, "prompt", retry_delay_seconds=2)

    assert result["answer"] == "recovered answer"
    assert result["attempts"] == 2
    assert result["error"] is None
    assert delays == [2]


def test_model_invocation_honors_provider_retry_after(monkeypatch):
    model = FakeProviderDirectedModel()
    delays = []
    monkeypatch.setattr("src.model_benchmark.time.sleep", delays.append)

    result = invoke_model(model, "prompt", retry_delay_seconds=30)

    assert result["answer"] == "provider-directed recovery"
    assert result["retry_delays_seconds"] == [8.0]
    assert delays == [8.0]


def test_resume_reuses_successful_side_and_retries_only_failure(tmp_path):
    question = {
        "id": "full-class",
        "question": "What if my class is full?",
        "review_focus": "Waitlist guidance",
    }
    left_first = FakeModel("left")
    right_failure = FakeFailureModel()
    processor = FakeProcessor()
    specs = {
        "left_spec": {
            "provider": "azure_openai",
            "model": "gpt-4o-mini",
            "label": "GPT-4o mini",
        },
        "right_spec": {
            "provider": "azure_openai",
            "model": "gpt-5.6-luna",
            "label": "GPT-5.6 Luna",
        },
    }
    checkpoints = []

    partial = run_benchmark(
        questions=[question],
        left_model=left_first,
        right_model=right_failure,
        document_processor=processor,
        project_root=tmp_path,
        progress_callback=lambda result: checkpoints.append(
            json.loads(json.dumps(result))
        ),
        **specs,
    )

    assert partial["status"] == "in_progress"
    assert partial["successful_responses"] == 1
    assert partial["failed_responses"] == 1
    assert checkpoints[-1]["results"][0]["left"]["answer"] == "left answer"

    left_resume = FakeModel("left-should-not-run")
    right_resume = FakeModel("right-recovered")
    resumed = run_benchmark(
        questions=[question],
        left_model=left_resume,
        right_model=right_resume,
        document_processor=processor,
        project_root=tmp_path,
        existing_result=partial,
        **specs,
    )

    assert left_resume.prompts == []
    assert len(right_resume.prompts) == 1
    assert resumed["status"] == "complete"
    assert resumed["results"][0]["left"]["answer"] == "left answer"
    assert resumed["results"][0]["right"]["answer"] == "right-recovered answer"


def test_incomplete_run_keeps_published_output_unchanged(tmp_path, monkeypatch):
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps([{"id": "q1", "question": "Question one?"}]),
        encoding="utf-8",
    )
    output_path = tmp_path / "latest.json"
    output_path.write_text('{"published": "old"}\n', encoding="utf-8")
    checkpoint_path = tmp_path / "latest.partial.json"
    processor = FakeProcessor()
    processor.load_vector_store = lambda: object()

    monkeypatch.setattr(
        "src.model_benchmark.Config.validate_provider",
        classmethod(lambda _cls, _provider: True),
    )
    monkeypatch.setattr("src.model_benchmark.DocumentProcessor", lambda: processor)

    def create_model(_provider, *, model, temperature):
        assert temperature is None
        return FakeModel("left") if model == "left-model" else FakeFailureModel()

    monkeypatch.setattr("src.model_benchmark.LLMFactory.create_llm", create_model)

    command = [
        "--questions",
        str(questions_path),
        "--left-model",
        "left-model",
        "--right-model",
        "right-model",
        "--request-delay",
        "0",
        "--max-attempts",
        "1",
        "--output",
        str(output_path),
        "--report",
        str(tmp_path / "report.md"),
        "--checkpoint",
        str(checkpoint_path),
        "--vector-store-path",
        str(tmp_path / "vector"),
    ]
    exit_code = main(command)

    assert exit_code == 2
    assert output_path.read_text(encoding="utf-8") == '{"published": "old"}\n'
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["status"] == "in_progress"
    assert checkpoint["successful_responses"] == 1

    left_resume = FakeModel("left-should-not-run")
    right_resume = FakeModel("right-recovered")

    def create_resume_model(_provider, *, model, temperature):
        assert temperature is None
        return left_resume if model == "left-model" else right_resume

    monkeypatch.setattr(
        "src.model_benchmark.LLMFactory.create_llm", create_resume_model
    )

    assert main(command) == 0
    published = json.loads(output_path.read_text(encoding="utf-8"))
    assert published["status"] == "complete"
    assert published["successful_responses"] == 2
    assert left_resume.prompts == []
    assert len(right_resume.prompts) == 1
    assert not checkpoint_path.exists()
    assert (tmp_path / "report.md").exists()
