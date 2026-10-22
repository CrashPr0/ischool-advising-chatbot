"""Reviewer counts of anonymized student questions, and the publish flag."""

from datetime import datetime, timedelta, timezone
import sqlite3
import subprocess
from pathlib import Path

import pytest

import app as app_module
from src.anonymized_questions import (
    ensure_anonymized_questions_table,
    popular_questions,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def _stamp(*, days=0, seconds=0):
    return (NOW - timedelta(days=days, seconds=seconds)).isoformat()


def _insert(database, rows):
    connection = sqlite3.connect(database)
    try:
        ensure_anonymized_questions_table(connection)
        connection.executemany(
            "INSERT INTO anonymized_student_questions (created_at, question) VALUES (?, ?)",
            rows,
        )
        connection.commit()
        return connection.execute(
            "SELECT COUNT(*) FROM anonymized_student_questions"
        ).fetchone()[0]
    finally:
        connection.close()


def _row_count(database):
    connection = sqlite3.connect(database)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM anonymized_student_questions"
        ).fetchone()[0]
    finally:
        connection.close()


def test_counts_ignore_case_and_whitespace_and_honor_window_edges(tmp_path):
    database = tmp_path / "reviews.sqlite3"
    stored = _insert(
        database,
        [
            (_stamp(days=2), "how do i apply to the mlis?"),
            (_stamp(days=1), "How   do I\napply to the MLIS?"),
            (_stamp(days=7), "what is info 202?"),
            (_stamp(days=6), "  What is INFO 202?  "),
            (_stamp(days=7, seconds=1), "Which electives count?"),
            (_stamp(days=30), "Which electives count?"),
            (_stamp(days=30, seconds=1), "When is the e-portfolio due?"),
            (_stamp(days=90), "When is the e-portfolio due?"),
            (_stamp(days=90, seconds=1), "Too old to count"),
            (_stamp(days=1), "   \n\t  "),
            ("not-a-timestamp", "Ignore this broken row"),
        ],
    )

    week = popular_questions(database, days=7, now=NOW)
    assert week == [
        {"question": "How do I apply to the MLIS?", "count": 2},
        {"question": "What is INFO 202?", "count": 2},
    ]
    month = popular_questions(database, days=30, now=NOW)
    assert month == [
        {"question": "How do I apply to the MLIS?", "count": 2},
        {"question": "What is INFO 202?", "count": 2},
        {"question": "Which electives count?", "count": 2},
    ]
    quarter = popular_questions(database, days=90, now=NOW)
    assert quarter == [
        {"question": "How do I apply to the MLIS?", "count": 2},
        {"question": "What is INFO 202?", "count": 2},
        {"question": "When is the e-portfolio due?", "count": 2},
        {"question": "Which electives count?", "count": 2},
    ]
    assert popular_questions(database, days=90, now=NOW) == quarter
    assert _row_count(database) == stored

    same_time = tmp_path / "tie.sqlite3"
    stamp = _stamp(days=1)
    _insert(
        same_time,
        [(stamp, "first wording"), (stamp, "First   Wording")],
    )
    assert popular_questions(same_time, days=7, now=NOW) == [
        {"question": "First Wording", "count": 2}
    ]


def test_missing_database_or_table_is_an_empty_read(tmp_path):
    assert popular_questions(tmp_path / "missing.sqlite3", days=30, now=NOW) == []
    empty = tmp_path / "empty.sqlite3"
    sqlite3.connect(empty).close()
    assert popular_questions(empty, days=7, now=NOW) == []
    with pytest.raises(ValueError, match="7, 30, or 90"):
        popular_questions(empty, days=14, now=NOW)


def _live_rows():
    moment = datetime.now(timezone.utc)
    return [
        ((moment - timedelta(days=1)).isoformat(), "How   do I\napply?"),
        ((moment - timedelta(days=2)).isoformat(), "how do i apply?"),
        ((moment - timedelta(days=10)).isoformat(), "Only in the month"),
        ((moment - timedelta(days=40)).isoformat(), "Only in the quarter"),
        ((moment - timedelta(days=100)).isoformat(), "Too old to count"),
    ]


def _questions(response):
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    return body["days"], body["questions"]


