"""Timestamp boundaries for the read-only popular-question aggregate."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.anonymized_questions import (
    ensure_anonymized_questions_table,
    popular_questions,
)


@pytest.mark.parametrize("days", [7, 30, 90])
def test_popular_window_excludes_future_rows(tmp_path, days):
    database = tmp_path / "reviews.sqlite3"
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    # Different offsets must be compared as instants, not as timestamp strings.
    offset = timezone(timedelta(hours=-7))
    rows = [
        ((now - timedelta(days=days)).isoformat(), "At the cutoff"),
        (now.astimezone(offset).isoformat(), "At the end"),
        ((now + timedelta(seconds=1)).isoformat(), "At the end"),
        ((now + timedelta(days=1)).isoformat(), "Future only"),
    ]
    with sqlite3.connect(database) as connection:
        ensure_anonymized_questions_table(connection)
        connection.executemany(
            "INSERT INTO anonymized_student_questions VALUES (?, ?)", rows
        )

    assert popular_questions(database, days=days, now=now) == [
        {"question": "At the cutoff", "count": 1},
        {"question": "At the end", "count": 1},
    ]
