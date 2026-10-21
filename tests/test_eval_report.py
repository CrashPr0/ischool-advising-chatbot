"""Retrieval evaluation must stay offline and expose misses honestly."""

import json
import socket

from langchain_core.documents import Document
import pytest

from scripts.eval_report import (
    ROOT,
    evaluate,
    load_cases,
    render_report,
    run_report,
    source_key,
)

A = "https://ischool.sjsu.edu/page-a"
B = "https://ischool.sjsu.edu/page-b"


def document(url=None):
    return Document(
        page_content="Advising", metadata={} if url is None else {"source_url": url}
    )


def test_recall_deduplicates_pages_and_excludes_unscored_cases():
    cases = [
        {"id": "partial", "question": "partial", "expected_sources": [A, B]},
        {"id": "miss", "question": "miss", "expected_sources": [B]},
        {"id": "boundary", "question": "boundary", "skip_reason": "No source expected"},
    ]
    outputs = {
        "partial": [document(A), document(A + "#section"), document(B)],
        "miss": [],
        "boundary": [document(B)],
    }
    times = iter([0, 0.01, 1, 1.02, 2, 2.03])
    result = evaluate(
        cases, lambda q, k: outputs[q], {A, B}, 2, clock=lambda: next(times)
    )
    assert result["scored"] == 2
    assert result["hit_rate"] == 0.5
    assert result["recall"] == 0.25
    assert result["rows"][0]["matched"] == [A]
    assert result["rows"][1]["recall"] == 0
    assert result["rows"][2]["recall"] is None
    assert result["link_count"] == result["valid_links"] == 3
    assert result["mean_ms"] == pytest.approx(20)
    assert result["p50_ms"] == pytest.approx(20)
    assert result["p95_ms"] == pytest.approx(30)


def test_invalid_missing_and_unknown_source_links_fail():
    docs = [document(A), document(), document("javascript:alert(1)"), document(B)]
    result = evaluate(
        [{"id": "links", "question": "links", "expected_sources": [A]}],
        lambda q, k: docs,
        {A},
        4,
    )
    assert result["link_validity"] == 0.25
    assert result["link_count"] == 4
    assert result["hit_rate"] == 1


def test_empty_retrieval_is_not_perfect_link_validity():
    result = evaluate(
        [{"id": "empty", "question": "empty", "expected_sources": [A]}],
        lambda q, k: [],
        {A},
        4,
    )
    assert result["hit_rate"] == result["recall"] == 0
    assert result["link_validity"] is None
    assert "N/A (0/0)" in render_report(result, {})


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///tmp/a",
        "https://",
        "https://user@host/a",
        "https://host:bad/a",
        "https://host:8080/a",
        "https://host/a b",
    ],
)
def test_source_key_rejects_invalid_urls(url):
    assert source_key(url) == ""


def test_source_key_keeps_catalog_query_identity():
    url = "https://catalog.sjsu.edu/course.php?catoid=23&coid=1"
    assert source_key(url + "#top") == url
    assert source_key(url) != source_key(url.replace("23", "17"))
    assert source_key(A + "/#top") == A


@pytest.mark.parametrize("k", [0, 21])
def test_k_cannot_silently_exceed_production_limit(k):
    with pytest.raises(ValueError, match="k must"):
        evaluate([], lambda q, k: [], set(), k)


def test_expected_sources_must_exist_before_retrieval():
    def forbidden(*args):
        pytest.fail("Retrieval should not run with stale source labels")

    with pytest.raises(ValueError, match="absent from corpus"):
        evaluate(
            [{"id": "bad", "question": "bad", "expected_sources": [B]}],
            forbidden,
            {A},
            4,
        )


def test_committed_labels_cover_every_curated_question():
    cases = load_cases(
        ROOT / "evals/advising_questions.json",
        ROOT / "evals/retrieval_expectations.json",
    )
    assert len(cases) == 21
    assert sum(bool(case.get("expected_sources")) for case in cases) == 16


@pytest.mark.parametrize(
    "questions, labels",
    [
        ([{"id": "a", "question": "q"}] * 2, {"a": {"expected_sources": [A]}}),
        ([{"id": "a", "question": "q"}], {"b": {"expected_sources": [A]}}),
        ([{"id": "a", "question": "q"}], {"a": {}}),
        (
            [{"id": "a", "question": "q"}],
            {"a": {"expected_sources": [A], "skip_reason": "skip"}},
        ),
        ([{"id": "a", "question": "q"}], {"a": {"expected_sources": ["not a URL"]}}),
        ([{"id": "a", "question": " "}], {"a": {"expected_sources": [A]}}),
    ],
)
def test_bad_label_inputs_fail(tmp_path, questions, labels):
    question_path = tmp_path / "questions.json"
    label_path = tmp_path / "labels.json"
    question_path.write_text(json.dumps(questions))
    label_path.write_text(json.dumps(labels))
    with pytest.raises(ValueError):
        load_cases(question_path, label_path)


def test_real_offline_pipeline_ignores_hosted_settings_and_preserves_index(
    tmp_path, monkeypatch
):
    from src.config import Config

    def forbidden(*args, **kwargs):
        pytest.fail("Offline evaluation attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(Config, "EMBEDDING_PROVIDER", "openai")
    saved_index = tmp_path / "app-index"
    saved_index.mkdir()
    sentinel = saved_index / "keep.txt"
    sentinel.write_text("existing app index")
    monkeypatch.setattr(Config, "VECTOR_STORE_PATH", str(saved_index))
    docs = tmp_path / "corpus"
    docs.mkdir()
    (docs / "advising.txt").write_text(
        "Schedule an advising appointment using Spartan Connect."
    )
    (docs / "advising.yml").write_text(f"source_url: {A}\nprograms: [ISDA]\n")
    questions = tmp_path / "questions.json"
    labels = tmp_path / "labels.json"
    questions.write_text(
        json.dumps([{"id": "advising", "question": "advising appointment"}])
    )
    labels.write_text(json.dumps({"advising": {"expected_sources": [A]}}))
    report = run_report(docs, questions, labels, 4)
    assert "100.00% (1/1)" in report
    assert "Corpus content + metadata SHA-256" in report
    assert "### advising" in report
    assert Config.EMBEDDING_PROVIDER == "openai"
    assert list(saved_index.iterdir()) == [sentinel]
    assert sentinel.read_text() == "existing app index"
