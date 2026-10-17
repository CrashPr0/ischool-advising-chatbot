"""Personal-information scrubbing before free text is stored."""

import json
import sqlite3
from pathlib import Path

from src.evaluation_store import EvaluationStore
from src.issue_reports import IssueReportStore, build_issue_report
from src.pii_scrubber import (
    REDACTED_ADDRESS,
    REDACTED_EMAIL,
    REDACTED_PHONE,
    REDACTED_STUDENT_ID,
    scrub_pii,
    scrub_pii_value,
)
from src.stripped_link_log import OUTCOME_EDITED, StrippedLinkLog


def test_scrub_pii_redacts_student_ids_emails_phones_and_addresses():
    text = (
        "Student 012345678 (also 012-345-678) can be reached at "
        "ada.lovelace@sjsu.edu or (408) 555-1234, 408-555-1234, "
        "408.555.1234, or +1 408 555 1234. Address: 123 Main St Apt 4."
    )
    scrubbed = scrub_pii(text)

    assert "012345678" not in scrubbed
    assert "012-345-678" not in scrubbed
    assert "ada.lovelace@sjsu.edu" not in scrubbed
    assert "408" not in scrubbed
    assert "555-1234" not in scrubbed
    assert "123 Main St" not in scrubbed
    assert scrubbed.count(REDACTED_STUDENT_ID) == 2
    assert scrubbed.count(REDACTED_EMAIL) == 1
    assert scrubbed.count(REDACTED_PHONE) == 4
    assert scrubbed.count(REDACTED_ADDRESS) == 1
    assert scrub_pii(scrubbed) == scrubbed


def test_scrub_pii_redacts_each_address_shape():
    samples = [
        "456 Oak Ave",
        "789 Pine Blvd.",
        "10 Elm Rd #12",
        "55 Broadway Way",
        "9 Park Ct Unit 4B",
        "8 Hill Pl",
        "100 First Ln",
        "400 South 4th Dr Apt 12",
        "12 King Street",
    ]
    for sample in samples:
        scrubbed = scrub_pii(f"Mail it to {sample} please.")
        assert sample not in scrubbed
        assert REDACTED_ADDRESS in scrubbed


def test_scrub_pii_leaves_course_codes_years_dates_and_short_numbers():
    samples = [
        "INFO 202",
        "ISDA 150",
        "INFO202",
        "Fall 2026",
        "2024",
        "10/17/2026",
        "2026-10-17",
        "October 17, 2026",
        "3 credits",
        "section 01",
        "room 123",
        "12345",
        "I need 9 units in INFO 289.",
    ]
    for sample in samples:
        assert scrub_pii(sample) == sample


def test_scrub_pii_value_walks_nested_dicts_and_lists():
    payload = {
        "question": "My id is 012345678",
        "turns": [
            {"role": "user", "text": "Email ada@sjsu.edu about INFO 202"},
            {"role": "assistant", "text": "Call 408-555-1234"},
        ],
        "count": 9,
        "ok": True,
        "empty": None,
    }
    scrubbed = scrub_pii_value(payload)

    assert scrubbed["question"] == f"My id is {REDACTED_STUDENT_ID}"
    assert scrubbed["turns"][0]["role"] == "user"
    assert "ada@sjsu.edu" not in scrubbed["turns"][0]["text"]
    assert "INFO 202" in scrubbed["turns"][0]["text"]
    assert scrubbed["turns"][1]["text"] == f"Call {REDACTED_PHONE}"
    assert scrubbed["count"] == 9
    assert scrubbed["ok"] is True
    assert scrubbed["empty"] is None
    assert payload["question"] == "My id is 012345678"


