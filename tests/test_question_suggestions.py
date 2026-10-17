"""Curated chat suggestions: matching, the list endpoint, and the combobox."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

import pytest

import app as app_module
from question_suggestions import (
    load_question_suggestions,
    normalize_suggestion_text,
    rank_question_suggestions,
    suggestion_payload,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "question_suggestions.json"
TEMPLATES = (ROOT / "templates" / "index.html", ROOT / "docs" / "index.html")

_NORMALIZE_SAMPLES = (
    "INFO-289",
    "info289",
    "  How   do I   ",
    "e-Portfolio",
    "ISDA 160: User Experience (UX)",
    "What’s the GPA?",
    "ISDA140",
)


def _texts(ranked):
    return [item["text"] for item in ranked]


def _catalog():
    return load_question_suggestions()


def test_catalog_covers_topic_samples_and_starter_chips():
    questions = _catalog()
    texts = {item["text"] for item in questions}
    assert len(texts) == len(questions)
    assert len(questions) >= 20

    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    samples = []
    for payload in re.findall(r"data-samples='([^']+)'", template):
        samples.extend(json.loads(payload))
    assert samples
    assert set(samples) <= texts

    starters = re.findall(
        r'class="starter-chip"[^>]*data-question="([^"]+)"',
        template,
    )
    assert len(starters) == 4
    assert len(starters) == len(set(starters))
    assert set(starters) <= texts
    assert "sample-chips-area" not in template


def test_course_prompts_quote_catalog_titles():
    docs = [
        path.read_text(encoding="utf-8", errors="replace")
        for path in (ROOT / "data" / "advising_docs" / "txt").glob("*.txt")
    ]
    found = 0
    for item in _catalog():
        match = re.fullmatch(r"What is ([A-Z]+ \d+[A-Z]?): (.+)\?", item["text"])
        if not match:
            continue
        found += 1
        code, title = match.group(1), match.group(2)
        assert any(code in body and title in body for body in docs), item["text"]
        assert item["topic"] == "Courses"
        assert item["programs"] == ["isda"] or item["programs"] == ["mlis"]
    assert found >= 10


def test_catalog_excludes_sensitive_benchmark_questions():
    benchmark = json.loads(
        (ROOT / "evals" / "advising_questions.json").read_text(encoding="utf-8")
    )
    excluded = {
        item["question"]
        for item in benchmark
        if item["category"]
        in {
            "out-of-scope and sensitive-data",
            "distress and academic failure",
        }
    }
    texts = {item["text"] for item in _catalog()}
    assert excluded
    assert excluded.isdisjoint(texts)
    source = (ROOT / "src" / "question_suggestions.py").read_text(encoding="utf-8")
    assert "anonymized_questions" not in source
    assert "POPULAR_QUESTIONS" not in source


def test_short_or_unknown_queries_match_nothing():
    questions = _catalog()
    for query in ("", " ", "a", "xyzzy", "??"):
        assert rank_question_suggestions(query, questions) == []


def test_prefix_course_code_and_program_order():
    questions = _catalog()
    core = _texts(rank_question_suggestions("core courses", questions, program="mlis"))
    assert "What are the MLIS core courses?" in core
    assert "What are the ISDA core courses?" in core
    assert core.index("What are the MLIS core courses?") < core.index(
        "What are the ISDA core courses?"
    )

    info = _texts(rank_question_suggestions("INFO289", questions, program="isda"))
    assert info[0] == "What is INFO 289: e-Portfolio?"

    big_data = _texts(rank_question_suggestions("big data", questions))
    assert big_data[0] == "What is ISDA 140: Big Data Analytics and Management?"

    scholarships = _texts(rank_question_suggestions("scholarship", questions))
    assert "Are there scholarships for ISDA students?" in scholarships

    limited = rank_question_suggestions("how", questions, limit=3)
    assert len(limited) == 3
    assert all(item["text"].lower().startswith("how") for item in limited)


def test_invalid_entries_are_skipped(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            {
                "questions": [
                    {"text": "How do I add or drop a course?", "programs": ["both"]},
                    {"text": "How do I add or drop a course?", "programs": ["isda"]},
                    {"text": "  ", "programs": ["both"]},
                    {"text": "Look at my transcript", "programs": ["secret"]},
                    "not-an-object",
                    {"text": "Where is the iSchool advising office?"},
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = load_question_suggestions(path)
    assert [item["text"] for item in loaded] == [
        "How do I add or drop a course?",
        "Where is the iSchool advising office?",
    ]
    assert loaded[1]["programs"] == ["both"]


def test_broken_catalog_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "question_suggestions.DEFAULT_SUGGESTIONS_PATH",
        tmp_path / "missing.json",
    )
    assert suggestion_payload() == {"enabled": False, "questions": []}


@pytest.mark.parametrize(
    "value, enabled",
    [
        (None, True),
        ("true", True),
        ("YES", True),
        ("1", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
        ("maybe", False),
    ],
)
def test_feature_flag(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("QUESTION_SUGGESTIONS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("QUESTION_SUGGESTIONS_ENABLED", value)
    payload = suggestion_payload()
    assert payload["enabled"] is enabled
    assert bool(payload["questions"]) is enabled


def test_endpoint_serves_curated_list_without_chatbot_or_typed_text(
    monkeypatch, caplog
):
    def boom():
        raise AssertionError("suggestion lookup initialized the chatbot")

    monkeypatch.setattr(app_module, "get_chatbot", boom)
    monkeypatch.delenv("QUESTION_SUGGESTIONS_ENABLED", raising=False)
    monkeypatch.delenv("DYNAMIC_SUGGESTIONS_ENABLED", raising=False)
    caplog.set_level(logging.DEBUG)
    secret = "partial-question-should-not-be-logged"
    response = app_module.app.test_client().get(
        "/api/question-suggestions",
        query_string={"q": secret},
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["enabled"] is True
    assert body["dynamic"] is False
    assert "starters" not in body
    assert "popularity" not in body
    assert body["questions"] == _catalog()
    assert secret not in caplog.text
    assert secret not in response.get_data(as_text=True)
    assert "query" not in body

    route = (ROOT / "app.py").read_text(encoding="utf-8")
    handler = route.split("def question_suggestions(", 1)[1].split("\n@", 1)[0]
    assert "request.args" not in handler
    assert "anonymized" not in handler
    assert "popular" not in handler.lower()


def test_flag_off_endpoint_is_empty(monkeypatch):
    monkeypatch.setenv("QUESTION_SUGGESTIONS_ENABLED", "off")
    response = app_module.app.test_client().get("/api/question-suggestions")
    assert response.status_code == 200
    assert response.get_json() == {"enabled": False, "questions": []}


def test_combobox_markup_is_on_both_pages_and_client_does_not_send_partials():
    for path in TEMPLATES:
        html = path.read_text(encoding="utf-8")
        assert 'role="combobox"' in html
        assert 'aria-autocomplete="list"' in html
        assert 'aria-controls="question-suggestions"' in html
        assert 'id="question-suggestions" class="suggest-list" role="listbox"' in html
        assert 'id="question-suggestions-status"' in html
        assert 'aria-live="polite"' in html
        assert "suggestions.js?v=20261017-dynamic-suggestions" in html or (
            "filename='suggestions.js', v='20261017-dynamic-suggestions'" in html
        )

    script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    loader = script.split("async function loadQuestionSuggestions(", 1)[1].split(
        "function initQuestionSuggestions(", 1
    )[0]
    fetch_call = loader.split("fetch(", 1)[1].split(");", 1)[0]
    assert 'apiUrl("/api/question-suggestions")' in fetch_call
    assert "userInput" not in fetch_call
    assert "console.log" not in script
    suggestions = (ROOT / "static" / "suggestions.js").read_text(encoding="utf-8")
    assert "fetch(" not in suggestions
    assert "console." not in suggestions
    assert "SUGGESTION_DEBOUNCE_MS = 120" in suggestions
    styles = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ".suggest-list" in styles
    assert "body.is-embed .suggest-list" in styles


def test_browser_matcher_matches_python(tmp_path):
    questions = _catalog()
    cases = []
    for query, program, limit in (
        ("core courses", "mlis", 6),
        ("INFO289", "isda", 6),
        ("big data", "both", 6),
        ("scholarship", "isda", 6),
        ("how do i", "both", 4),
        ("xyzzy", "both", 6),
        ("a", "both", 6),
        ("waitlist", "mlis", 6),
        ("e-portfolio", "both", 6),
        ("isda 160", "mlis", 6),
    ):
        ranked = rank_question_suggestions(
            query, questions, program=program, limit=limit
        )
        cases.append(
            {
                "query": query,
                "program": program,
                "limit": limit,
                "expected": _texts(ranked),
                "normalized": normalize_suggestion_text(query),
            }
        )
    payload = {
        "questions": questions,
        "cases": cases,
        "samples": [
            {"text": sample, "normalized": normalize_suggestion_text(sample)}
            for sample in _NORMALIZE_SAMPLES
        ],
        "keys": [
            {
                "key": "Escape",
                "state": {"open": True, "activeIndex": 1, "count": 3},
                "type": "close",
            },
            {
                "key": "Escape",
                "state": {"open": False, "activeIndex": -1, "count": 0},
                "type": "none",
            },
            {
                "key": "ArrowDown",
                "state": {"open": False, "activeIndex": -1, "count": 4},
                "type": "move",
            },
            {
                "key": "ArrowDown",
                "state": {"open": True, "activeIndex": 3, "count": 4},
                "type": "move",
            },
            {
                "key": "ArrowUp",
                "state": {"open": True, "activeIndex": 0, "count": 4},
                "type": "move",
            },
            {
                "key": "Enter",
                "state": {"open": True, "activeIndex": 2, "count": 4},
                "type": "select",
            },
            {
                "key": "Tab",
                "state": {"open": True, "activeIndex": 0, "count": 4},
                "type": "select",
            },
            {
                "key": "Enter",
                "state": {"open": False, "activeIndex": -1, "count": 4},
                "type": "none",
            },
            {
                "key": "Tab",
                "state": {"open": False, "activeIndex": -1, "count": 4},
                "type": "none",
            },
            {
                "key": "ArrowDown",
                "state": {"open": True, "count": 2, "isComposing": True},
                "type": "none",
            },
        ],
    }
    case_path = tmp_path / "cases.json"
    case_path.write_text(json.dumps(payload), encoding="utf-8")
    runner = tmp_path / "check_suggestions.js"
    runner.write_text(
        r"""
