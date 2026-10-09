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
    assert "filename='style.css', v='20261010-export-chat'" in template
    assert "style.css?v=20261010-export-chat" in docs
    assert "filename='script.js', v='20261010-export-chat'" in template
    assert "script.js?v=20261010-export-chat" in docs
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


def test_export_chat_prints_active_transcript_without_network_requests(tmp_path):
    runner = _PAGE_RUNNER.split("(async () => {")[0]
    runner = runner.replace('    appendChild() {},', '''    children: [],
    appendChild(child) { child.parent = this; this.children.push(child); },
    append(...children) { children.forEach(child => this.appendChild(child)); },
    replaceChildren() { this.children = []; },''')
    runner = runner.replace('    remove() {},', '''    remove() {
      if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);
    },''')
    runner = runner.replace('      getElementById(id)', '''      title: "Original chatbot title",
      body: makeElement(),
      getElementById(id)''')
    runner = runner.replace('    window: {},', '''    URL,
    window: {
      handlers: {},
      addEventListener(type, fn) { this.handlers[type] = fn; },
      print() { this.printCalls = (this.printCalls || 0) + 1; },
    },''')
    runner = runner.replace('  vm.runInContext(source, sandbox);', '''  vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), sandbox);
  vm.runInContext(source, sandbox);''')
    runner = runner.replace('resolve(elements)', 'resolve({ elements, sandbox })')
    runner += r'''
(async () => {
  const { elements, sandbox } = await runPage(docsIds.concat([
    "export-chat-button", "export-chat-status", "chat-tabs", "empty-state"
  ]), null);
  const button = elements["export-chat-button"];
  const status = elements["export-chat-status"];
  const click = button.handlers.click[0];
  if (!button.disabled) throw new Error("Empty conversation can be exported");
  click();
  if (sandbox.window.printCalls) throw new Error("Empty conversation opened print dialog");
  vm.runInContext(`rememberMessage(activeConversationId, { role: "user", text: "Question <img onerror=bad>" });`, sandbox);
  if (!button.disabled) throw new Error("Question alone enables export");
  vm.runInContext(`rememberMessage(activeConversationId, {
    role: "bot", text: "Answer **with emphasis** [Citation](https://ischool.sjsu.edu/) <img onerror=bad>"
  });`, sandbox);
  if (button.disabled) throw new Error("Answer did not enable export");
  let requests = 0;
  sandbox.fetch = async () => { requests++; throw new Error("Export made a request"); };
  click();
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
  const question = view.children[2].children[1];
  if (question.innerHTML) throw new Error("Question inserted as HTML");
  const answer = view.children[3].children[1].innerHTML;
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
  click();
  if (sandbox.window.printCalls !== 2) throw new Error("Repeat export failed");
  sandbox.window.handlers.afterprint();
  vm.runInContext("selectConversation(conversations[1].id)", sandbox);
  if (!button.disabled) throw new Error("Switching to empty chat did not disable export");
  console.log("ok");
})().catch(error => { console.error(error.stack || error); process.exit(1); });
'''
    path = tmp_path / "check_export_chat.js"
    path.write_text(runner, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(path), str(ROOT / "static" / "script.js"),
         str(ROOT / "static" / "markdown.js")],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_export_chat_accessibility_and_print_styles():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    evaluation = (ROOT / "templates" / "evaluation.html").read_text(encoding="utf-8")
    assert re.search(r'<button id="export-chat-button"[^>]*type="button"[^>]*disabled>', template)
    assert "Download this conversation as a PDF using your browser's print dialog" in template
    assert 'id="export-chat-status" class="export-chat-status" role="status" aria-live="polite"' in template
    assert "button.clear-btn:focus-visible" in styles
    assert "#chat-print-view { display: none; }" in styles
    assert "@media print" in styles
    assert "body > :not(#chat-print-view) { display: none !important; }" in styles
    assert 'filename=\'evaluation.css\')' in evaluation
    assert 'filename=\'evaluation.js\')' in evaluation
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