def test_issue_report_stores_scrubbed_comments_transcripts_questions_and_answers(tmp_path):
    record = build_issue_report(
        {
            "reason": "incorrect_info",
            "comment": "Student 012-345-678 wrote ada@sjsu.edu.",
            "question": "Call (408) 555-1234 about INFO 202 in Fall 2026?",
            "answer": "Meet at 123 Main St Apt 4. ISDA 150 still counts.",
            "include_conversation": True,
            "turns": [
                {
                    "role": "user",
                    "text": "My id is 012345678 and I live at 10 Oak Ave.",
                },
                {
                    "role": "assistant",
                    "text": "See mailto:advisor@sjsu.edu and INFO 202.",
                },
            ],
        }
    )
    database = tmp_path / "reviews.sqlite3"
    report_id = IssueReportStore(database).save(record)
    stored = IssueReportStore(database).get(report_id)

    assert stored["comment"] == f"Student {REDACTED_STUDENT_ID} wrote {REDACTED_EMAIL}."
    assert "INFO 202" in stored["question"]
    assert "Fall 2026" in stored["question"]
    assert REDACTED_PHONE in stored["question"]
    assert "(408) 555-1234" not in stored["question"]
    assert "ISDA 150" in stored["answer"]
    assert REDACTED_ADDRESS in stored["answer"]
    assert "123 Main St" not in stored["answer"]
    assert stored["turns"][0]["text"] == (
        f"My id is {REDACTED_STUDENT_ID} and I live at {REDACTED_ADDRESS}"
    )
    joined = json.dumps(stored)
    assert "012345678" not in joined
    assert "012-345-678" not in joined
    assert "ada@sjsu.edu" not in joined
    assert "advisor@sjsu.edu" not in joined
    blob = database.read_bytes()
    assert b"012345678" not in blob
    assert b"ada@sjsu.edu" not in blob
    assert b"advisor@sjsu.edu" not in blob
    assert b"123 Main St" not in blob


def test_reviewer_comment_is_scrubbed_before_it_is_stored(tmp_path):
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "created_at": "2026-09-19T12:00:00+00:00",
                "method": "Same prompt.",
                "left_model": {"provider": "openai", "model": "left", "label": "Left"},
                "right_model": {"provider": "openai", "model": "right", "label": "Right"},
                "results": [
                    {
                        "id": "q1",
                        "category": "degree requirements",
                        "question": "Which course should I take next?",
                        "review_focus": "Grounded advice.",
                        "sources": [],
                        "left": {"answer": "Take INFO 202.", "error": None},
                        "right": {"answer": "Take ISDA 150.", "error": None},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    database = tmp_path / "reviews.sqlite3"
    store = EvaluationStore(results, database)
    saved = store.save_review(
        reviewer="Reviewer One",
        question_id="q1",
        candidate="A",
        accuracy=5,
        tone=4,
        succinctness=4,
        edge_cases=3,
        comment="The note names student 012345678 and ada@sjsu.edu.",
    )
    assert REDACTED_STUDENT_ID in saved["comment"]
    assert REDACTED_EMAIL in saved["comment"]
    assert "012345678" not in saved["comment"]
    with sqlite3.connect(database) as connection:
        comment = connection.execute(
            "SELECT comment FROM evaluation_reviews"
        ).fetchone()[0]
    assert comment == saved["comment"]
    assert b"012345678" not in Path(database).read_bytes()


def test_stripped_link_question_is_scrubbed_before_it_is_stored(tmp_path):
    database = tmp_path / "stripped_links.sqlite3"
    log = StrippedLinkLog(database)
    log.record(
        question="Where is form 012-345-678 for ada@sjsu.edu in INFO 202?",
        stripped_urls=["https://evil.example/form"],
        outcome=OUTCOME_EDITED,
    )
    stored = log.records()[0]
    assert stored["question"] == (
        f"Where is form {REDACTED_STUDENT_ID} for {REDACTED_EMAIL} in INFO 202?"
    )
    assert stored["stripped_urls"] == ["https://evil.example/form"]
    assert b"ada@sjsu.edu" not in database.read_bytes()
    assert b"012-345-678" not in database.read_bytes()
