"""Browser-script guards that do not need a model or a running server."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Loads static/script.js against a tiny DOM. The docs page has no voice or
# topic nodes; the main page does, including the composer microphone.
_PAGE_RUNNER = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");

process.on("unhandledRejection", (error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});

function makeElement() {
  const classes = new Set();
  return {
    classes,
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      toggle(name, force) {
        if (force === undefined) {
          if (classes.has(name)) classes.delete(name);
          else classes.add(name);
        } else if (force) classes.add(name);
        else classes.delete(name);
      },
      contains(name) { return classes.has(name); },
    },
    handlers: {},
    addEventListener(type, fn) {
      if (!this.handlers[type]) this.handlers[type] = [];
      this.handlers[type].push(fn);
    },
    setAttribute() {},
    appendChild() {},
    remove() {},
    focus() {},
    textContent: "",
    innerHTML: "",
    value: "",
    disabled: false,
    title: "",
    checked: false,
    dataset: {},
  };
}

function runPage(ids, voiceConfig) {
  const elements = {};
  ids.forEach((id) => {
    elements[id] = makeElement();
  });
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    document: {
      getElementById(id) { return elements[id] || null; },
      querySelectorAll() { return []; },
      createElement() { return makeElement(); },
    },
    fetch: async () => ({
      ok: voiceConfig !== null,
      json: async () => voiceConfig || {},
    }),
    window: {},
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return new Promise((resolve) => {
    setTimeout(() => resolve(elements), 0);
  });
}

const docsIds = [
  "chat-box", "user-input", "send-button", "clear-button",
  "status-text", "provider-text", "source-badge", "docs-badge",
];
const mainIds = docsIds.concat([
  "active-topic-banner", "active-topic-label", "sample-chips-area",
  "sample-chips-label", "sample-chips", "voice-tools", "voice-listen-button",
  "voice-listen-label", "voice-language-select", "voice-reply-toggle",
  "voice-stop-button", "voice-status", "voice-support-badge",
]);

(async () => {
  const docs = await runPage(docsIds, null);
  const clearFns = docs["clear-button"].handlers.click || [];
  if (clearFns.length !== 1) throw new Error("clear button listener missing");
  await clearFns[0]();
  if (!String(docs["chat-box"].innerHTML).includes("ISDA advising assistant")) {
    throw new Error("clear did not reset the chat box");
  }

  const disabled = await runPage(mainIds, { enabled: false, mode: "disabled" });
  const enabled = await runPage(mainIds, null);
  console.log(JSON.stringify({
    docsClearOk: true,
    disabledButton: disabled["voice-listen-button"].disabled,
    disabledTitle: disabled["voice-listen-button"].title,
    disabledHidden: disabled["voice-tools"].classes.has("hidden"),
    disabledStatus: disabled["voice-status"].textContent,
    enabledTitle: enabled["voice-listen-button"].title,
    enabledHidden: enabled["voice-tools"].classes.has("hidden"),
    enabledStatus: enabled["voice-status"].textContent,
    enabledBadge: enabled["voice-support-badge"].textContent,
  }));
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""


def test_confidence_display_is_gone_and_link_rendering_is_unchanged():
    script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    assert "confidenceBadge" not in script
    assert "info.confidence" not in script
    assert "confidence: data.confidence" not in script
    assert 'id="confidence-badge"' not in template
    assert 'id="confidence-badge"' not in docs
    assert "filename='style.css', v='20261009-report-retention'" in template
    assert "style.css?v=20261009-report-retention" in docs
    assert "filename='script.js', v='20261009-report-retention'" in template
    assert "script.js?v=20261009-report-retention" in docs
    assert "20261001-reveal-tabs" not in template
    assert "20260927-tabs" not in docs
    assert "20261002-letter-reveal" not in template
    assert "20261002-letter-reveal" not in docs
    assert "Report issue" in script
    assert "Include this conversation so the team can review it" in script
    assert (
        "Only the reason, your comment, and this answer are saved unless you include the conversation."
        in script
    )
    assert (
        "Reports, including any conversation you share, are automatically deleted after 90 days."
        in script
    )
    assert "include_conversation" in script
    assert 'id="issue-reports"' in (ROOT / "templates" / "evaluation.html").read_text(
        encoding="utf-8"
    )
    assert "20260927-welcome" not in template
    assert "20260927-welcome" not in docs
    assert 'id="chat-tabs"' in template
    assert 'role="tablist"' in template
    assert "conversation_id" in script
    assert "localStorage" in script
    assert "filename='markdown.js', v='20260927-mailto'" in template
    assert "markdown.js?v=20260927-mailto" in docs
    assert "filename='script.js', v='20260927-markdown'" not in template
    assert "script.js?v=20260927-markdown" not in docs
    assert "filename='script.js', v='20260924-composer'" not in template
    assert "script.js?v=20260924-composer" not in docs
    # The disclaimer paragraph is owned by the grounding change. Leave it.
    assert "For individualized advising, please contact an advisor." in template
    assert "Questions you submit are sent to an AI provider" not in template
    markdown = (ROOT / "static" / "markdown.js").read_text(encoding="utf-8")
    assert "if (!['http:', 'https:', 'mailto:'].includes(parsed.protocol))" in markdown
    assert "container.textContent = text" in script
    assert "container.innerHTML = renderBotMarkdown(text)" in script
    assert "botMarkdownPlainText(responseText)" in script
    assert "if (!voiceTools) return;" in script
    assert "activeBanner?.classList.add" in script
    assert "silent" not in script


def test_index_welcome_chips_reuse_topic_samples():
    """The empty state offers real buttons whose questions already exist."""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    samples = []
    for payload in re.findall(r"data-samples='([^']+)'", template):
        samples.extend(json.loads(payload))

    starters = re.findall(
        r'class="starter-chip"[^>]*data-question="([^"]+)"',
        template,
    )
    assert 3 <= len(starters) <= 6
    assert len(starters) == len(set(starters))
    assert starters
    for question in starters:
        assert question in samples
        assert f">{question}</span>" in template

    assert 'id="empty-state"' in template
    assert 'role="group" aria-labelledby="starter-label"' in template
    assert template.count('class="starter-chip"') == len(starters)


def test_docs_page_voice_init_and_clear_do_not_throw(tmp_path):
    runner = tmp_path / "check_voice_guards.js"
    runner.write_text(_PAGE_RUNNER, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "script.js")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = json.loads(completed.stdout)
    assert report["docsClearOk"] is True
    assert report["disabledButton"] is True
    assert report["disabledHidden"] is True
    assert report["disabledTitle"] == "Voice input is disabled for this deployment"
    assert (
        report["disabledStatus"]
        == "Type your question to get started. Voice input is disabled."
    )
    assert report["enabledHidden"] is False
    assert (
        report["enabledTitle"]
        == "Speech recognition is unavailable in this browser"
    )
    assert report["enabledBadge"] == "Text-only browser"
    assert (
        report["enabledStatus"]
        == "This browser does not expose speech controls. You can still use text chat."
    )


_EVALUATION_RUNNER = r"""
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
    this._text = '';
    const classes = new Set();
    this.classList = {
      add: name => classes.add(name),
      remove: name => classes.delete(name),
      contains: name => classes.has(name),
      toggle: (name, force) => force ? classes.add(name) : classes.delete(name),
    };
  }
  set innerHTML(value) { throw new Error('Unsafe innerHTML assignment'); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  get lastChild() { return this.children[this.children.length - 1]; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.append(child); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, handler) { this.handlers[type] = handler; }
  showModal() {}
}
const nodes = {};
const node = id => nodes[id] || (nodes[id] = new Element());
const attack = '<img src=x onerror=alert(1)>';
let data = {
  reports: [
    {reason: 'other', reason_label: 'Other', comment: attack, question: attack, answer: attack,
     include_conversation: true, provider: attack, model: attack, conversation_id: attack,
     citations: [attack], turns: [{role: 'user', text: attack}]},
    {reason: 'made_up', reason_label: attack, comment: 'A visible comment', question: 'Q', answer: 'A'},
    {reason: 'incorrect_info', reason_label: 'Incorrect information', comment: '', question: 'Q', answer: 'A'},
  ],
  summary: {total: 205, reasons: [
    {key: 'other', label: 'Other', count: 200},
    {key: 'made_up', label: attack, count: 4},
    {key: 'incorrect_info', label: 'Incorrect information', count: 1},
    {key: 'outdated', label: 'Outdated information', count: 0},
  ]},
};
const sandbox = {
  console, Headers, setTimeout, clearTimeout,
  window: {setTimeout, clearTimeout},
  document: {getElementById: node, querySelectorAll: () => [], createElement: tag => new Element(tag)},
  fetch: async path => ({
    ok: path.includes('issue-reports'), status: 401,
    headers: {get: () => 'application/json'},
    json: async () => path.includes('issue-reports') ? data : {error: 'Sign in'},
  }),
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
(async () => {
  await new Promise(resolve => setTimeout(resolve, 0));
  await vm.runInContext('state.reviewer = "Reviewer"; loadIssueReports()', sandbox);
  const summary = node('issue-reports-summary');
  const filters = node('issue-reports-filters');
  const list = node('issue-reports-list');
  const status = node('issue-reports-status');
  assert.strictEqual(summary.children.length, 4);
  assert(summary.children[0].textContent.includes('Other200Most reported'));
  assert(summary.children[0].classList.contains('issue-reports-summary__reason--top'));
  assert.strictEqual(summary.children[1].children[0].textContent, attack);
  assert.strictEqual(summary.children[3].children[1].textContent, '0');
  assert.strictEqual(filters.children.length, 5);
  assert.strictEqual(filters.children[0].textContent, 'All');
  assert.strictEqual(filters.children[0].attributes['aria-pressed'], 'true');
  assert.strictEqual(status.textContent, 'Showing 3 of 205 reports');
  assert(node('issue-reports-limit').textContent.includes('3 newest loaded reports'));
  assert.strictEqual(list.children.length, 3);
  assert(list.children[0].textContent.includes("Student's description" + attack));
  assert(list.children[1].textContent.includes('CommentA visible comment'));
  assert(list.children[2].textContent.includes('Comment—'));
  filters.children[1].handlers.click();
  assert.strictEqual(list.children.length, 1);
  assert.strictEqual(status.textContent, 'Showing 1 of 205 reports');
  assert.strictEqual(filters.children[1].attributes['aria-pressed'], 'true');
  assert.strictEqual(filters.children[0].attributes['aria-pressed'], 'false');
  await vm.runInContext('loadIssueReports()', sandbox);
  assert.strictEqual(status.textContent, 'Showing 1 of 205 reports');
  filters.children[4].handlers.click();
  assert.strictEqual(status.textContent, 'Showing 0 of 205 reports');
  assert.strictEqual(list.textContent, 'No loaded reports for this reason.');
  filters.children[0].handlers.click();
  assert.strictEqual(list.children.length, 3);
  data = {reports: [], summary: {total: 0, reasons: data.summary.reasons.map(r => ({...r, count: 0}))}};
  await vm.runInContext('loadIssueReports()', sandbox);
  assert.strictEqual(status.textContent, 'Showing 0 of 0 reports');
  assert.strictEqual(list.textContent, 'No student reports yet.');
  assert(!summary.textContent.includes('Most reported'));
  assert.strictEqual(node('issue-reports-limit').textContent, '');
  console.log('ok');
})().catch(error => { console.error(error); process.exit(1); });
"""


def test_evaluation_report_summary_and_filters_render_safely(tmp_path):
    template = (ROOT / "templates" / "evaluation.html").read_text(encoding="utf-8")
    for filename in ["evaluation.js", "evaluation.css"]:
        assert f"filename='{filename}', v='20261010-report-breakdown'" in template
    assert 'role="status" aria-live="polite"' in template
    assert 'role="group" aria-label="Filter reports by reason"' in template
    runner = tmp_path / "check_evaluation_reports.js"
    runner.write_text(_EVALUATION_RUNNER, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "evaluation.js")],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert completed.stdout.strip() == "ok"