def test_reviewer_windows_auth_and_public_flag(monkeypatch, tmp_path, caplog):
    database = tmp_path / "reviews.sqlite3"
    stored = _insert(database, _live_rows())
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "test-review-token")
    monkeypatch.delenv("EVAL_ALLOWED_REVIEWERS", raising=False)
    monkeypatch.delenv("POPULAR_QUESTIONS_PUBLIC", raising=False)
    caplog.set_level("INFO")

    anonymous = app_module.app.test_client()
    assert anonymous.get("/api/evaluation/popular-questions").status_code == 401
    assert anonymous.get("/api/popular-questions").status_code == 404
    assert anonymous.get("/api/popular-questions?days=nope").status_code == 404

    reviewer = app_module.app.test_client()
    rejected = reviewer.post(
        "/api/evaluation/session",
        json={"reviewer": "reviewer@sjsu.edu", "access_token": "wrong"},
    )
    assert rejected.status_code == 401
    assert reviewer.get("/api/evaluation/popular-questions").status_code == 401
    signed_in = reviewer.post(
        "/api/evaluation/session",
        json={"reviewer": "reviewer@sjsu.edu", "access_token": "test-review-token"},
    )
    assert signed_in.status_code == 200

    for raw in ("14", "0", "-7", "all", "30.0"):
        denied = reviewer.get(f"/api/evaluation/popular-questions?days={raw}")
        assert denied.status_code == 400
        assert denied.get_json()["error"] == "days must be 7, 30, or 90"

    days, week = _questions(reviewer.get("/api/evaluation/popular-questions?days=7"))
    assert days == 7
    assert week == [{"question": "How do I apply?", "count": 2}]
    days, month = _questions(reviewer.get("/api/evaluation/popular-questions"))
    assert days == 30
    assert month == [
        {"question": "How do I apply?", "count": 2},
        {"question": "Only in the month", "count": 1},
    ]
    days, blank = _questions(reviewer.get("/api/evaluation/popular-questions?days="))
    assert days == 30
    assert blank == month
    days, quarter = _questions(reviewer.get("/api/evaluation/popular-questions?days=90"))
    assert days == 90
    assert quarter == [
        {"question": "How do I apply?", "count": 2},
        {"question": "Only in the month", "count": 1},
        {"question": "Only in the quarter", "count": 1},
    ]
    assert _questions(reviewer.get("/api/evaluation/popular-questions?days=90"))[1] == quarter
    assert _row_count(database) == stored
    assert "How do I apply" not in caplog.text
    assert "Only in the quarter" not in caplog.text

    for value in ("", "0", "false", "no", "off", "yes-please"):
        monkeypatch.setenv("POPULAR_QUESTIONS_PUBLIC", value)
        assert anonymous.get("/api/popular-questions").status_code == 404

    for value in ("1", "true", "TRUE", " yes ", "on"):
        monkeypatch.setenv("POPULAR_QUESTIONS_PUBLIC", value)
        days, published = _questions(anonymous.get("/api/popular-questions?days=30"))
        assert days == 30
        assert published == month
    published_invalid = anonymous.get("/api/popular-questions?days=14")
    assert published_invalid.status_code == 400

    monkeypatch.setenv("EVAL_ALLOWED_REVIEWERS", "reviewer@sjsu.edu")
    monkeypatch.delenv("EVAL_ACCESS_TOKEN", raising=False)
    outsider = app_module.app.test_client()
    blocked = outsider.post(
        "/api/evaluation/session",
        json={"reviewer": "other@sjsu.edu"},
    )
    assert blocked.status_code == 403
    assert "not approved" in blocked.get_json()["error"]
    assert outsider.get("/api/evaluation/popular-questions").status_code == 401
    approved = outsider.post(
        "/api/evaluation/session",
        json={"reviewer": "Reviewer@SJSU.edu"},
    )
    assert approved.status_code == 200
    assert _questions(outsider.get("/api/evaluation/popular-questions?days=7"))[1] == week


