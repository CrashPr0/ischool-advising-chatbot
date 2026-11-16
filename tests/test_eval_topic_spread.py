"""The flags-off/on comparison stays offline, deterministic, and parity-checked."""

from langchain_core.documents import Document

from scripts.eval_report import ROOT
from scripts.eval_topic_spread import offline_summarizer, run, score
from src.summary_chunks import PAGE_PROMPT, TOPIC_PROMPT


def test_offline_summarizer_is_extractive_and_deterministic():
    page = PAGE_PROMPT.format(
        title="BSISDA Advising",
        url="https://ischool.sjsu.edu/bsisda-advising",
        text="Short.\nStudents changing major must meet the published GPA requirement first.",
    )
    assert offline_summarizer(page) == offline_summarizer(page)
    assert "published GPA requirement" in offline_summarizer(page)
    topic = TOPIC_PROMPT.format(
        count=2, topic="advising", program="ISDA", pages="Page: A\nURL: x\n\nPage: B\nURL: y"
    )
    assert offline_summarizer(topic) == "Official pages about advising for ISDA: A; B."


def test_score_counts_topics_and_summary_coverage():
    covered = "https://ischool.sjsu.edu/bsisda-advising"
    documents = [
        Document(
            page_content="overview",
            metadata={
                "is_summary": True,
                "topic_cluster": "advising",
                "source_url": "https://ischool.sjsu.edu/bsisda-faqs",
                "summary_source_urls": ["https://ischool.sjsu.edu/bsisda-faqs", covered],
            },
        )
    ]
    row = score(
        {"id": "x", "expected_sources": [covered], "expected_topics": ["advising", "costs"]},
        documents,
    )
    assert row["recall"] == 0
    assert row["covered_recall"] == 1
    assert row["topic_recall"] == 0.5
    assert row["summaries"] == 1


def test_full_offline_comparison_keeps_parity_and_exact_answers():
    report = run(
        ROOT / "data/advising_docs",
        ROOT / "evals/advising_questions.json",
        ROOT / "evals/retrieval_expectations.json",
        ROOT / "evals/broad_questions.json",
        8,
        "offline",
        None,
    )
    assert "**identical for every question**" in report
    for setting in ("spread", "summaries", "both"):
        line = next(row for row in report.splitlines() if row.startswith(f"| {setting} |"))
        assert line.endswith("| none |"), line
