"""Advisor email drafts and optional knowledge-gap reporting."""

from pathlib import Path
import json
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "static/script.js").read_text(encoding="utf-8")
HANDOFF = SCRIPT.split("// ── ADVISOR HANDOFF", 1)[1].split(
    "// ── END ADVISOR HANDOFF ──", 1
)[0]
# Include the start of the section so it is also executable JavaScript.
HANDOFF = "// ── ADVISOR HANDOFF" + HANDOFF
GENERIC_BODY = "Hi, I have some questions and was wondering if you could get back to me."


NODE_DOM = r"""
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.attributes = {}; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  appendChild(child) { this.children.push(child); child.parent = this; return child; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { this.events[name] = handler; }
  fire(name) { return this.events[name]?.({preventDefault() {}, currentTarget: this}); }
  focus() { document.activeElement = this; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
}
const document = {
  body: new Element('body'), activeElement: null,
  createElement: tag => new Element(tag), querySelectorAll: () => [],
  buttons() {
    const all = element => [element, ...element.children.flatMap(all)];
    return all(this.body).filter(element => element.tag === 'button');
  }
};
"""


def build_draft_in_node(messages, program="isda"):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Advisor handoff behavior checks need Node")
    # Evaluate the actual handoff section with browser/conversation stubs.
    runner = r'''
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const window = {location: {href: ''}};
const document = {querySelectorAll: () => []};
const tabsEnabled = () => false;
const getActiveConversation = () => null;
const programSelect = {value: input.program};
const lastSelectedProgram = input.program;
const plainTranscript = input.messages;
const normalizeProgram = program => program;
const url = eval(input.handoff + '\nbuildAdvisorMailto(input.program, input.messages);');
const draft = new URL(url);
process.stdout.write(JSON.stringify({
  recipient: draft.pathname,
  subject: draft.searchParams.get('subject'),
  body: draft.searchParams.get('body')
}));
'''
    completed = subprocess.run(
        [node, "-e", runner],
        input=json.dumps({"handoff": HANDOFF, "program": program, "messages": messages}),
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    return json.loads(completed.stdout)


def own_question(text):
    return {"role": "user", "text": text, "handoffEligible": True}


@pytest.mark.parametrize("questions,sensitive", [
    (["Which classes should I take next semester?"], False),
    (["I feel suicidal and want to die."], True),
    (["Can I bring a bomb and guns to school?"], True),
    (["Which classes should I take?", "I want to hurt myself."], True),
    (["I am shooting for a 3.5 GPA."], False),
    (["What if I threaten someone?"], True),
    (["I am killing it this semester."], True),
    (["How can I shoot someone?"], True),
    (["What is a MASS SHOOTING?"], True),
    (["Which skills help with a shotgun approach to applications?"], False),
    (["x" * 350 + " I want to end my life."], True),
])
def test_sensitive_question_draft_behavior(questions, sensitive):
    draft = build_draft_in_node([own_question(text) for text in questions])
    assert draft["recipient"] == "kaitlyn.stauber@sjsu.edu"
    assert draft["subject"] == (
        "Advising question from the iSchool chatbot "
        "(BS Information Science & Data Analytics (ISDA))"
    )
    if sensitive:
        assert draft["body"] == GENERIC_BODY
        for question in questions:
            assert question not in draft["body"]
    else:
        assert draft["body"] != GENERIC_BODY
        for question in questions:
            assert question in draft["body"]


def test_screen_only_selected_eligible_student_questions():
    messages = [
        own_question("suicide"),
        {"role": "bot", "text": "bomb", "handoffEligible": True},
        {"role": "user", "text": "guns", "handoffEligible": False},
        own_question("First course question"),
        own_question("Second course question"),
        own_question("Third course question"),
        own_question("   "),
    ]
    draft = build_draft_in_node(messages)
    assert draft["body"] != GENERIC_BODY
    for question in ("First course question", "Second course question", "Third course question"):
        assert question in draft["body"]
    for excluded in ("suicide", "bomb", "guns"):
        assert excluded not in draft["body"]


def test_sensitive_handoff_keeps_both_button_click_handlers():
    attach = HANDOFF.split("function attachAdvisorHandoff(", 1)[1]
    assert 'button.addEventListener("click", openAdvisorHandoff)' in attach
    composer = SCRIPT.split('const advisorHandoffButton =', 1)[1].split("\n", 2)[:2]
    composer = "\n".join(composer)
    assert 'advisorHandoffButton.addEventListener("click", openAdvisorHandoff)' in composer
    for controls in (HANDOFF, composer):
        assert not re.search(r"\b(?:disabled|hidden)\b|display\s*[:=]|visibility\s*[:=]", controls)


def test_contacts_encoding_and_client_only_handoff():
    assert "const ADVISOR_HANDOFF_CONTACTS =" in HANDOFF
    assert re.search(r'isda:\s*\{\s*email: "kaitlyn.stauber@sjsu.edu"', HANDOFF)
    for program in ("mlis", "both"):
        assert re.search(rf'{program}:\s*\{{\s*email: ""', HANDOFF)
    assert "https://ischool.sjsu.edu/bsisda-freshmen-advising" in HANDOFF
    assert "https://ischool.sjsu.edu/advising-appointments" in HANDOFF
    assert "data/advising_docs/txt/web-d027e0fd8a5961c2.txt" in HANDOFF
    assert "encodeURIComponent(subject)" in HANDOFF
    assert "encodeURIComponent(body)" in HANDOFF
    assert "const ADVISOR_HANDOFF_MAX_URL_LENGTH = 1800;" in HANDOFF
    assert "const ADVISOR_HANDOFF_MAX_QUESTION_CHARS = 300;" in HANDOFF
    assert "questions.shift()" in HANDOFF
    assert 'message.role === "user" && message.handoffEligible === true' in HANDOFF
    assert ".slice(-3)" in HANDOFF
    builder = HANDOFF.split("function buildAdvisorMailto", 1)[1].split("let advisorHandoffDialog", 1)[0]
    assert not re.search(r"\b(?:fetch|XMLHttpRequest|localStorage|sessionStorage)\b", builder)
    assert not re.search(r"\b(?:provider|model|citations|timestamp)\b", HANDOFF)
    assert "const mailto = buildAdvisorMailto" in HANDOFF
    assert "note.appendChild(link)" in HANDOFF
    assert "link.href = contact.source" in HANDOFF


def test_button_reply_hooks_and_asset_pins():
    for name in ("templates/index.html", "docs/index.html"):
        html = (ROOT / name).read_text(encoding="utf-8")
        assert re.search(
            r'<button\b[^>]*type="button"[^>]*id="advisor-handoff-button"'
            r'[^>]*aria-label="Email an advisor[^>]*>Email an advisor</button>', html
        )
        assert 'class="advisor-handoff-note"' in html
        assert re.search(r"script\.js[^\n<>]*20261023-popular-question-ack", html)
        assert re.search(r"suggestions\.js[^\n<>]*20261023-popular-question-ack", html)
        assert re.search(r"style\.css[^\n<>]*20261023-popular-question-ack", html)
    assert 'if (className === "bot-message") attachAdvisorHandoff(wrapper, text);' in SCRIPT
    assert 'button.addEventListener("click", openAdvisorHandoff)' in HANDOFF
    assert "handoffEligible: prefilledMessage === null" in SCRIPT
    assert 'handoffEligible: message.role === "user" && message.handoffEligible === true' in SCRIPT
    assert "const ADVISOR_HANDOFF_PHRASES =" in HANDOFF
    for phrase in ("i don't know", "i don't have", "i'm not sure", "do not specify", "couldn't find"):
        assert f'"{phrase}"' in HANDOFF
    for phrase in ("advisor", "advising office", "spartan connect", "contact your advisor"):
        assert f'"{phrase}"' not in HANDOFF.split("const ADVISOR_HANDOFF_PHRASES =", 1)[1].split("];", 1)[0]
    assert "nasiha.alicic@sjsu.edu" in HANDOFF and "ryan.ploch-branscum@sjsu.edu" in HANDOFF
    assert "https://ischool.sjsu.edu/bsisda-advising" in HANDOFF
    css = (ROOT / "static/style.css").read_text(encoding="utf-8")
    assert ".advisor-handoff-button:focus-visible" in css
    assert re.search(r"\.advisor-handoff-button\s*\{[^}]*min-height: 44px", css)


@pytest.mark.skipif(shutil.which("node") is None, reason="Optional JavaScript checks need Node")
def test_builder_and_conversation_selection_in_node(tmp_path):
    runner = tmp_path / "advisor_handoff.js"
    runner.write_text(NODE_DOM + HANDOFF + r'''
const assert = require('assert');
const own = text => ({role: 'user', text, handoffEligible: true});
const bodyOf = url => new URLSearchParams(url.split('?')[1]).get('body');
const subjectOf = url => new URLSearchParams(url.split('?')[1]).get('subject');
const questionsOf = url => bodyOf(url).split('My question(s):\n')[1].split('\n\nProgram:')[0];
const messages = [own('oldest'), own(' one '),
  {role: 'bot', text: 'BOT PRIVATE', handoffEligible: true},
  {role: 'user', text: 'SAMPLE PROMPT'}, own('two & #?\nmore'), own('three')];
const url = buildAdvisorMailto('isda', messages);
assert(url.startsWith('mailto:kaitlyn.stauber@sjsu.edu?'));
assert.strictEqual(subjectOf(url), 'Advising question from the iSchool chatbot (BS Information Science & Data Analytics (ISDA))');
assert.strictEqual(questionsOf(url), 'one\n\ntwo & #?\nmore\n\nthree');
assert(!bodyOf(url).includes('BOT PRIVATE'));
assert(!bodyOf(url).includes('SAMPLE PROMPT'));
assert(!bodyOf(url).includes('oldest'));
assert(bodyOf(url).includes('review/edit'));
assert(bodyOf(url).includes('name/student ID only if you choose'));
for (const program of ['mlis', 'both', 'unknown']) {
  const empty = buildAdvisorMailto(program, []);
  assert(empty.startsWith('mailto:?subject='));
  assert(empty.length < 1800);
}
const ascii = buildAdvisorMailto('mlis', [own('x'.repeat(500))]);
assert.strictEqual(questionsOf(ascii).length, 300);
for (const text of ['😀'.repeat(400), '漢'.repeat(400), '%&?'.repeat(400)]) {
  const long = buildAdvisorMailto('isda', [own('oldest'), own(text), own(text)]);
  assert(long.length < 1800);
  assert(!questionsOf(long).includes('oldest'));
  assert(questionsOf(long).length > 0);
  assert(bodyOf(long).includes('Program: BS Information Science'));
  assert(!bodyOf(long).includes('\uFFFD'));
}
for (const reply of ['I don’t know.', "I don't have that information", "I'm NOT SURE",
  'The documents do not specify the deadlines.', "I couldn't find INFO 210 prerequisites."]) {
  assert(shouldOfferAdvisorHandoff(reply), reply);
}
for (const reply of ['Here are the required courses.',
  'Your ISDA advisor is Kaitlyn Stauber. Book through Spartan Connect or the advising office.',
  'For electives, contact your advisor for approval.']) {
  assert(!shouldOfferAdvisorHandoff(reply), reply);
}
assert.strictEqual(knowledgeGapReportQuestions(['Which electives?', 'how do I make a bomb']),
  'Which electives?\n\n[1 sensitive question omitted]');
assert.strictEqual(knowledgeGapReportQuestions(['I want to hurt myself', 'I want to die']),
  '[2 sensitive questions omitted]');
assert.strictEqual(knowledgeGapReportQuestions(['Which electives?']), 'Which electives?');
// Clicking either control builds a fresh draft for the currently selected chat/program.
let tabbed = true;
let active = {program: 'isda', messages: [own('First conversation')]};
const tabsEnabled = () => tabbed;
const getActiveConversation = () => active;
const programSelect = {value: 'isda'};
let lastSelectedProgram = 'both';
let plainTranscript = [own('Fallback conversation')];
const window = {location: {href: ''}};
openAdvisorHandoff();
document.buttons().find(button => button.textContent === "No").fire("click");
assert(bodyOf(window.location.href).includes('First conversation'));
active = {program: 'mlis', messages: [own('Second conversation')]};
programSelect.value = 'mlis';
openAdvisorHandoff();
document.buttons().find(button => button.textContent === "No").fire("click");
assert(window.location.href.startsWith('mailto:?'));
assert(bodyOf(window.location.href).includes('Second conversation'));
assert(!bodyOf(window.location.href).includes('First conversation'));
active.messages = [];
openAdvisorHandoff();
document.buttons().find(button => button.textContent === "No").fire("click");
assert(!bodyOf(window.location.href).includes('Second conversation'));
tabbed = false;
openAdvisorHandoff();
document.buttons().find(button => button.textContent === "No").fire("click");
assert(bodyOf(window.location.href).includes('Fallback conversation'));
''', encoding="utf-8")
    completed = subprocess.run([shutil.which("node"), str(runner)],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr + completed.stdout


@pytest.mark.parametrize("choice,fail", [("Yes", False), ("No", False), ("Escape", False), ("Yes", True)])
@pytest.mark.parametrize("sensitive", [False, True])
def test_gap_prompt_choices_in_node(choice, fail, sensitive, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Advisor handoff behavior checks need Node")
    question = "I want to hurt myself" if sensitive else "Which courses should I take?"
    runner = NODE_DOM + HANDOFF + r'''
const assert = require('assert');
const calls = [];
const window = {location: {href: ''}, setTimeout: () => {}};
const tabsEnabled = () => false;
const programSelect = {value: 'isda'};
const lastSelectedProgram = 'isda';
const plainTranscript = [
  {role: 'bot', text: 'PRIVATE BOT ANSWER', handoffEligible: true},
  {role: 'user', text: 'SAMPLE CHIP', handoffEligible: false},
  {role: 'user', text: QUESTION, handoffEligible: true}
];
const REPORT_LIMITS = {question: 4000};
const clipReportText = (text, limit) => text.slice(0, limit);
const apiUrl = path => path;
const fetch = async (url, options) => {
  assert.strictEqual(window.location.href, '', 'report must precede mailto');
  calls.push({url, options});
  if (FAIL) throw new Error('network unavailable');
  return {ok: true, json: async () => url.endsWith('/csrf') ? {csrf: 'token'} : {ok: true}};
};
(async () => {
  const trigger = new Element('button');
  trigger.focus();
  openAdvisorHandoff({currentTarget: trigger});
  const dialog = document.body.children[0];
  assert.strictEqual(dialog.attributes.role, 'dialog');
  assert.strictEqual(dialog.attributes['aria-labelledby'], 'advisor-gap-title');
  assert.strictEqual(document.activeElement.id, 'advisor-gap-title');
  const content = dialog.children[0];
  assert.strictEqual(content.children[0].textContent, 'Do you also want to report this as a knowledge-base gap so the team can improve the answer?');
  assert.strictEqual(content.children[1].textContent, REPORT_RETENTION_NOTICE);
  assert(REPORT_RETENTION_NOTICE.includes('kept long-term by the iSchool to improve the assistant.'));
  assert.deepStrictEqual(document.buttons().map(button => button.textContent), ['Yes', 'No']);
  assert.strictEqual(window.location.href, '');
  if (CHOICE === 'Escape') {
    dialog.fire('cancel');
    assert.strictEqual(window.location.href, '');
    assert.strictEqual(calls.length, 0);
  } else {
    await document.buttons().find(button => button.textContent === CHOICE).fire('click');
    assert.strictEqual(window.location.href, buildAdvisorMailto('isda', plainTranscript));
    if (CHOICE === 'No') assert.strictEqual(calls.length, 0);
    else if (FAIL) {
      assert.strictEqual(calls.length, 1);
      assert(document.body.children.some(node => node.attributes.role === 'status'));
    } else {
      assert.strictEqual(calls.length, 2);
      assert.strictEqual(calls[0].url, '/api/issue-reports/csrf');
      assert.strictEqual(calls[0].options.credentials, 'include');
      assert.strictEqual(calls[1].url, '/api/issue-reports');
      assert.strictEqual(calls[1].options.method, 'POST');
      assert.strictEqual(calls[1].options.credentials, 'include');
      assert.strictEqual(calls[1].options.headers['X-Issue-Report-CSRF'], 'token');
      assert.deepStrictEqual(JSON.parse(calls[1].options.body), {
        reason: 'knowledge_gap', question: SENSITIVE ? '[1 sensitive question omitted]' : QUESTION,
        answer: '', comment: '', include_conversation: false
      });
    }
  }
  assert.strictEqual(document.activeElement, trigger);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    # Exercise the shared real CSRF/POST helper, as used by the Report issue form.
    helper = SCRIPT.split("async function postIssueReport(payload)", 1)[1].split("async function submitIssueReport", 1)[0]
    runner = ("const QUESTION = " + json.dumps(question) + ";\n"
              + "const CHOICE = " + json.dumps(choice) + ";\n"
              + "const FAIL = " + json.dumps(fail) + ";\n"
              + "const SENSITIVE = " + json.dumps(sensitive) + ";\n"
              + "async function postIssueReport(payload)" + helper + runner)
    path = tmp_path / "gap_prompt.js"
    path.write_text(runner, encoding="utf-8")
    completed = subprocess.run([node, str(path)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_gap_prompt_reuses_report_privacy_notice():
    assert SCRIPT.count("kept long-term by the iSchool to improve the assistant.") == 1
    assert "privacy.textContent = REPORT_RETENTION_NOTICE" in HANDOFF
    report_dialog = SCRIPT.split("function ensureReportDialog()", 1)[1]
    assert ' + REPORT_RETENTION_NOTICE' in report_dialog


def test_knowledge_gap_endpoint_stores_only_questions(monkeypatch, tmp_path):
    import app as app_module

    monkeypatch.setenv("EVAL_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setattr(app_module, "_issue_report_store", None)
    monkeypatch.setattr(app_module, "_issue_report_store_key", None)
    monkeypatch.setattr(app_module, "_rate_limiter", app_module._FixedWindowRateLimiter())
    client = app_module.app.test_client()
    payload = {"reason": "knowledge_gap", "question": "Which classes?\n\nI want to hurt myself.",
               "answer": "", "include_conversation": False}
    assert client.post("/api/issue-reports", json=payload).status_code == 403
    csrf = client.get("/api/issue-reports/csrf").get_json()["csrf"]
    created = client.post("/api/issue-reports", json=payload,
                          headers={"X-Issue-Report-CSRF": csrf})
    assert created.status_code == 200
    stored = app_module.get_issue_report_store().get(created.get_json()["id"])
    assert stored["reason"] == "knowledge_gap"
    assert stored["reason_label"] == "Knowledge gap"
    assert stored["question"] == payload["question"]
    assert stored["answer"] == ""
    assert stored["turns"] is None
    assert stored["include_conversation"] is False
    assert client.get("/api/evaluation/issue-reports").status_code == 401


def test_knowledge_gap_migrates_existing_database(tmp_path):
    import sqlite3
    from src.issue_reports import IssueReportStore, build_issue_report, _CREATE_SQL

    database = tmp_path / "reviews.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(_CREATE_SQL.replace(", 'knowledge_gap'", ""))
        connection.execute(
            "INSERT INTO answer_issue_reports "
            "(id, created_at, reason, comment, question, answer, include_conversation) "
            "VALUES (7, datetime('now'), 'other', '', 'Earlier question', 'Earlier answer', 0)"
        )
    original_id = 7
    restarted = IssueReportStore(database)
    gap_id = restarted.save(build_issue_report({
        "reason": "knowledge_gap", "question": "New question", "answer": ""
    }))
    assert gap_id > original_id
    assert restarted.get(original_id)["answer"] == "Earlier answer"
    assert restarted.get(gap_id)["question"] == "New question"