def test_reviewer_page_lists_windows_and_renders_counts_as_text(tmp_path):
    page = (ROOT / "templates" / "evaluation.html").read_text(encoding="utf-8")
    section = page.split('id="popular-questions"', 1)[1].split("</section>", 1)[0]
    assert 'id="popular-questions" class="popular-questions hidden"' in page
    assert 'aria-labelledby="popular-questions-title"' in page
    assert 'role="group" aria-label="Time window"' in section
    assert 'role="status" aria-live="polite"' in section
    assert 'aria-label="Most-asked questions"' in section
    for days, pressed in (("7", "false"), ("30", "true"), ("90", "false")):
        assert f'data-days="{days}" aria-pressed="{pressed}"' in section
    script = (ROOT / "static" / "evaluation.js").read_text(encoding="utf-8")
    assert "function loadPopularQuestions()" in script
    assert 'elements.popularQuestions.classList.add("hidden")' in script
    render = script.split("function renderPopularQuestions", 1)[1].split(
        "async function loadPopularQuestions", 1
    )[0]
    assert "innerHTML" not in render
    assert "textContent" in render

    runner = tmp_path / "check_popular_questions.js"
    runner.write_text(_POPULAR_RUNNER, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "evaluation.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert completed.stdout.strip() == "ok"


_POPULAR_RUNNER = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.handlers = {};
    this.value = '';
    this.open = false;
    this._text = '';
    const classes = new Set();
    this.classList = {
      add: name => classes.add(name),
      remove: name => classes.delete(name),
      contains: name => classes.has(name),
      toggle: (name, force) => {
        const on = force === undefined ? !classes.has(name) : Boolean(force);
        if (on) classes.add(name); else classes.delete(name);
        return on;
      },
    };
  }
  set innerHTML(value) { throw new Error('Unsafe innerHTML assignment'); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.append(child); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, handler) { this.handlers[type] = handler; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const nodes = {};
const node = id => nodes[id] || (nodes[id] = new Element());
const attack = '<img src=x onerror=alert(1)>';
const payload = {
  questions: [
    {question: attack, count: 4},
    {question: 'What is INFO 202?', count: 1},
  ],
};
const sandbox = {
  console, Headers, setTimeout, clearTimeout,
  window: {setTimeout, clearTimeout},
  document: {
    getElementById: node,
    querySelectorAll: () => [],
    createElement: tag => new Element(tag),
  },
  fetch: async path => ({
    ok: true,
    status: 200,
    headers: {get: () => 'application/json'},
    json: async () => {
      sandbox.lastPath = String(path);
      if (String(path).includes('popular-questions')) return {days: 30, questions: payload.questions};
      if (String(path).includes('issue-reports')) return {reports: [], summary: {total: 0, reasons: []}};
      return {authenticated: false, token_required: false};
    },
  }),
  lastPath: '',
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
(async () => {
  await new Promise(resolve => setTimeout(resolve, 0));
  await vm.runInContext('state.reviewer = "Reviewer"; state.popularWindowDays = 30; loadPopularQuestions()', sandbox);
  const list = node('popular-questions-list');
  const status = node('popular-questions-status');
  assert.strictEqual(list.children.length, 2);
  assert.strictEqual(list.children[0].children[0].textContent, attack);
  assert.strictEqual(list.children[0].children[1].textContent, '4 asks');
  assert.strictEqual(list.children[1].children[1].textContent, '1 ask');
  assert.strictEqual(status.textContent, '2 questions, 5 asks in the last 30 days.');
  assert(sandbox.lastPath.includes('days=30'));
  await vm.runInContext('selectPopularWindow(7)', sandbox);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert(sandbox.lastPath.includes('days=7'));
  payload.questions = [];
  await vm.runInContext('loadPopularQuestions()', sandbox);
  assert.strictEqual(status.textContent, 'No questions in the last 7 days.');
  assert.strictEqual(list.children.length, 0);
  node('popular-questions').classList.remove('hidden');
  await vm.runInContext('signOut()', sandbox);
  assert(node('popular-questions').classList.contains('hidden'));
  assert.strictEqual(list.children.length, 0);
  assert.strictEqual(status.textContent, '');
  console.log('ok');
})().catch(error => { console.error(error); process.exit(1); });
"""


@pytest.mark.parametrize("days", ["%C2%B2", "%D9%A7", "%EF%BC%97", "-7", "7.0"])
def test_popular_days_rejects_non_ascii_digits_with_400(monkeypatch, days):
    """Unicode digits pass str.isdigit() but crash int(); they must be a 400."""
    monkeypatch.setenv("POPULAR_QUESTIONS_PUBLIC", "1")
    response = app_module.app.test_client().get(f"/api/popular-questions?days={days}")
    assert response.status_code == 400
    assert response.get_json() == {"error": "days must be 7, 30, or 90"}
