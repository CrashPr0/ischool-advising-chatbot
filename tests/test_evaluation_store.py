import csv
import hashlib
import io
import json
import sqlite3

import pytest

from src.evaluation_store import (
    DIMENSION_KEYS,
    EvaluationDataError,
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
                "category": "degree requirements",
                "question": "Which course should I take next?",
                "review_focus": "Grounded, actionable advice.",
                "sources": ["handbook.md"],
                "left": {"answer": "Take course A.", "error": None},
                "right": {"answer": "Take course B.", "error": None},
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _scores(**overrides):
    payload = {
        "accuracy": 5,
        "tone": 4,
        "succinctness": 4,
        "edge_cases": 3,
        "comment": "This answer is clear and grounded.",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def store(tmp_path):
    results = tmp_path / "results.json"
    _write_results(results)
    return EvaluationStore(results, tmp_path / "reviews.sqlite3")


def test_workspace_stays_blind_until_both_candidates_are_reviewed(store):
    initial = store.workspace("Reviewer One")

    assert initial["progress"]["total_reviews"] == 2
    assert [item["key"] for item in initial["rubric"]["dimensions"]] == list(
        DIMENSION_KEYS
    )
    assert initial["questions"][0]["category"] == "degree requirements"
    assert initial["questions"][0]["reveal"] is None
    assert initial["questions"][0]["needs_rescore"] is False
    assert initial["questions"][0]["candidates"]["A"]["error"] is None
    assert initial["questions"][0]["candidates"]["B"]["error"] is None
    assert "Azure GPT-4o mini" not in json.dumps(initial)
    assert "Azure GPT-5.6 Luna" not in json.dumps(initial)

    store.save_review(
        reviewer="Reviewer One",
        question_id="q1",
        candidate="A",
        **_scores(),
    )
    halfway = store.workspace("Reviewer One")
    assert halfway["questions"][0]["reveal"] is None
    assert (
        halfway["questions"][0]["candidates"]["A"]["review"]["rubric"] == "dimensions"
    )
    assert halfway["questions"][0]["candidates"]["A"]["review"]["rating"] is None

    store.save_review(
        reviewer="Reviewer One",
        question_id="q1",
        candidate="B",
        **_scores(
            accuracy=3,
            tone=3,
            succinctness=2,
            edge_cases=2,
            comment="This answer needs a more concrete next step.",
        ),
    )
    complete = store.workspace("Reviewer One")

    assert complete["questions"][0]["complete"] is True
    assert complete["questions"][0]["needs_rescore"] is False
    labels = {model["label"] for model in complete["questions"][0]["reveal"].values()}
    assert labels == {"Azure GPT-4o mini", "Azure GPT-5.6 Luna"}


def test_review_validation_enforces_score_and_short_rationale(store):
    with pytest.raises(EvaluationValidationError, match="between 1 and 5"):
        store.save_review(
            reviewer="Reviewer One",
            question_id="q1",
            candidate="A",
            **_scores(accuracy=8),
        )

    with pytest.raises(EvaluationValidationError, match="integer from 1 to 5"):
        store.save_review(
            reviewer="Reviewer One",
            question_id="q1",
            candidate="A",
            **_scores(tone=4.5),
        )

    with pytest.raises(EvaluationValidationError, match="between 1 and 3 sentences"):
        store.save_review(
            reviewer="Reviewer One",
            question_id="q1",
            candidate="A",
            **_scores(
                comment="One sentence. Two sentences. Three sentences. Four sentences."
            ),
        )

    assert count_sentences("清晰、准确。下一步也很具体！") == 2
    with store._connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM evaluation_reviews"
        ).fetchone()["count"]
    assert count == 0


def test_summary_and_csv_reveal_models_after_reviewer_completes_run(store):
    saved = {
        "A": _scores(accuracy=5, tone=4, succinctness=4, edge_cases=3),
        "B": _scores(
            accuracy=3,
            tone=3,
            succinctness=2,
            edge_cases=2,
            comment="The response gives a useful and defensible recommendation.",
        ),
    }
    for candidate, payload in saved.items():
        store.save_review(
            reviewer="Reviewer One",
            question_id="q1",
            candidate=candidate,
            **payload,
        )

    summary = store.summary("Reviewer One")
    assert summary["available"] is True
    assert summary["scale"] == {
        "min": 1,
        "max": 5,
        "legacy_min": 1,
        "legacy_max": 7,
    }
    assert summary["total_reviewers"] == 1
    assert sum(model["review_count"] for model in summary["models"].values()) == 2
    assert (
        sum(model["dimension_review_count"] for model in summary["models"].values())
        == 2
    )
    assert (
        sum(model["legacy_review_count"] for model in summary["models"].values()) == 0
    )
    accuracy = sorted(
        model["dimensions"]["accuracy"]["average"]
        for model in summary["models"].values()
    )
    assert accuracy == [3, 5]
    for model in summary["models"].values():
        for key in DIMENSION_KEYS:
            assert sum(model["dimensions"][key]["distribution"].values()) == (
                model["dimension_review_count"]
            )

    rows = list(csv.DictReader(io.StringIO(store.export_csv())))
    assert len(rows) == 2
    assert {row["model_label"] for row in rows} == {
        "Azure GPT-4o mini",
        "Azure GPT-5.6 Luna",
    }
    assert {row["reviewer"] for row in rows} == {"Reviewer One"}
    assert {row["rubric"] for row in rows} == {"dimensions"}
    assert {row["rating"] for row in rows} == {""}
    assert {row["accuracy"] for row in rows} == {"5", "3"}


def _seed_legacy_database(results_path, database_path):
    run_id = hashlib.sha256(results_path.read_bytes()).hexdigest()[:20]
    connection = sqlite3.connect(database_path)
    connection.execute("""
        CREATE TABLE evaluation_reviews (
            run_id TEXT NOT NULL,
            question_id TEXT NOT NULL,
            candidate TEXT NOT NULL CHECK(candidate IN ('A', 'B')),
            model_side TEXT NOT NULL CHECK(model_side IN ('left', 'right')),
            reviewer TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 7),
            comment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, question_id, candidate, reviewer)
        )
        """)
    created_at = "2026-09-20T15:00:00+00:00"
    for candidate, rating in (("A", 6), ("B", 4)):
        model_side = EvaluationStore._candidate_mapping(run_id, "q1", "Reviewer One")[
            candidate
        ]
        connection.execute(
            """
            INSERT INTO evaluation_reviews (
                run_id, question_id, candidate, model_side, reviewer,
                rating, comment, created_at, updated_at
            ) VALUES (?, 'q1', ?, ?, 'Reviewer One', ?, ?, ?, ?)
            """,
            (
                run_id,
                candidate,
                model_side,
                rating,
                "This legacy note explains the previous overall score.",
                created_at,
                created_at,
            ),
        )
    connection.commit()
    connection.close()
    return created_at


def test_unrecognized_schema_is_left_untouched(tmp_path):
    results = tmp_path / "results.json"
    database = tmp_path / "reviews.sqlite3"
    _write_results(results)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE evaluation_reviews (id INTEGER)")
    connection.execute("INSERT INTO evaluation_reviews (id) VALUES (1)")
    connection.commit()
    connection.close()

    with pytest.raises(EvaluationDataError, match="left untouched"):
        EvaluationStore(results, database)

    with sqlite3.connect(database) as connection:
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(evaluation_reviews)")
        ]
        preserved = connection.execute("SELECT id FROM evaluation_reviews").fetchone()
    assert columns == ["id"]
    assert preserved[0] == 1


