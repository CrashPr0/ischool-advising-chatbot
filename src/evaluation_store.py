"""Persistent human-review storage for model comparison runs.

The benchmark JSON remains the immutable source of questions and responses.
Reviewer scores are stored separately in SQLite so a new benchmark run cannot
silently inherit judgments from an older set of model outputs.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EvaluationDataError(ValueError):
    """Raised when the benchmark artifact cannot be loaded or validated."""


class EvaluationValidationError(ValueError):
    """Raised when a submitted review does not satisfy the review contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def count_sentences(value: str) -> int:
    """Count reviewer sentences across common Latin and CJK punctuation."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return 0
    segments = re.findall(r"[^.!?。！？]+(?:[.!?。！？]+|$)", text)
    return len([segment for segment in segments if segment.strip(" .!?。！？")])


def validate_review(rating: Any, comment: Any) -> tuple[int, str]:
    """Validate and normalize the business review contract."""
    if isinstance(rating, bool):
        raise EvaluationValidationError("Rating must be an integer from 1 to 7")
    try:
        normalized_rating = int(rating)
    except (TypeError, ValueError) as exc:
        raise EvaluationValidationError(
            "Rating must be an integer from 1 to 7"
        ) from exc
    if normalized_rating < 1 or normalized_rating > 7:
        raise EvaluationValidationError("Rating must be between 1 and 7")

    normalized_comment = re.sub(r"\s+", " ", str(comment or "")).strip()
    if len(normalized_comment) < 10:
        raise EvaluationValidationError("Feedback must contain at least 10 characters")
    if len(normalized_comment) > 1_000:
        raise EvaluationValidationError(
            "Feedback must contain no more than 1,000 characters"
        )
    sentence_count = count_sentences(normalized_comment)
    if sentence_count < 1 or sentence_count > 3:
        raise EvaluationValidationError(
            "Feedback must contain between 1 and 3 sentences"
        )
    return normalized_rating, normalized_comment


class EvaluationStore:
    """Read benchmark artifacts and persist reviewer judgments."""

    def __init__(self, results_path: str | Path, database_path: str | Path):
        self.results_path = Path(results_path)
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_reviews (
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
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_evaluation_reviews_run
                ON evaluation_reviews(run_id, question_id, model_side)
                """)

    def _load_run(self) -> tuple[str, dict[str, Any]]:
        try:
            raw = self.results_path.read_bytes()
        except FileNotFoundError as exc:
            raise EvaluationDataError(
                f"Benchmark results were not found at {self.results_path}"
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvaluationDataError("Benchmark results are not valid JSON") from exc

        if not isinstance(payload, dict):
            raise EvaluationDataError("Benchmark results must be a JSON object")
        if not isinstance(payload.get("results"), list) or not payload["results"]:
            raise EvaluationDataError("Benchmark results contain no questions")
        for side in ("left_model", "right_model"):
            model = payload.get(side)
            if not isinstance(model, dict) or not str(model.get("label", "")).strip():
                raise EvaluationDataError(f"Benchmark results are missing {side}")

        seen_ids: set[str] = set()
        for item in payload["results"]:
            if not isinstance(item, dict):
                raise EvaluationDataError("Each benchmark result must be an object")
            question_id = str(item.get("id", "")).strip()
            question = str(item.get("question", "")).strip()
            if not question_id or not question or question_id in seen_ids:
                raise EvaluationDataError(
                    "Benchmark question IDs must be unique and non-empty"
                )
            seen_ids.add(question_id)
            for side in ("left", "right"):
                response = item.get(side)
                if not isinstance(response, dict):
                    raise EvaluationDataError(
                        f"Question {question_id} is missing the {side} response"
                    )

        return hashlib.sha256(raw).hexdigest()[:20], payload

    @staticmethod
    def _candidate_mapping(
        run_id: str, question_id: str, reviewer: str
    ) -> dict[str, str]:
        """Return a deterministic per-reviewer, per-question blind assignment."""
        digest = hashlib.sha256(
            f"{run_id}:{question_id}:{reviewer}".encode("utf-8")
        ).digest()
        if digest[0] % 2:
            return {"A": "right", "B": "left"}
        return {"A": "left", "B": "right"}

    @staticmethod
    def _response_payload(response: dict[str, Any]) -> dict[str, Any]:
        error = response.get("error")
        return {
            "answer": str(response.get("answer", "")),
            "error": str(error) if error else None,
        }

    def _reviews_for(self, run_id: str, reviewer: str) -> dict[tuple[str, str], dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT question_id, candidate, rating, comment, created_at, updated_at
                FROM evaluation_reviews
                WHERE run_id = ? AND reviewer = ?
                """,
                (run_id, reviewer),
            ).fetchall()
        return {
            (row["question_id"], row["candidate"]): {
                "rating": row["rating"],
                "comment": row["comment"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def workspace(self, reviewer: str) -> dict[str, Any]:
        """Return a blind review workspace for one reviewer."""
        run_id, payload = self._load_run()
        reviews = self._reviews_for(run_id, reviewer)
        questions = []

        for item in payload["results"]:
            question_id = str(item["id"])
            mapping = self._candidate_mapping(run_id, question_id, reviewer)
            candidates = {}
            complete = True
            for candidate, side in mapping.items():
                review = reviews.get((question_id, candidate))
                if review is None:
                    complete = False
                candidates[candidate] = {
                    **self._response_payload(item[side]),
                    "review": review,
                }

            reveal = None
            if complete:
                reveal = {
                    candidate: {
                        "label": payload[f"{side}_model"]["label"],
                        "provider": payload[f"{side}_model"].get("provider"),
                        "model": payload[f"{side}_model"].get("model"),
                    }
                    for candidate, side in mapping.items()
                }

            questions.append(
                {
                    "id": question_id,
                    "question": str(item["question"]),
                    "review_focus": str(item.get("review_focus", "")),
                    "sources": [str(source) for source in item.get("sources", [])],
                    "candidates": candidates,
                    "complete": complete,
                    "reveal": reveal,
                }
            )

        completed = sum(1 for question in questions if question["complete"])
        return {
            "run_id": run_id,
            "created_at": payload.get("created_at"),
            "method": payload.get("method"),
            "reviewer": reviewer,
            "progress": {
                "completed_questions": completed,
                "total_questions": len(questions),
                "submitted_reviews": len(reviews),
                "total_reviews": len(questions) * 2,
            },
            "questions": questions,
        }

    def save_review(
        self,
        *,
        reviewer: str,
        question_id: str,
        candidate: str,
        rating: Any,
        comment: Any,
    ) -> dict[str, Any]:
        """Insert or update one reviewer judgment."""
        normalized_rating, normalized_comment = validate_review(rating, comment)
        normalized_candidate = str(candidate).strip().upper()
        if normalized_candidate not in {"A", "B"}:
            raise EvaluationValidationError("Candidate must be A or B")

        run_id, payload = self._load_run()
        question_ids = {str(item["id"]) for item in payload["results"]}
        if question_id not in question_ids:
            raise EvaluationValidationError("Unknown benchmark question")
        model_side = self._candidate_mapping(run_id, question_id, reviewer)[
            normalized_candidate
        ]
        now = _utc_now()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_reviews (
                    run_id, question_id, candidate, model_side, reviewer,
                    rating, comment, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, question_id, candidate, reviewer)
                DO UPDATE SET
                    model_side = excluded.model_side,
                    rating = excluded.rating,
                    comment = excluded.comment,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    question_id,
                    normalized_candidate,
                    model_side,
                    reviewer,
                    normalized_rating,
                    normalized_comment,
                    now,
                    now,
                ),
            )

        return {
            "question_id": question_id,
            "candidate": normalized_candidate,
            "rating": normalized_rating,
            "comment": normalized_comment,
            "updated_at": now,
        }

    def summary(self, reviewer: str) -> dict[str, Any]:
        """Return aggregate model results after the reviewer finishes the run."""
        workspace = self.workspace(reviewer)
        progress = workspace["progress"]
        if progress["submitted_reviews"] < progress["total_reviews"]:
            return {
                "available": False,
                "message": "Complete all reviews before revealing aggregate results.",
                "progress": progress,
            }

        run_id, payload = self._load_run()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT model_side, rating
                FROM evaluation_reviews
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()

        models = {}
        for side in ("left", "right"):
            ratings = [row["rating"] for row in rows if row["model_side"] == side]
            distribution = {str(score): ratings.count(score) for score in range(1, 8)}
            models[side] = {
                **payload[f"{side}_model"],
                "review_count": len(ratings),
                "average_rating": (
                    round(sum(ratings) / len(ratings), 2) if ratings else None
                ),
                "distribution": distribution,
            }

        return {
            "available": True,
            "run_id": run_id,
            "models": models,
            "total_reviewers": self.reviewer_count(run_id),
        }

    def reviewer_count(self, run_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(DISTINCT reviewer) AS count
                FROM evaluation_reviews
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def export_csv(self) -> str:
        """Export all reviews for the current run with revealed model labels."""
        run_id, payload = self._load_run()
        questions = {str(item["id"]): item for item in payload["results"]}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT question_id, candidate, model_side, reviewer, rating,
                       comment, created_at, updated_at
                FROM evaluation_reviews
                WHERE run_id = ?
                ORDER BY question_id, reviewer, candidate
                """,
                (run_id,),
            ).fetchall()

        output = io.StringIO(newline="")
        fieldnames = [
            "run_id",
            "question_id",
            "question",
            "candidate",
            "model_label",
            "provider",
            "model",
            "reviewer",
            "rating",
            "comment",
            "created_at",
            "updated_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            question = questions.get(row["question_id"], {})
            model = payload[f"{row['model_side']}_model"]
            writer.writerow(
                {
                    "run_id": run_id,
                    "question_id": row["question_id"],
                    "question": question.get("question", ""),
                    "candidate": row["candidate"],
                    "model_label": model.get("label", ""),
                    "provider": model.get("provider", ""),
                    "model": model.get("model", ""),
                    "reviewer": row["reviewer"],
                    "rating": row["rating"],
                    "comment": row["comment"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return output.getvalue()