const fs = require("fs");
const vm = require("vm");
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const sandbox = { console };
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const rank = sandbox.rankQuestionSuggestions;
const normalize = sandbox.normalizeSuggestionText;
const keyAction = sandbox.suggestionKeyAction;
if (typeof rank !== "function") throw new Error("rankQuestionSuggestions missing");
for (const sample of payload.samples) {
  if (normalize(sample.text) !== sample.normalized) {
    throw new Error("Normalize mismatch for " + sample.text + ": " + normalize(sample.text));
  }
}
for (const item of payload.cases) {
  const ranked = rank(item.query, payload.questions, { program: item.program, limit: item.limit });
  const texts = ranked.map((question) => question.text);
  if (JSON.stringify(texts) !== JSON.stringify(item.expected)) {
    throw new Error(
      "Rank mismatch for " + JSON.stringify(item.query) +
      "\nexpected " + JSON.stringify(item.expected) +
      "\ngot " + JSON.stringify(texts)
    );
  }
}
for (const item of payload.keys) {
  const action = keyAction(item.key, item.state);
  if (action.type !== item.type) {
    throw new Error(item.key + " expected " + item.type + " got " + action.type);
  }
  if (item.key === "ArrowDown" && item.state.open && item.state.activeIndex === 3 && action.index !== 3) {
    throw new Error("ArrowDown did not stay on the last option");
  }
  if (item.key === "ArrowDown" && !item.state.open && item.type === "move" && action.index !== 0) {
    throw new Error("ArrowDown on a closed list did not start at the first option");
  }
  if (item.key === "Enter" && item.type === "select" && action.index !== 2) {
    throw new Error("Enter did not select the active option");
  }
}
if (sandbox.SUGGESTION_DEBOUNCE_MS !== 120) throw new Error("debounce changed");
console.log("ok");
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "suggestions.js"), str(case_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert completed.stdout.strip() == "ok"