def test_legacy_single_score_reviews_survive_migration_and_can_be_rescored(tmp_path):
    results = tmp_path / "results.json"
    database = tmp_path / "reviews.sqlite3"
    _write_results(results)
    created_at = _seed_legacy_database(results, database)

    store = EvaluationStore(results, database)
    EvaluationStore(results, database)

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(evaluation_reviews)")
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        preserved = connection.execute("""
            SELECT candidate, rating, accuracy, comment, created_at
            FROM evaluation_reviews
            ORDER BY candidate
            """).fetchall()
        legacy_tables = connection.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'evaluation_reviews_legacy'
            """).fetchall()

    assert version == 2
    assert set(DIMENSION_KEYS).issubset(columns)
    assert legacy_tables == []
    assert preserved == [
        (
            "A",
            6,
            None,
            "This legacy note explains the previous overall score.",
            created_at,
        ),
        (
            "B",
            4,
            None,
            "This legacy note explains the previous overall score.",
            created_at,
        ),
    ]

    workspace = store.workspace("Reviewer One")
    question = workspace["questions"][0]
    assert question["complete"] is True
    assert question["needs_rescore"] is True
    assert question["candidates"]["A"]["review"]["rubric"] == "legacy"
    assert question["candidates"]["A"]["review"]["rating"] == 6
    assert question["candidates"]["A"]["review"]["accuracy"] is None

    summary = store.summary("Reviewer One")
    assert summary["available"] is True
    assert (
        sum(model["legacy_review_count"] for model in summary["models"].values()) == 2
    )
    assert (
        sum(model["dimension_review_count"] for model in summary["models"].values())
        == 0
    )
    assert sorted(
        model["legacy"]["average_rating"] for model in summary["models"].values()
    ) == [4, 6]
    for model in summary["models"].values():
        for key in DIMENSION_KEYS:
            assert model["dimensions"][key]["average"] is None

    legacy_rows = list(csv.DictReader(io.StringIO(store.export_csv())))
    assert {row["rubric"] for row in legacy_rows} == {"legacy"}
    assert {row["rating"] for row in legacy_rows} == {"6", "4"}
    assert {row["accuracy"] for row in legacy_rows} == {""}

    store.save_review(
        reviewer="Reviewer One",
        question_id="q1",
        candidate="A",
        **_scores(comment="Rescored for accuracy, tone, length, and care."),
    )
    rescored = store.workspace("Reviewer One")["questions"][0]
    candidate_a = rescored["candidates"]["A"]["review"]
    assert rescored["needs_rescore"] is True
    assert candidate_a["rubric"] == "dimensions"
    assert candidate_a["rating"] is None
    assert candidate_a["accuracy"] == 5
    assert candidate_a["created_at"] == created_at
    assert rescored["candidates"]["B"]["review"]["rubric"] == "legacy"

    updated = store.summary("Reviewer One")
    assert sum(model["review_count"] for model in updated["models"].values()) == 2
    assert (
        sum(model["dimension_review_count"] for model in updated["models"].values())
        == 1
    )
    assert (
        sum(model["legacy_review_count"] for model in updated["models"].values()) == 1
    )
