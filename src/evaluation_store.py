"""Persistent human-review storage for model comparison runs.

The benchmark JSON remains the immutable source of questions and responses.
Reviewer scores are stored separately in SQLite so a new benchmark run cannot
silently inherit judgments from an older set of model outputs.

Reviews use four 1–5 dimensions (information accuracy, tone, appropriate
length, and edge-case/distress handling) plus one short rationale. Rows saved
under the previous single 1–7 score are kept and reported separately.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .pii_scrubber import scrub_pii
except ImportError:  # pragma: no cover - app.py loads src as a top-level path
    from pii_scrubber import scrub_pii

logger = logging.getLogger(__name__)

DIMENSION_MIN = 1
DIMENSION_MAX = 5
LEGACY_RATING_MIN = 1
LEGACY_RATING_MAX = 7

DIMENSION_SPECS: tuple[dict[str, str], ...] = (
    {
        "key": "accuracy",
        "label": "Information accuracy",
    },
    {
        "key": "tone",
        "label": "Tone (warmth and support)",
    },
    {
        "key": "succinctness",
        "label": "Appropriate length",
    },
    {
        "key": "edge_cases",
        "label": "Edge cases and distress",
    },
)
DIMENSION_KEYS: tuple[str, ...] = tuple(spec["key"] for spec in DIMENSION_SPECS)

_LEGACY_COLUMNS = {
    "run_id",
    "question_id",
    "candidate",
    "model_side",
    "reviewer",
    "rating",
    "comment",
    "created_at",
    "updated_at",
}

_CREATE_REVIEWS_SQL = """
CREATE TABLE evaluation_reviews (
    run_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    candidate TEXT NOT NULL CHECK(candidate IN ('A', 'B')),
    model_side TEXT NOT NULL CHECK(model_side IN ('left', 'right')),
    reviewer TEXT NOT NULL,
    rating INTEGER,
    accuracy INTEGER,
    tone INTEGER,
    succinctness INTEGER,
    edge_cases INTEGER,
    comment TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, question_id, candidate, reviewer),
    CHECK (
        (
            rating BETWEEN 1 AND 7
            AND accuracy IS NULL
            AND tone IS NULL
            AND succinctness IS NULL
            AND edge_cases IS NULL
        )
        OR
        (
            rating IS NULL
            AND accuracy BETWEEN 1 AND 5
            AND tone BETWEEN 1 AND 5
            AND succinctness BETWEEN 1 AND 5
            AND edge_cases BETWEEN 1 AND 5
        )
    )
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_evaluation_reviews_run
ON evaluation_reviews(run_id, question_id, model_side)
"""


class EvaluationDataError(ValueError):
    """Raised when the benchmark artifact cannot be loaded or validated."""


class EvaluationValidationError(ValueError):
    """Raised when a submitted review does not satisfy the review contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dimension_definitions() -> list[dict[str, str]]:
    """Return the public rubric labels in display order."""
    return [{"key": spec["key"], "label": spec["label"]} for spec in DIMENSION_SPECS]


