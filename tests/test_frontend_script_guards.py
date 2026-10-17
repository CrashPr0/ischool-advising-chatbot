import base64
import hashlib
import io
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
  if (!String(docs["chat-box"].innerHTML).includes("BS ISDA and MLIS advising assistant")) {
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
    assert "filename='style.css', v='20261014-advisor-handoff'" in template
    assert "style.css?v=20261014-advisor-handoff" in docs
    assert "filename='script.js', v='20261017-long-term-reports'" in template
    assert "script.js?v=20261017-long-term-reports" in docs
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
        "Reports, including any shared conversation, are kept long-term by the iSchool to improve the assistant."
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


def test_chat_errors_surface_safe_messages_and_parse_json_defensively(tmp_path):
    script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    chat = script.split("async function sendMessage(", 1)[1].split("function reportContext(", 1)[0]
    assert 'typeof data.error === "string" && data.error.trim()' in chat
    assert 'typeof error.message === "string"' in chat
    assert "deliverBotReply(conversationId, safeMessage ? errorMessage : fallbackError)" in chat
    assert "Sorry, I couldn't get an answer right now. Please try again." in chat
    assert re.search(r"try\s*\{\s*data = .*?response\.json\(\).*?\}\s*catch", chat, re.S)

    runner = tmp_path / "check_chat_errors.js"
    runner.write_text(r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const chat = source.slice(source.indexOf("async function sendMessage("), source.indexOf("function reportContext("));
const fallback = "Sorry, I couldn't get an answer right now. Please try again.";

async function check({ body = {}, ok = false, parseError, fetchError, cancelled = false, expected = fallback }) {
  const replies = [];
  const operations = new Map();
  const noop = () => {};
  const sandbox = {
    AbortController, replyOperations: operations, activeReplyKey: () => "default",
    userInput: { value: "Question" }, conversationDrafts: new Map(),
    hideSampleChips: noop, updateMeta: noop, addMessage: noop,
    showTypingIndicator: noop, updateReplyControls: noop, renderTabs: noop,
    programSelect: null, lastSelectedProgram: "both", normalizeProgram: value => value,
    apiUrl: value => value, removeTypingIndicator: noop,
    deliverBotReply: (id, text) => replies.push(text),
    revealReply: (key, text) => replies.push(text),
    fetch: async () => {
      if (fetchError) throw fetchError;
      return { ok, json: async () => {
        if (cancelled) operations.clear();
        if (parseError) throw parseError;
        return body;
      } };
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(chat, sandbox);
  await vm.runInContext("sendMessage()", sandbox);
  const wanted = expected === null ? [] : [expected];
  if (JSON.stringify(replies) !== JSON.stringify(wanted)) {
    throw new Error("Expected " + JSON.stringify(wanted) + ", got " + JSON.stringify(replies));
  }
}

(async () => {
  for (const message of ["Too many requests. Please try again in a minute.", "The advising index is not ready. Please try again later."]) {
    await check({ body: { error: message }, expected: message });
  }
  for (const error of [null, 42, {}, "", "   ", "x".repeat(300),
      "TypeError: Failed to fetch", "ReferenceError: value is not defined",
      "SyntaxError: Unexpected token", "NetworkError: Request failed",
      "Error: internal failure", "Exception: internal failure",
      "Cannot read properties of undefined", "handler is not a function",
      "Error\n    at sendMessage (script.js:900)", "at sendMessage (script.js:900)"]) {
    await check({ body: { error } });
  }
  await check({ body: null });
  await check({ parseError: new SyntaxError("Unexpected token '<'") });
  await check({ parseError: Object.assign(new Error("Cancelled"), { name: "AbortError" }), expected: null });
  await check({ fetchError: new TypeError("Failed to fetch") });
  await check({ fetchError: Object.assign(new Error("Cancelled"), { name: "AbortError" }), expected: null });
  await check({ cancelled: true, body: { error: "Please try later." }, expected: null });
  await check({ ok: true, body: { response: "Successful answer" }, expected: "Successful answer" });
})().catch(error => { console.error(error); process.exit(1); });
""", encoding="utf-8")
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "script.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


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


_PROGRAM_RUNNER = r"""
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

function runPage(ids, voiceConfig, storage) {
  const elements = {};
  ids.forEach((id) => {
    elements[id] = makeElement();
  });
  const sandbox = {
    console,
    AbortController,
    localStorage: storage,
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
    setTimeout(() => resolve({ elements, sandbox }), 0);
  });
}


const assert = require("assert");
const values = new Map();
const storage = { getItem(key) { return values.get(key) || null; }, setItem(key, value) { values.set(key, value); } };
const ids = ["chat-box", "user-input", "send-button", "clear-button", "status-text", "provider-text", "source-badge", "docs-badge", "chat-tabs", "empty-state", "program-select"];
(async () => {
  const { elements, sandbox } = await runPage(ids, null, storage);
  const run = code => vm.runInContext(code, sandbox);
  assert.equal(elements["program-select"].value, "both");
  elements["program-select"].value = "mlis";
  elements["program-select"].handlers.change[0]();
  const first = run("activeConversationId");
  run('getActiveConversation().messages.push({ role: "user", text: "core courses" }); startNewConversation();');
  const second = run("activeConversationId");
  assert.notEqual(first, second);
  assert.equal(elements["program-select"].value, "mlis");
  elements["program-select"].value = "isda";
  elements["program-select"].handlers.change[0]();
  run('conversations[0].messages = [];');
  run(`selectConversation(${JSON.stringify(first)})`);
  assert.equal(elements["program-select"].value, "mlis");
  assert.equal(run("lastSelectedProgram"), "isda");
  const restored = await runPage(ids, null, storage);
  assert.equal(restored.elements["program-select"].value, "mlis");
  assert.equal(vm.runInContext("lastSelectedProgram", restored.sandbox), "isda");
  run("startNewConversation()");
  assert.equal(elements["program-select"].value, "isda");
  assert.equal(run("activeConversationId"), first);
  run(`selectConversation(${JSON.stringify(second)})`);
  assert.equal(elements["program-select"].value, "isda");
  run('getActiveConversation().messages.push({ role: "user", text: "courses" }); startNewConversation();');
  assert.equal(elements["program-select"].value, "isda");
  // Exercise the real request construction while isolating reply rendering.
  const sent = [];
  sandbox.fetch = async (url, options) => {
    if (url.endsWith("/api/chat")) sent.push(JSON.parse(options.body));
    return { ok: false, json: async () => ({ error: "test" }) };
  };
  run('deliverBotReply = () => {};');
  await run('sendMessage("core courses")');
  assert.equal(sent[0].program, "isda");
  assert.equal(sent[0].conversation_id, run("activeConversationId"));
  console.log("program state and request checks passed");
})().catch(error => { console.error(error.stack); process.exit(1); });
"""


def test_program_state_persists_per_tab_and_is_sent(tmp_path):
    runner = tmp_path / "check_program_state.js"
    runner.write_text(_PROGRAM_RUNNER, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "script.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_export_chat_download_and_print_fallback_without_network_requests(tmp_path):
    runner = _PAGE_RUNNER.split("(async () => {")[0]
    runner = runner.replace(
        "    appendChild() {},",
        """    children: [],
    appendChild(child) { child.parent = this; this.children.push(child); },
    append(...children) { children.forEach(child => this.appendChild(child)); },
    replaceChildren() { this.children = []; },""",
    )
    runner = runner.replace(
        "    remove() {},",
        """    remove() {
      if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);
    },""",
    )
    runner = runner.replace(
        "      getElementById(id)",
        """      title: "Original chatbot title",
      body: makeElement(),
      getElementById(id)""",
    )
    runner = runner.replace(
        "    window: {},",
        """    URL,
    window: {
      handlers: {},
      addEventListener(type, fn) { this.handlers[type] = fn; },
      print() { this.printCalls = (this.printCalls || 0) + 1; },
    },""",
    )
    runner = runner.replace(
        "  vm.runInContext(source, sandbox);",
        """  vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), sandbox);
  vm.runInContext(source, sandbox);""",
    )
    runner = runner.replace("resolve(elements)", "resolve({ elements, sandbox })")
    runner += r"""
