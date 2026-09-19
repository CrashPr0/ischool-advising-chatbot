import csv
import io
import json

import pytest

from src.evaluation_store import (
    EvaluationStore,
    EvaluationValidationError,
    count_sentences,
)


def _write_results(path):
    payload = {
        "created_at": "2026-09-19T12:00:00+00:00",
        "method": "Same prompt and retrieved context.",
        "left_model": {
            "provider": "azure_openai",
            "model": "gpt-4o-mini",
            "label": "Azure GPT-4o mini",
        },
        "right_model": {
            "provider": "azure_openai",
            "model": "gpt-5.6-luna",
            "label": "Azure GPT-5.6 Luna",
        },
        "results": [
            {
                "id": "q1",
                "question": "Which course should I take next?",
                "review_focus": "Grounded, actionable advice.",
                "sources": ["handbook.md"],
                "left": {"answer": "Take course A.", "error": None},
                "right": {"answer": "Take course B.", "error": None},
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    results = tmp_path / "results.json"
    _write_results(results)
    return EvaluationStore(results, tmp_path / "reviews.sqlite3")


def test_workspace_stays_blind_until_both_candidates_are_reviewed(store):
    initial = store.workspace("Reviewer One")

    assert initial["progress"]["total_reviews"] == 2
    assert initial["questions"][0]["reveal"] is None
    assert initial["questions"][0]["candidates"]["A"]["error"] is None
    assert initial["questions"][0]["candidates"]["B"]["error"] is None
    assert "Azure GPT-4o mini" not in json.dumps(initial)
    assert "Azure GPT-5.6 Luna" not in json.dumps(initial)

    store.save_review(
        reviewer="Reviewer One",
        question_id="q1",
        candidate="A",
        rating=6,
        comment="This answer is clear and grounded.",
    )
    halfway = store.workspace("Reviewer One")
    assert halfway["questions"][0]["reveal"] is None

    store.save_review(
        reviewer="Reviewer One",
        question_id="q1",
        candidate="B",
        rating=4,
        comment="This answer needs a more concrete next step.",
    )
    complete = store.workspace("Reviewer One")

    assert complete["questions"][0]["complete"] is True
    labels = {model["label"] for model in complete["questions"][0]["reveal"].values()}
    assert labels == {"Azure GPT-4o mini", "Azure GPT-5.6 Luna"}


def test_review_validation_enforces_score_and_short_rationale(store):
    with pytest.raises(EvaluationValidationError, match="between 1 and 7"):
        store.save_review(
            reviewer="Reviewer One",
            question_id="q1",
            candidate="A",
            rating=8,
            comment="This is otherwise valid feedback.",
        )

    with pytest.raises(EvaluationValidationError, match="between 1 and 3 sentences"):
        store.save_review(
            reviewer="Reviewer One",
            question_id="q1",
            candidate="A",
            rating=5,
            comment="One sentence. Two sentences. Three sentences. Four sentences.",
        )

    assert count_sentences("清晰、准确。下一步也很具体！") == 2


def test_summary_and_csv_reveal_models_after_reviewer_completes_run(store):
    for candidate, rating in (("A", 7), ("B", 5)):
        store.save_review(
            reviewer="Reviewer One",
            question_id="q1",
            candidate=candidate,
            rating=rating,
            comment="The response gives a useful and defensible recommendation.",
        )

    summary = store.summary("Reviewer One")
    assert summary["available"] is True
    assert summary["total_reviewers"] == 1
    assert sum(model["review_count"] for model in summary["models"].values()) == 2

    rows = list(csv.DictReader(io.StringIO(store.export_csv())))
    assert len(rows) == 2
    assert {row["model_label"] for row in rows} == {
        "Azure GPT-4o mini",
        "Azure GPT-5.6 Luna",
    }
    assert {row["reviewer"] for row in rows} == {"Reviewer One"}