def count_sentences(value: str) -> int:
    """Count reviewer sentences across common Latin and CJK punctuation."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return 0
    segments = re.findall(r"[^.!?。！？]+(?:[.!?。！？]+|$)", text)
    return len([segment for segment in segments if segment.strip(" .!?。！？")])


def _normalize_dimension(value: Any, label: str) -> int:
    """Validate one 1–5 rubric score."""
    if isinstance(value, bool):
        raise EvaluationValidationError(f"{label} must be an integer from 1 to 5")
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        score = int(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationValidationError(
            f"{label} must be an integer from 1 to 5"
        ) from exc
    if score < DIMENSION_MIN or score > DIMENSION_MAX:
        raise EvaluationValidationError(f"{label} must be between 1 and 5")
    return score


def validate_review(
    *,
    accuracy: Any,
    tone: Any,
    succinctness: Any,
    edge_cases: Any,
    comment: Any,
) -> tuple[dict[str, int], str]:
    """Validate and normalize the four-dimension review contract."""
    raw_scores = {
        "accuracy": accuracy,
        "tone": tone,
        "succinctness": succinctness,
        "edge_cases": edge_cases,
    }
    scores = {
        spec["key"]: _normalize_dimension(raw_scores[spec["key"]], spec["label"])
        for spec in DIMENSION_SPECS
    }

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
    return scores, normalized_comment


def _average(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _distribution(values: list[int], low: int, high: int) -> dict[str, int]:
    return {str(score): values.count(score) for score in range(low, high + 1)}


def _review_from_row(row: sqlite3.Row) -> dict[str, Any]:
    """Serialize one stored review, including legacy single-score rows."""
    dimensional = row["accuracy"] is not None
    return {
        "rubric": "dimensions" if dimensional else "legacy",
        "rating": None if dimensional else row["rating"],
        "accuracy": row["accuracy"],
        "tone": row["tone"],
        "succinctness": row["succinctness"],
        "edge_cases": row["edge_cases"],
        "comment": row["comment"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


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
        connection = sqlite3.connect(
            self.database_path, timeout=10, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_schema(connection)
            except Exception:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
        finally:
            connection.close()

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        existing = connection.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'evaluation_reviews'
            """).fetchone()
        if existing is None:
            connection.execute(_CREATE_REVIEWS_SQL)
        else:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(evaluation_reviews)")
            }
            if not set(DIMENSION_KEYS).issubset(columns):
                self._migrate_legacy_table(connection, columns)
        connection.execute(_CREATE_INDEX_SQL)
        connection.execute("PRAGMA user_version = 2")

    def _migrate_legacy_table(
        self, connection: sqlite3.Connection, columns: set[str]
    ) -> None:
        """Copy the previous single-score table forward without dropping rows.

        The rebuild runs inside the caller's immediate transaction. A failure
        rolls the rename back, so a partially applied migration cannot replace
        the live table.
        """
        if not _LEGACY_COLUMNS.issubset(columns):
            missing = sorted(_LEGACY_COLUMNS - columns)
            raise EvaluationDataError(
                "Evaluation database is missing required review columns "
                f"({', '.join(missing)}); refusing to migrate so existing "
                "reviews are left untouched."
            )

        original_count = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_reviews"
            ).fetchone()["count"]
        )
        legacy_rows = connection.execute("""
            SELECT run_id, question_id, candidate, model_side, reviewer,
                   rating, comment, created_at, updated_at
            FROM evaluation_reviews
            """).fetchall()
        connection.execute(
            "ALTER TABLE evaluation_reviews RENAME TO evaluation_reviews_legacy"
        )
        connection.execute(_CREATE_REVIEWS_SQL)
        connection.executemany(
            """
            INSERT INTO evaluation_reviews (
                run_id, question_id, candidate, model_side, reviewer,
                rating, accuracy, tone, succinctness, edge_cases,
                comment, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)
            """,
            [
                (
                    row["run_id"],
                    row["question_id"],
                    row["candidate"],
                    row["model_side"],
                    row["reviewer"],
                    row["rating"],
                    scrub_pii(row["comment"]),
                    row["created_at"],
                    row["updated_at"],
                )
                for row in legacy_rows
            ],
        )
        copied_count = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_reviews"
            ).fetchone()["count"]
        )
        if copied_count != original_count:
            raise EvaluationDataError(
                "Evaluation review migration did not preserve every existing "
                "row; the previous table was left unchanged."
            )
        connection.execute("DROP TABLE evaluation_reviews_legacy")
        logger.info(
            "Migrated evaluation reviews to the four-dimension rubric; "
            "preserved %s existing review(s) as legacy 1-7 scores",
            copied_count,
        )

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
                SELECT question_id, candidate, rating, accuracy, tone,
                       succinctness, edge_cases, comment, created_at, updated_at
                FROM evaluation_reviews
                WHERE run_id = ? AND reviewer = ?
                """,
                (run_id, reviewer),
            ).fetchall()
        return {
            (row["question_id"], row["candidate"]): _review_from_row(row)
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
            needs_rescore = any(
                candidate["review"] and candidate["review"]["rubric"] == "legacy"
                for candidate in candidates.values()
            )

            questions.append(
                {
                    "id": question_id,
                    "category": str(item.get("category", "")).strip(),
                    "question": str(item["question"]),
                    "review_focus": str(item.get("review_focus", "")),
                    "sources": [str(source) for source in item.get("sources", [])],
                    "candidates": candidates,
                    "complete": complete,
                    "needs_rescore": needs_rescore,
                    "reveal": reveal,
                }
            )

        completed = sum(1 for question in questions if question["complete"])
        return {
            "run_id": run_id,
            "created_at": payload.get("created_at"),
            "method": payload.get("method"),
            "reviewer": reviewer,
            "rubric": {
                "scale_min": DIMENSION_MIN,
                "scale_max": DIMENSION_MAX,
                "dimensions": dimension_definitions(),
                "rationale": "1-3 sentences",
            },
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
        accuracy: Any,
        tone: Any,
        succinctness: Any,
        edge_cases: Any,
        comment: Any,
    ) -> dict[str, Any]:
        """Insert or update one four-dimension reviewer judgment."""
        scores, normalized_comment = validate_review(
            accuracy=accuracy,
            tone=tone,
            succinctness=succinctness,
            edge_cases=edge_cases,
            comment=comment,
        )
        normalized_comment = scrub_pii(normalized_comment)
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
                    rating, accuracy, tone, succinctness, edge_cases,
                    comment, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, question_id, candidate, reviewer)
                DO UPDATE SET
                    model_side = excluded.model_side,
                    rating = NULL,
                    accuracy = excluded.accuracy,
                    tone = excluded.tone,
                    succinctness = excluded.succinctness,
                    edge_cases = excluded.edge_cases,
                    comment = excluded.comment,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    question_id,
                    normalized_candidate,
                    model_side,
                    reviewer,
                    scores["accuracy"],
                    scores["tone"],
                    scores["succinctness"],
                    scores["edge_cases"],
                    normalized_comment,
                    now,
                    now,
                ),
            )

        return {
            "question_id": question_id,
            "candidate": normalized_candidate,
            "rubric": "dimensions",
            "rating": None,
            **scores,
            "comment": normalized_comment,
            "updated_at": now,
        }

    def _model_summary(
        self, rows: list[sqlite3.Row], payload: dict[str, Any], side: str
    ) -> dict[str, Any]:
        side_rows = [row for row in rows if row["model_side"] == side]
        current_rows = [row for row in side_rows if row["accuracy"] is not None]
        legacy_rows = [row for row in side_rows if row["accuracy"] is None]
        legacy_ratings = [int(row["rating"]) for row in legacy_rows]
        dimensions = {}
        for key in DIMENSION_KEYS:
            scores = [int(row[key]) for row in current_rows]
            dimensions[key] = {
                "average": _average(scores),
                "distribution": _distribution(scores, DIMENSION_MIN, DIMENSION_MAX),
            }
        return {
            **payload[f"{side}_model"],
            "review_count": len(side_rows),
            "dimension_review_count": len(current_rows),
            "legacy_review_count": len(legacy_rows),
            "dimensions": dimensions,
            "legacy": {
                "average_rating": _average(legacy_ratings),
                "distribution": _distribution(
                    legacy_ratings, LEGACY_RATING_MIN, LEGACY_RATING_MAX
                ),
            },
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
                SELECT model_side, rating, accuracy, tone, succinctness, edge_cases
                FROM evaluation_reviews
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()

        return {
            "available": True,
            "run_id": run_id,
            "scale": {
                "min": DIMENSION_MIN,
                "max": DIMENSION_MAX,
                "legacy_min": LEGACY_RATING_MIN,
                "legacy_max": LEGACY_RATING_MAX,
            },
            "dimension_definitions": dimension_definitions(),
            "models": {
                side: self._model_summary(rows, payload, side)
                for side in ("left", "right")
            },
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
                       accuracy, tone, succinctness, edge_cases,
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
            "rubric",
            "rating",
            *DIMENSION_KEYS,
            "comment",
            "created_at",
            "updated_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            question = questions.get(row["question_id"], {})
            model = payload[f"{row['model_side']}_model"]
            dimensional = row["accuracy"] is not None
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
                    "rubric": "dimensions" if dimensional else "legacy",
                    "rating": "" if dimensional else row["rating"],
                    **{
                        key: row[key] if row[key] is not None else ""
                        for key in DIMENSION_KEYS
                    },
                    "comment": row["comment"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return output.getvalue()
