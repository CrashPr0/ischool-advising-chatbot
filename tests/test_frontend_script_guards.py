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
    assert "filename='style.css', v='20261001-reveal-tabs'" in template
    assert "style.css?v=20260927-tabs" in docs
    assert "filename='script.js', v='20261002-letter-reveal'" in template
    assert "script.js?v=20261002-letter-reveal" in docs
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
    # The disclaimer paragraph is owned by the grounding change.
    assert "Questions you submit are sent to an AI provider (Azure OpenAI)" in template
    assert "Do not paste a student ID" in template
    assert "about one hour" in template
    assert "Spartan Connect" in template
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