(async () => {
  const { elements, sandbox } = await runPage(docsIds.concat([
    "export-chat-button", "export-chat-status", "chat-tabs", "empty-state"
  ]), null);
  const button = elements["export-chat-button"];
  const status = elements["export-chat-status"];
  const click = button.handlers.click[0];
  if (!button.disabled) throw new Error("Empty conversation can be exported");
  await click();
  if (sandbox.window.printCalls) throw new Error("Empty conversation opened print dialog");
  vm.runInContext(`rememberMessage(activeConversationId, { role: "user", text: "Question <img onerror=bad>" });`, sandbox);
  if (!button.disabled) throw new Error("Question alone enables export");
  vm.runInContext(`rememberMessage(activeConversationId, {
    role: "bot", text: "Answer **with emphasis** [Citation](https://ischool.sjsu.edu/) <img onerror=bad>"
  });`, sandbox);
  if (button.disabled) throw new Error("Answer did not enable export");
  let requests = 0;
  sandbox.fetch = async () => { requests++; throw new Error("Export made a request"); };
  await click();
  if (sandbox.window.printCalls !== 1) throw new Error("Export did not call window.print");
  if (requests) throw new Error("Export fetched data");
  const view = sandbox.document.body.children.find(child => child.id === "chat-print-view");
  if (!view) throw new Error("Print view missing");
  const texts = node => [node.textContent, ...node.children.flatMap(texts)];
  const text = texts(view).join("\n");
  for (const expected of ["SJSU iSchool Advising Chatbot - conversation", "Exported:",
      "You", "Advising assistant", "Question <img onerror=bad>",
      "This copy was created on your device. The advising chatbot does not keep it."]) {
    if (!text.includes(expected)) throw new Error("Missing print text: " + expected);
  }
  if (!text.includes("Program: Not sure / both")) throw new Error("Default program header missing");
  const question = view.children[3].children[1];
  if (question.innerHTML) throw new Error("Question inserted as HTML");
  const answer = view.children[4].children[1].innerHTML;
  if (!answer.includes("Answer") || !answer.includes("<strong>with emphasis</strong>") ||
      !answer.includes('href="https://ischool.sjsu.edu/"') || !answer.includes("Citation")) {
    throw new Error("Answer formatting or citation lost: " + answer);
  }
  if (answer.includes("<img")) throw new Error("Unsanitized answer HTML");
  if (!/^SJSU-advising-chat-\d{4}-\d{2}-\d{2}$/.test(sandbox.document.title)) {
    throw new Error("Missing PDF filename title");
  }
  if (status.textContent !== "Choose “Save as PDF” in the print dialog to keep a copy.") {
    throw new Error("Missing export status");
  }
  sandbox.window.handlers.afterprint();
  if (sandbox.document.body.children.includes(view)) throw new Error("Print view was not removed");
  if (sandbox.document.title !== "Original chatbot title") throw new Error("Title was not restored");
  // New chat and tab switching must refresh the enabled state.
  vm.runInContext("startNewConversation()", sandbox);
  if (!button.disabled || status.textContent) throw new Error("New chat did not reset export");
  vm.runInContext("selectConversation(conversations[0].id)", sandbox);
  if (button.disabled) throw new Error("Switching to answered chat did not enable export");
  await click();
  if (sandbox.window.printCalls !== 2) throw new Error("Repeat export failed");
  sandbox.window.handlers.afterprint();
  vm.runInContext("selectConversation(conversations[1].id)", sandbox);
  if (!button.disabled) throw new Error("Switching to empty chat did not disable export");
  vm.runInContext("selectConversation(conversations[0].id)", sandbox);
  vm.runInContext('getActiveConversation().program = "mlis"', sandbox);
  // Both constructor and save failures must open the existing print fallback.
  sandbox.window.jspdf = { jsPDF: class { constructor() { throw new Error("broken PDF"); } } };
  await click();
  if (sandbox.window.printCalls !== 3) throw new Error("Constructor failure did not fall back");
  const programView = sandbox.document.body.children.find(child => child.id === "chat-print-view");
  if (!texts(programView).includes("Program: MLIS")) throw new Error("Print program missing");
  sandbox.window.handlers.afterprint();
  // Load the UMD browser global with eval disabled, as required by the CSP.
  const browser = {
    navigator: { userAgent: "test" }, document: { createElement: () => ({}) },
    atob, btoa, TextEncoder, TextDecoder,
  };
  browser.window = browser;
  vm.createContext(browser, { codeGeneration: { strings: false, wasm: false } });
  vm.runInContext(fs.readFileSync(process.argv[4], "utf8"), browser);
  const { jsPDF } = browser.jspdf;
  sandbox.window.jspdf = { jsPDF: function(options) {
    const pdf = new jsPDF(options);
    pdf.save = () => { throw new Error("save failed"); };
    return pdf;
  } };
  await click();
  if (sandbox.window.printCalls !== 4) throw new Error("Save failure did not fall back");
  sandbox.window.handlers.afterprint();
  let saved;
  sandbox.window.jspdf = { jsPDF: function(options) {
    const pdf = new jsPDF(options);
    const text = pdf.text.bind(pdf);
    pdf.text = (line, x, y) => {
      if (x < 48 || y < 48 || y > pdf.internal.pageSize.getHeight() - 48) {
        throw new Error("Text outside page margins");
      }
      if (pdf.getTextWidth(line) > pdf.internal.pageSize.getWidth() - 96 + 0.1) {
        throw new Error("Text was not wrapped");
      }
      return text(line, x, y);
    };
    pdf.save = filename => {
      saved = { filename, pdf: Buffer.from(pdf.output(), "binary").toString("base64") };
    };
    return pdf;
  } };
  vm.runInContext(`
    for (const [value, expected] of [["isda", "ISDA"], ["mlis", "MLIS"],
        ["both", "Not sure / both"], [undefined, null], [null, null],
        [42, null], [{}, null], ["unknown", null], ["toString", null]]) {
      if (chatExportProgramLabel({ program: value }) !== expected) throw new Error("Invalid program label");
    }
    if (chatExportProgramLabel(null) !== null || chatExportProgramLabel() !== null) {
      throw new Error("Missing chat caused a program error");
    }
    getActiveConversation().program = "both";
    const recordedAt = Date.UTC(2026, 9, 9, 12, 30);
    rememberMessage(activeConversationId, { role: "user", text: "Long question", timestamp: recordedAt });
    rememberMessage(activeConversationId, {
      role: "bot", text: "Wrapped paragraph ".repeat(2000) + "\\n" + "x".repeat(1000) +
        "\\nFinal answer [Citation](https://ischool.sjsu.edu/) [Email](mailto:advisor@sjsu.edu)",
      timestamp: recordedAt
    });
    const restored = normalizeConversation(getActiveConversation());
    if (restored.messages.at(-1).timestamp !== recordedAt) throw new Error("Timestamp not preserved");
    if (normalizeMessage({role: "bot", text: "old"}).timestamp !== null) throw new Error("Invented old timestamp");
    if (!Number.isFinite(restored.messages[0].timestamp)) throw new Error("New turn timestamp missing");
    // An old saved message must export without inventing its timestamp.
    getActiveConversation().messages[0].timestamp = null;
    const other = createConversation();
    conversations.push(other);
    rememberMessage(other.id, { role: "bot", text: "Other conversation secret" });
  `, sandbox);
  await click();
  if (!saved || !/^SJSU-advising-chat-\d{4}-\d{2}-\d{2}\.pdf$/.test(saved.filename)) {
    throw new Error("PDF download or filename missing");
  }
  if (sandbox.window.printCalls !== 4) throw new Error("Successful PDF opened print dialog");
  if (requests) throw new Error("PDF export fetched data");
  if (status.textContent !== "PDF downloaded.") throw new Error("PDF status missing");
  if (sandbox.document.title !== "Original chatbot title" || sandbox.document.body.children.length) {
    throw new Error("PDF export changed document or left print view");
  }
  saved.timestamp = vm.runInContext("new Date(recordedAt).toLocaleString()", sandbox);
  for (const outcome of ["success", "error", "missing", "cross-origin"]) {
    const page = await runPage(docsIds.concat([
      "export-chat-button", "export-chat-status", "chat-tabs", "empty-state"
    ]), null);
    const target = page.sandbox;
    target.window.location = { href: "https://example.test/", origin: "https://example.test" };
    target.document.head = makeElement();
    const exportButton = page.elements["export-chat-button"];
    exportButton.dataset.jspdfSrc = outcome === "cross-origin"
      ? "https://other.test/jspdf.js" : "/static/vendor/jspdf/jspdf.umd.min.js?v=4.2.1";
    vm.runInContext('rememberMessage(activeConversationId, { role: "bot", text: "Answer" })', target);
    if (target.document.head.children.length) throw new Error("Eager PDF library load");
    const exportClick = exportButton.handlers.click[0];
    const pending = exportClick();
    const repeated = exportClick();
    const scripts = target.document.head.children;
    if (outcome === "cross-origin") {
      if (scripts.length) throw new Error("Cross-origin script inserted");
    } else {
      if (scripts.length !== 1) throw new Error("Load promise not shared");
      if (scripts[0].src !== "https://example.test/static/vendor/jspdf/jspdf.umd.min.js?v=4.2.1") {
        throw new Error("Wrong library URL");
      }
      if (outcome === "success") {
        target.window.jspdf = { jsPDF: function(options) {
          const pdf = new jsPDF(options);
          pdf.save = () => {};
          return pdf;
        } };
        scripts[0].onload();
      } else if (outcome === "error") scripts[0].onerror();
      else scripts[0].onload();
    }
    await Promise.all([pending, repeated]);
    if (outcome === "success") {
      if (target.window.printCalls) throw new Error("Loaded PDF fell back to print");
    } else if (target.window.printCalls !== 2) throw new Error("Load failure did not print");
    if (target.document.body.children.length !== (outcome === "success" ? 0 : 1)) {
      throw new Error("Repeated export left stale print views");
    }
    await exportClick();
    if (scripts.length !== (outcome === "cross-origin" ? 0 : 1)) throw new Error("Library loaded again");
  }
  console.log(JSON.stringify(saved));
})().catch(error => { console.error(error.stack || error); process.exit(1); });
"""
    path = tmp_path / "check_export_chat.js"
    path.write_text(runner, encoding="utf-8")
    completed = subprocess.run(
        [
            "node",
            str(path),
            str(ROOT / "static" / "script.js"),
            str(ROOT / "static" / "markdown.js"),
            str(ROOT / "static" / "vendor" / "jspdf" / "jspdf.umd.min.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    from pypdf import PdfReader

    report = json.loads(completed.stdout)
    pdf = PdfReader(io.BytesIO(base64.b64decode(report["pdf"])))
    assert len(pdf.pages) > 2
    text = "\n".join(page.extract_text() for page in pdf.pages)
    for expected in (
        "SJSU iSchool Advising Chatbot - conversation",
        "Exported:",
        "Program: Not sure / both",
        "You - Timestamp unavailable",
        "Advising assistant -",
        "Long question",
        "Final answer",
        "Citation (https://ischool.sjsu.edu/)",
        "Email (mailto:advisor@sjsu.edu)",
        "with emphasis",
    ):
        assert expected in text
    assert "You - " + report["timestamp"] in text
    assert "Advising assistant - " + report["timestamp"] in text
    assert "Other conversation secret" not in text
    assert all(not page.get("/Annots") for page in pdf.pages)


def test_jspdf_vendor_license_and_local_template_reference():
    vendor = ROOT / "static" / "vendor" / "jspdf"
    build = (vendor / "jspdf.umd.min.js").read_bytes()
    assert b"Version 4.2.1" in build
    # Exact upstream build, including its bundled license notices.
    assert (
        hashlib.sha256(build).hexdigest()
        == "e6551fcdc32f09d6853b2c5126d18d01d9447e0da618a41a11ebeee0f6c20d54"
    )
    license_text = (vendor / "LICENSE").read_text()
    assert "Permission is hereby granted, free of charge" in license_text
    assert "James Hall" in license_text
    assert "4.2.1" in (vendor / "README.txt").read_text()
    template = (ROOT / "templates" / "index.html").read_text()
    reference = "filename='vendor/jspdf/jspdf.umd.min.js', v='4.2.1'"
    assert reference in template
    assert re.search(r'<button[^>]+data-jspdf-src="[^"\n]*' + re.escape(reference), template)
    assert not re.search(r'<script[^>]+jspdf', template, re.I)
    script = (ROOT / "static" / "script.js").read_text()
    assert "await loadChatPdfLibrary()" in script
    assert "if (!chatPdfLoadPromise)" in script
    assert 'document.createElement("script")' in script
    assert "url.origin !== window.location.origin" in script
    assert "convo?.program" in script
    assert 'typeof program !== "string"' in script
    assert not re.search(r'<script[^>]+src="https?://[^\"]*jspdf', template, re.I)


def test_export_chat_accessibility_and_print_styles():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    evaluation = (ROOT / "templates" / "evaluation.html").read_text(encoding="utf-8")
    assert re.search(r'<button id="export-chat-button"[^>]*type="button"[^>]*disabled>', template)
    assert "Download this conversation as a PDF" in template
    assert 'id="export-chat-status" class="export-chat-status" role="status" aria-live="polite"' in template
    assert "button.clear-btn:focus-visible" in styles
    assert "#chat-print-view { display: none; }" in styles
    assert "@media print" in styles
    assert "body > :not(#chat-print-view) { display: none !important; }" in styles
    assert "filename='evaluation.css'" in evaluation
    assert "filename='evaluation.js'" in evaluation
    assert 'id="saved-chats"' not in evaluation


def test_app_has_no_saved_chat_routes():
    import ast

    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    routes = [
        decorator.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "route"
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
        and isinstance(decorator.args[0].value, str)
    ]
    assert routes
    assert all("saved-chat" not in route for route in routes)
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
