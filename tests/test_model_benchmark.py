import json

import pytest
from langchain_core.documents import Document

from src.model_benchmark import (
    invoke_model,
    load_questions,
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


def test_model_invocation_retries_transient_rate_limits(monkeypatch):
    model = FakeFlakyModel()
    delays = []
    monkeypatch.setattr("src.model_benchmark.time.sleep", delays.append)

    result = invoke_model(model, "prompt", retry_delay_seconds=2)

    assert result["answer"] == "recovered answer"
    assert result["attempts"] == 2
    assert result["error"] is None
    assert delays == [2]
