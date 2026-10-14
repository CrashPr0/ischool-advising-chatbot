"""Offline guards for the client-only advisor email draft."""

from pathlib import Path
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
    assert not re.search(r"\b(?:fetch|XMLHttpRequest|localStorage|sessionStorage)\b", HANDOFF)
    assert not re.search(r"\b(?:provider|model|citations|timestamp)\b", HANDOFF)
    assert "window.location.href = buildAdvisorMailto" in HANDOFF
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
        for asset in ("script.js", "style.css"):
            assert re.search(rf"{re.escape(asset)}[^\n<>]*20261014-advisor-handoff", html)
    assert 'if (className === "bot-message") attachAdvisorHandoff(wrapper, text);' in SCRIPT
    assert 'button.addEventListener("click", openAdvisorHandoff)' in HANDOFF
    assert "handoffEligible: prefilledMessage === null" in SCRIPT
    assert 'handoffEligible: message.role === "user" && message.handoffEligible === true' in SCRIPT
    assert "const ADVISOR_HANDOFF_PHRASES =" in HANDOFF
    for phrase in ("i don't know", "i don't have", "not sure", "contact your advisor",
                   "advisor", "advising office", "spartan connect"):
        assert f'"{phrase}"' in HANDOFF
    css = (ROOT / "static/style.css").read_text(encoding="utf-8")
    assert ".advisor-handoff-button:focus-visible" in css
    assert re.search(r"\.advisor-handoff-button\s*\{[^}]*min-height: 44px", css)


@pytest.mark.skipif(shutil.which("node") is None, reason="Optional JavaScript checks need Node")
def test_builder_and_conversation_selection_in_node(tmp_path):
    runner = tmp_path / "advisor_handoff.js"
    runner.write_text(HANDOFF + r'''
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
for (const reply of ['I don’t know.', "I don't have that information", 'NOT SURE',
  'Contact your advisor', 'An advisor can help', 'The advising office', 'Use Spartan Connect']) {
  assert(shouldOfferAdvisorHandoff(reply), reply);
}
assert(!shouldOfferAdvisorHandoff('Here are the required courses.'));
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
assert(bodyOf(window.location.href).includes('First conversation'));
active = {program: 'mlis', messages: [own('Second conversation')]};
programSelect.value = 'mlis';
openAdvisorHandoff();
assert(window.location.href.startsWith('mailto:?'));
assert(bodyOf(window.location.href).includes('Second conversation'));
assert(!bodyOf(window.location.href).includes('First conversation'));
active.messages = [];
openAdvisorHandoff();
assert(!bodyOf(window.location.href).includes('Second conversation'));
tabbed = false;
openAdvisorHandoff();
assert(bodyOf(window.location.href).includes('Fallback conversation'));
''', encoding="utf-8")
    completed = subprocess.run([shutil.which("node"), str(runner)],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr + completed.stdout
