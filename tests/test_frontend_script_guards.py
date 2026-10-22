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
_PAGE_RUNNER = (
    "\n"
    'const fs = require("fs");\n'
    'const vm = require("vm");\n'
    "\n"
    'const source = fs.readFileSync(process.argv[2], "utf8");\n'
    "\n"
    'process.on("unhandledRejection", (error) => {\n'
    "  console.error(error && error.stack ? error.stack : error);\n"
    "  process.exit(1);\n"
    "});\n"
    "\n"
    "function makeElement() {\n"
    "  const classes = new Set();\n"
    "  return {\n"
    "    classes,\n"
    "    classList: {\n"
    "      add(name) { classes.add(name); },\n"
    "      remove(name) { classes.delete(name); },\n"
    "      toggle(name, force) {\n"
    "        if (force === undefined) {\n"
    "          if (classes.has(name)) classes.delete(name);\n"
    "          else classes.add(name);\n"
    "        } else if (force) classes.add(name);\n"
    "        else classes.delete(name);\n"
    "      },\n"
    "      contains(name) { return classes.has(name); },\n"
    "    },\n"
    "    handlers: {},\n"
    "    addEventListener(type, fn) {\n"
    "      if (!this.handlers[type]) this.handlers[type] = [];\n"
    "      this.handlers[type].push(fn);\n"
    "    },\n"
    "    setAttribute() {},\n"
    "    appendChild() {},\n"
    "    remove() {},\n"
    "    focus() {},\n"
    '    textContent: "",\n'
    '    innerHTML: "",\n'
    '    value: "",\n'
    "    disabled: false,\n"
    '    title: "",\n'
    "    checked: false,\n"
    "    dataset: {},\n"
    "  };\n"
    "}\n"
    "\n"
    "function runPage(ids, voiceConfig) {\n"
    "  const elements = {};\n"
    "  ids.forEach((id) => {\n"
    "    elements[id] = makeElement();\n"
    "  });\n"
    "  const sandbox = {\n"
    "    console,\n"
    "    setTimeout,\n"
    "    clearTimeout,\n"
    "    document: {\n"
    "      getElementById(id) { return elements[id] || null; },\n"
    "      querySelectorAll() { return []; },\n"
    "      createElement() { return makeElement(); },\n"
    "    },\n"
    "    fetch: async () => ({\n"
    "      ok: voiceConfig !== null,\n"
    "      json: async () => voiceConfig || {},\n"
    "    }),\n"
    "    window: {},\n"
    "  };\n"
    "  vm.createContext(sandbox);\n"
    "  vm.runInContext(source, sandbox);\n"
    "  return new Promise((resolve) => {\n"
    "    setTimeout(() => resolve(elements), 0);\n"
    "  });\n"
    "}\n"
    "\n"
    "const docsIds = [\n"
    '  "chat-box", "user-input", "send-button", "clear-button",\n'
    '  "status-text", "provider-text", "source-badge", "docs-badge",\n'
    "];\n"
    "const mainIds = docsIds.concat([\n"
    '  "active-topic-banner", "active-topic-label", "sample-chips-area",\n'
    '  "sample-chips-label", "sample-chips", "voice-tools", '
    '"voice-listen-button",\n'
    '  "voice-listen-label", "voice-language-select", "voice-reply-toggle",\n'
    '  "voice-stop-button", "voice-status", "voice-support-badge",\n'
    "]);\n"
    "\n"
    "(async () => {\n"
    "  const docs = await runPage(docsIds, null);\n"
    '  const clearFns = docs["clear-button"].handlers.click || [];\n'
    '  if (clearFns.length !== 1) throw new Error("clear button listener '
    'missing");\n'
    "  await clearFns[0]();\n"
    '  if (!String(docs["chat-box"].innerHTML).includes("BS ISDA and MLIS '
    'advising assistant")) {\n'
    '    throw new Error("clear did not reset the chat box");\n'
    "  }\n"
    "\n"
    "  const disabled = await runPage(mainIds, { enabled: false, mode: "
    '"disabled" });\n'
    "  const enabled = await runPage(mainIds, null);\n"
    "  console.log(JSON.stringify({\n"
    "    docsClearOk: true,\n"
    '    disabledButton: disabled["voice-listen-button"].disabled,\n'
    '    disabledTitle: disabled["voice-listen-button"].title,\n'
    '    disabledHidden: disabled["voice-tools"].classes.has("hidden"),\n'
    '    disabledStatus: disabled["voice-status"].textContent,\n'
    '    enabledTitle: enabled["voice-listen-button"].title,\n'
    '    enabledHidden: enabled["voice-tools"].classes.has("hidden"),\n'
    '    enabledStatus: enabled["voice-status"].textContent,\n'
    '    enabledBadge: enabled["voice-support-badge"].textContent,\n'
    "  }));\n"
    "})().catch((error) => {\n"
    "  console.error(error && error.stack ? error.stack : error);\n"
    "  process.exit(1);\n"
    "});\n"
)


def test_confidence_display_is_gone_and_link_rendering_is_unchanged():
    script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    assert "confidenceBadge" not in script
    assert "info.confidence" not in script
    assert "confidence: data.confidence" not in script
    assert 'id="confidence-badge"' not in template
    assert 'id="confidence-badge"' not in docs
    assert "filename='style.css', v='20261017-dynamic-suggestions'" in template
    assert "style.css?v=20261017-dynamic-suggestions" in docs
    assert "filename='suggestions.js', v='20261017-dynamic-suggestions'" in template
    assert "suggestions.js?v=20261017-dynamic-suggestions" in docs
    assert "filename='script.js', v='20261017-dynamic-suggestions'" in template
    assert "script.js?v=20261017-dynamic-suggestions" in docs
    assert "20261014-advisor-handoff" not in template
    assert "20261014-advisor-handoff" not in docs
    assert "20261017-long-term-reports" not in template
    assert "20261017-long-term-reports" not in docs
    assert "20261017-scope-notice" not in template
    assert "20261017-scope-notice" not in docs
    assert "20261001-reveal-tabs" not in template
    assert "20260927-tabs" not in docs
    assert "20261002-letter-reveal" not in template
    assert "20261002-letter-reveal" not in docs
    assert "Report issue" in script
    assert "Include this conversation so the team can review it" in script
    assert (
        "Only the reason, your comment, and this answer are saved unless you "
        "include the conversation."
    ) in script
    assert (
        "Reports, including any shared conversation, are kept long-term by "
        "the iSchool to improve the assistant."
    ) in script
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
    chat = script.split("async function sendMessage(", 1)[1].split(
        "function reportContext(", 1
    )[0]
    assert 'typeof data.error === "string" && data.error.trim()' in chat
    assert 'typeof error.message === "string"' in chat
    assert (
        "deliverBotReply(conversationId, safeMessage ? errorMessage : fallbackError)"
        in chat
    )
    assert "Sorry, I couldn't get an answer right now. Please try again." in chat
    assert re.search(
        r"try\s*\{\s*data = .*?response\.json\(\).*?\}\s*catch", chat, re.S
    )

    runner = tmp_path / "check_chat_errors.js"
    runner.write_text(
        (
            "\n"
            'const fs = require("fs");\n'
            'const vm = require("vm");\n'
            'const source = fs.readFileSync(process.argv[2], "utf8");\n'
            'const chat = source.slice(source.indexOf("async function '
            'sendMessage("), source.indexOf("function reportContext("));\n'
            "const fallback = \"Sorry, I couldn't get an answer right now. Please "
            'try again.";\n'
            "\n"
            "async function check({ body = {}, ok = false, parseError, "
            "fetchError, cancelled = false, expected = fallback }) {\n"
            "  const replies = [];\n"
            "  const operations = new Map();\n"
            "  const noop = () => {};\n"
            "  const sandbox = {\n"
            "    AbortController, replyOperations: operations, activeReplyKey: "
            '() => "default",\n'
            '    userInput: { value: "Question" }, conversationDrafts: new Map(),\n'
            "    hideSampleChips: noop, updateMeta: noop, addMessage: noop,\n"
            "    showTypingIndicator: noop, updateReplyControls: noop, "
            "renderTabs: noop,\n"
            '    programSelect: null, lastSelectedProgram: "both", '
            "normalizeProgram: value => value,\n"
            "    apiUrl: value => value, removeTypingIndicator: noop,\n"
            "    deliverBotReply: (id, text) => replies.push(text),\n"
            "    revealReply: (key, text) => replies.push(text),\n"
            "    fetch: async () => {\n"
            "      if (fetchError) throw fetchError;\n"
            "      return { ok, json: async () => {\n"
            "        if (cancelled) operations.clear();\n"
            "        if (parseError) throw parseError;\n"
            "        return body;\n"
            "      } };\n"
            "    },\n"
            "  };\n"
            "  vm.createContext(sandbox);\n"
            "  vm.runInContext(chat, sandbox);\n"
            '  await vm.runInContext("sendMessage()", sandbox);\n'
            "  const wanted = expected === null ? [] : [expected];\n"
            "  if (JSON.stringify(replies) !== JSON.stringify(wanted)) {\n"
            '    throw new Error("Expected " + JSON.stringify(wanted) + ", got " '
            "+ JSON.stringify(replies));\n"
            "  }\n"
            "}\n"
            "\n"
            "(async () => {\n"
            '  for (const message of ["Too many requests. Please try again in a '
            'minute.", "The advising index is not ready. Please try again '
            'later."]) {\n'
            "    await check({ body: { error: message }, expected: message });\n"
            "  }\n"
            '  for (const error of [null, 42, {}, "", "   ", "x".repeat(300),\n'
            '      "TypeError: Failed to fetch", "ReferenceError: value is not '
            'defined",\n'
            '      "SyntaxError: Unexpected token", "NetworkError: Request '
            'failed",\n'
            '      "Error: internal failure", "Exception: internal failure",\n'
            '      "Cannot read properties of undefined", "handler is not a '
            'function",\n'
            '      "Error\\n    at sendMessage (script.js:900)", "at sendMessage '
            '(script.js:900)"]) {\n'
            "    await check({ body: { error } });\n"
            "  }\n"
            "  await check({ body: null });\n"
            "  await check({ parseError: new SyntaxError(\"Unexpected token '<'\") "
            "});\n"
            '  await check({ parseError: Object.assign(new Error("Cancelled"), { '
            'name: "AbortError" }), expected: null });\n'
            '  await check({ fetchError: new TypeError("Failed to fetch") });\n'
            '  await check({ fetchError: Object.assign(new Error("Cancelled"), { '
            'name: "AbortError" }), expected: null });\n'
            '  await check({ cancelled: true, body: { error: "Please try later." '
            "}, expected: null });\n"
            '  await check({ ok: true, body: { response: "Successful answer" }, '
            'expected: "Successful answer" });\n'
            "})().catch(error => { console.error(error); process.exit(1); });\n"
        ),
        encoding="utf-8",
    )
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
    assert report["enabledTitle"] == "Speech recognition is unavailable in this browser"
    assert report["enabledBadge"] == "Text-only browser"
    assert (
        report["enabledStatus"]
        == "This browser does not expose speech controls. You can still use text chat."
    )


_PROGRAM_RUNNER = (
    "\n"
    'const fs = require("fs");\n'
    'const vm = require("vm");\n'
    "\n"
    'const source = fs.readFileSync(process.argv[2], "utf8");\n'
    "\n"
    'process.on("unhandledRejection", (error) => {\n'
    "  console.error(error && error.stack ? error.stack : error);\n"
    "  process.exit(1);\n"
    "});\n"
    "\n"
    "function makeElement() {\n"
    "  const classes = new Set();\n"
    "  return {\n"
    "    classes,\n"
    "    classList: {\n"
    "      add(name) { classes.add(name); },\n"
    "      remove(name) { classes.delete(name); },\n"
    "      toggle(name, force) {\n"
    "        if (force === undefined) {\n"
    "          if (classes.has(name)) classes.delete(name);\n"
    "          else classes.add(name);\n"
    "        } else if (force) classes.add(name);\n"
    "        else classes.delete(name);\n"
    "      },\n"
    "      contains(name) { return classes.has(name); },\n"
    "    },\n"
    "    handlers: {},\n"
    "    addEventListener(type, fn) {\n"
    "      if (!this.handlers[type]) this.handlers[type] = [];\n"
    "      this.handlers[type].push(fn);\n"
    "    },\n"
    "    setAttribute() {},\n"
    "    appendChild() {},\n"
    "    remove() {},\n"
    "    focus() {},\n"
    '    textContent: "",\n'
    '    innerHTML: "",\n'
    '    value: "",\n'
    "    disabled: false,\n"
    '    title: "",\n'
    "    checked: false,\n"
    "    dataset: {},\n"
    "  };\n"
    "}\n"
    "\n"
    "function runPage(ids, voiceConfig, storage) {\n"
    "  const elements = {};\n"
    "  ids.forEach((id) => {\n"
    "    elements[id] = makeElement();\n"
    "  });\n"
    "  const sandbox = {\n"
    "    console,\n"
    "    AbortController,\n"
    "    localStorage: storage,\n"
    "    setTimeout,\n"
    "    clearTimeout,\n"
    "    document: {\n"
    "      getElementById(id) { return elements[id] || null; },\n"
    "      querySelectorAll() { return []; },\n"
    "      createElement() { return makeElement(); },\n"
    "    },\n"
    "    fetch: async () => ({\n"
    "      ok: voiceConfig !== null,\n"
    "      json: async () => voiceConfig || {},\n"
    "    }),\n"
    "    window: {},\n"
    "  };\n"
    "  vm.createContext(sandbox);\n"
    "  vm.runInContext(source, sandbox);\n"
    "  return new Promise((resolve) => {\n"
    "    setTimeout(() => resolve({ elements, sandbox }), 0);\n"
    "  });\n"
    "}\n"
    "\n"
    "\n"
    'const assert = require("assert");\n'
    "const values = new Map();\n"
    "const storage = { getItem(key) { return values.get(key) || null; }, "
    "setItem(key, value) { values.set(key, value); } };\n"
    'const ids = ["chat-box", "user-input", "send-button", "clear-button", '
    '"status-text", "provider-text", "source-badge", "docs-badge", "chat-tabs", '
    '"empty-state", "program-select"];\n'
    "(async () => {\n"
    "  const { elements, sandbox } = await runPage(ids, null, storage);\n"
    "  const run = code => vm.runInContext(code, sandbox);\n"
    '  assert.equal(elements["program-select"].value, "both");\n'
    '  elements["program-select"].value = "mlis";\n'
    '  elements["program-select"].handlers.change[0]();\n'
    '  const first = run("activeConversationId");\n'
    '  run(\'getActiveConversation().messages.push({ role: "user", text: "core '
    "courses\" }); startNewConversation();');\n"
    '  const second = run("activeConversationId");\n'
    "  assert.notEqual(first, second);\n"
    '  assert.equal(elements["program-select"].value, "mlis");\n'
    '  elements["program-select"].value = "isda";\n'
    '  elements["program-select"].handlers.change[0]();\n'
    "  run('conversations[0].messages = [];');\n"
    "  run(`selectConversation(${JSON.stringify(first)})`);\n"
    '  assert.equal(elements["program-select"].value, "mlis");\n'
    '  assert.equal(run("lastSelectedProgram"), "isda");\n'
    "  const restored = await runPage(ids, null, storage);\n"
    '  assert.equal(restored.elements["program-select"].value, "mlis");\n'
    '  assert.equal(vm.runInContext("lastSelectedProgram", restored.sandbox), '
    '"isda");\n'
    '  run("startNewConversation()");\n'
    '  assert.equal(elements["program-select"].value, "isda");\n'
    '  assert.equal(run("activeConversationId"), first);\n'
    "  run(`selectConversation(${JSON.stringify(second)})`);\n"
    '  assert.equal(elements["program-select"].value, "isda");\n'
    '  run(\'getActiveConversation().messages.push({ role: "user", text: '
    '"courses" }); startNewConversation();\');\n'
    '  assert.equal(elements["program-select"].value, "isda");\n'
    "  // Exercise the real request construction while isolating reply rendering.\n"
    "  const sent = [];\n"
    "  sandbox.fetch = async (url, options) => {\n"
    '    if (url.endsWith("/api/chat")) sent.push(JSON.parse(options.body));\n'
    '    return { ok: false, json: async () => ({ error: "test" }) };\n'
    "  };\n"
    "  run('deliverBotReply = () => {};');\n"
    "  await run('sendMessage(\"core courses\")');\n"
    '  assert.equal(sent[0].program, "isda");\n'
    '  assert.equal(sent[0].conversation_id, run("activeConversationId"));\n'
    '  console.log("program state and request checks passed");\n'
    "})().catch(error => { console.error(error.stack); process.exit(1); });\n"
)


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
        (
            "    remove() {\n"
            "      if (this.parent) this.parent.children = "
            "this.parent.children.filter(child => child !== this);\n"
            "    },"
        ),
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
    runner += (
        "\n"
        "(async () => {\n"
        "  const { elements, sandbox } = await runPage(docsIds.concat([\n"
        '    "export-chat-button", "export-chat-status", "chat-tabs", '
        '"empty-state"\n'
        "  ]), null);\n"
        '  const button = elements["export-chat-button"];\n'
        '  const status = elements["export-chat-status"];\n'
        "  const click = button.handlers.click[0];\n"
        '  if (!button.disabled) throw new Error("Empty conversation can be '
        'exported");\n'
        "  await click();\n"
        '  if (sandbox.window.printCalls) throw new Error("Empty conversation '
        'opened print dialog");\n'
        "  vm.runInContext(`rememberMessage(activeConversationId, { role: "
        '"user", text: "Question <img onerror=bad>" });`, sandbox);\n'
        '  if (!button.disabled) throw new Error("Question alone enables '
        'export");\n'
        "  vm.runInContext(`rememberMessage(activeConversationId, {\n"
        '    role: "bot", text: "Answer **with emphasis** '
        '[Citation](https://ischool.sjsu.edu/) <img onerror=bad>"\n'
        "  });`, sandbox);\n"
        '  if (button.disabled) throw new Error("Answer did not enable export");\n'
        "  let requests = 0;\n"
        '  sandbox.fetch = async () => { requests++; throw new Error("Export '
        'made a request"); };\n'
        "  await click();\n"
        '  if (sandbox.window.printCalls !== 1) throw new Error("Export did not '
        'call window.print");\n'
        '  if (requests) throw new Error("Export fetched data");\n'
        "  const view = sandbox.document.body.children.find(child => child.id "
        '=== "chat-print-view");\n'
        '  if (!view) throw new Error("Print view missing");\n'
        "  const texts = node => [node.textContent, "
        "...node.children.flatMap(texts)];\n"
        '  const text = texts(view).join("\\n");\n'
        '  for (const expected of ["SJSU iSchool Advising Chatbot - '
        'conversation", "Exported:",\n'
        '      "You", "Advising assistant", "Question <img onerror=bad>",\n'
        '      "This copy was created on your device. The advising chatbot does '
        'not keep it."]) {\n'
        '    if (!text.includes(expected)) throw new Error("Missing print text: '
        '" + expected);\n'
        "  }\n"
        '  if (!text.includes("Program: Not sure / both")) throw new '
        'Error("Default program header missing");\n'
        "  const question = view.children[3].children[1];\n"
        '  if (question.innerHTML) throw new Error("Question inserted as HTML");\n'
        "  const answer = view.children[4].children[1].innerHTML;\n"
        '  if (!answer.includes("Answer") || !answer.includes("<strong>with '
        'emphasis</strong>") ||\n'
        "      !answer.includes('href=\"https://ischool.sjsu.edu/\"') || "
        '!answer.includes("Citation")) {\n'
        '    throw new Error("Answer formatting or citation lost: " + answer);\n'
        "  }\n"
        '  if (answer.includes("<img")) throw new Error("Unsanitized answer '
        'HTML");\n'
        "  if (!/^SJSU-advising-chat-\\d{4}-\\d{2}-\\d{2}$/.test(sandbox.document.t"
        "itle)) {\n"
        '    throw new Error("Missing PDF filename title");\n'
        "  }\n"
        '  if (status.textContent !== "Choose “Save as PDF” in the print dialog '
        'to keep a copy.") {\n'
        '    throw new Error("Missing export status");\n'
        "  }\n"
        "  sandbox.window.handlers.afterprint();\n"
        "  if (sandbox.document.body.children.includes(view)) throw new "
        'Error("Print view was not removed");\n'
        '  if (sandbox.document.title !== "Original chatbot title") throw new '
        'Error("Title was not restored");\n'
        "  // New chat and tab switching must refresh the enabled state.\n"
        '  vm.runInContext("startNewConversation()", sandbox);\n'
        '  if (!button.disabled || status.textContent) throw new Error("New chat '
        'did not reset export");\n'
        '  vm.runInContext("selectConversation(conversations[0].id)", sandbox);\n'
        '  if (button.disabled) throw new Error("Switching to answered chat did '
        'not enable export");\n'
        "  await click();\n"
        '  if (sandbox.window.printCalls !== 2) throw new Error("Repeat export '
        'failed");\n'
        "  sandbox.window.handlers.afterprint();\n"
        '  vm.runInContext("selectConversation(conversations[1].id)", sandbox);\n'
        '  if (!button.disabled) throw new Error("Switching to empty chat did '
        'not disable export");\n'
        '  vm.runInContext("selectConversation(conversations[0].id)", sandbox);\n'
        "  vm.runInContext('getActiveConversation().program = \"mlis\"', "
        "sandbox);\n"
        "  // Both constructor and save failures must open the existing print "
        "fallback.\n"
        "  sandbox.window.jspdf = { jsPDF: class { constructor() { throw new "
        'Error("broken PDF"); } } };\n'
        "  await click();\n"
        '  if (sandbox.window.printCalls !== 3) throw new Error("Constructor '
        'failure did not fall back");\n'
        "  const programView = sandbox.document.body.children.find(child => "
        'child.id === "chat-print-view");\n'
        '  if (!texts(programView).includes("Program: MLIS")) throw new '
        'Error("Print program missing");\n'
        "  sandbox.window.handlers.afterprint();\n"
        "  // Load the UMD browser global with eval disabled, as required by the "
        "CSP.\n"
        "  const browser = {\n"
        '    navigator: { userAgent: "test" }, document: { createElement: () => '
        "({}) },\n"
        "    atob, btoa, TextEncoder, TextDecoder,\n"
        "  };\n"
        "  browser.window = browser;\n"
        "  vm.createContext(browser, { codeGeneration: { strings: false, wasm: "
        "false } });\n"
        '  vm.runInContext(fs.readFileSync(process.argv[4], "utf8"), browser);\n'
        "  const { jsPDF } = browser.jspdf;\n"
        "  sandbox.window.jspdf = { jsPDF: function(options) {\n"
        "    const pdf = new jsPDF(options);\n"
        '    pdf.save = () => { throw new Error("save failed"); };\n'
        "    return pdf;\n"
        "  } };\n"
        "  await click();\n"
        '  if (sandbox.window.printCalls !== 4) throw new Error("Save failure '
        'did not fall back");\n'
        "  sandbox.window.handlers.afterprint();\n"
        "  let saved;\n"
        "  sandbox.window.jspdf = { jsPDF: function(options) {\n"
        "    const pdf = new jsPDF(options);\n"
        "    const text = pdf.text.bind(pdf);\n"
        "    pdf.text = (line, x, y) => {\n"
        "      if (x < 48 || y < 48 || y > pdf.internal.pageSize.getHeight() - "
        "48) {\n"
        '        throw new Error("Text outside page margins");\n'
        "      }\n"
        "      if (pdf.getTextWidth(line) > pdf.internal.pageSize.getWidth() - "
        "96 + 0.1) {\n"
        '        throw new Error("Text was not wrapped");\n'
        "      }\n"
        "      return text(line, x, y);\n"
        "    };\n"
        "    pdf.save = filename => {\n"
        "      saved = { filename, pdf: Buffer.from(pdf.output(), "
        '"binary").toString("base64") };\n'
        "    };\n"
        "    return pdf;\n"
        "  } };\n"
        "  vm.runInContext(`\n"
        '    for (const [value, expected] of [["isda", "ISDA"], ["mlis", "MLIS"],\n'
        '        ["both", "Not sure / both"], [undefined, null], [null, null],\n'
        '        [42, null], [{}, null], ["unknown", null], ["toString", null]]) '
        "{\n"
        "      if (chatExportProgramLabel({ program: value }) !== expected) "
        'throw new Error("Invalid program label");\n'
        "    }\n"
        "    if (chatExportProgramLabel(null) !== null || "
        "chatExportProgramLabel() !== null) {\n"
        '      throw new Error("Missing chat caused a program error");\n'
        "    }\n"
        '    getActiveConversation().program = "both";\n'
        "    const recordedAt = Date.UTC(2026, 9, 9, 12, 30);\n"
        '    rememberMessage(activeConversationId, { role: "user", text: "Long '
        'question", timestamp: recordedAt });\n'
        "    rememberMessage(activeConversationId, {\n"
        '      role: "bot", text: "Wrapped paragraph ".repeat(2000) + "\\\\n" + '
        '"x".repeat(1000) +\n'
        '        "\\\\nFinal answer [Citation](https://ischool.sjsu.edu/) '
        '[Email](mailto:advisor@sjsu.edu)",\n'
        "      timestamp: recordedAt\n"
        "    });\n"
        "    const restored = normalizeConversation(getActiveConversation());\n"
        "    if (restored.messages.at(-1).timestamp !== recordedAt) throw new "
        'Error("Timestamp not preserved");\n'
        '    if (normalizeMessage({role: "bot", text: "old"}).timestamp !== '
        'null) throw new Error("Invented old timestamp");\n'
        "    if (!Number.isFinite(restored.messages[0].timestamp)) throw new "
        'Error("New turn timestamp missing");\n'
        "    // An old saved message must export without inventing its timestamp.\n"
        "    getActiveConversation().messages[0].timestamp = null;\n"
        "    const other = createConversation();\n"
        "    conversations.push(other);\n"
        '    rememberMessage(other.id, { role: "bot", text: "Other conversation '
        'secret" });\n'
        "  `, sandbox);\n"
        "  await click();\n"
        "  if (!saved || !/^SJSU-advising-chat-\\d{4}-\\d{2}-\\d{2}\\.pdf$/.test(sa"
        "ved.filename)) {\n"
        '    throw new Error("PDF download or filename missing");\n'
        "  }\n"
        '  if (sandbox.window.printCalls !== 4) throw new Error("Successful PDF '
        'opened print dialog");\n'
        '  if (requests) throw new Error("PDF export fetched data");\n'
        '  if (status.textContent !== "PDF downloaded.") throw new Error("PDF '
        'status missing");\n'
        '  if (sandbox.document.title !== "Original chatbot title" || '
        "sandbox.document.body.children.length) {\n"
        '    throw new Error("PDF export changed document or left print view");\n'
        "  }\n"
        '  saved.timestamp = vm.runInContext("new '
        'Date(recordedAt).toLocaleString()", sandbox);\n'
        '  for (const outcome of ["success", "error", "missing", '
        '"cross-origin"]) {\n'
        "    const page = await runPage(docsIds.concat([\n"
        '      "export-chat-button", "export-chat-status", "chat-tabs", '
        '"empty-state"\n'
        "    ]), null);\n"
        "    const target = page.sandbox;\n"
        '    target.window.location = { href: "https://example.test/", origin: '
        '"https://example.test" };\n'
        "    target.document.head = makeElement();\n"
        '    const exportButton = page.elements["export-chat-button"];\n'
        '    exportButton.dataset.jspdfSrc = outcome === "cross-origin"\n'
        '      ? "https://other.test/jspdf.js" : '
        '"/static/vendor/jspdf/jspdf.umd.min.js?v=4.2.1";\n'
        "    vm.runInContext('rememberMessage(activeConversationId, { role: "
        '"bot", text: "Answer" })\', target);\n'
        '    if (target.document.head.children.length) throw new Error("Eager '
        'PDF library load");\n'
        "    const exportClick = exportButton.handlers.click[0];\n"
        "    const pending = exportClick();\n"
        "    const repeated = exportClick();\n"
        "    const scripts = target.document.head.children;\n"
        '    if (outcome === "cross-origin") {\n'
        '      if (scripts.length) throw new Error("Cross-origin script '
        'inserted");\n'
        "    } else {\n"
        '      if (scripts.length !== 1) throw new Error("Load promise not '
        'shared");\n'
        '      if (scripts[0].src !== "https://example.test/static/vendor/jspdf/j'
        'spdf.umd.min.js?v=4.2.1") {\n'
        '        throw new Error("Wrong library URL");\n'
        "      }\n"
        '      if (outcome === "success") {\n'
        "        target.window.jspdf = { jsPDF: function(options) {\n"
        "          const pdf = new jsPDF(options);\n"
        "          pdf.save = () => {};\n"
        "          return pdf;\n"
        "        } };\n"
        "        scripts[0].onload();\n"
        '      } else if (outcome === "error") scripts[0].onerror();\n'
        "      else scripts[0].onload();\n"
        "    }\n"
        "    await Promise.all([pending, repeated]);\n"
        '    if (outcome === "success") {\n'
        '      if (target.window.printCalls) throw new Error("Loaded PDF fell '
        'back to print");\n'
        '    } else if (target.window.printCalls !== 2) throw new Error("Load '
        'failure did not print");\n'
        '    if (target.document.body.children.length !== (outcome === "success" '
        "? 0 : 1)) {\n"
        '      throw new Error("Repeated export left stale print views");\n'
        "    }\n"
        "    await exportClick();\n"
        '    if (scripts.length !== (outcome === "cross-origin" ? 0 : 1)) throw '
        'new Error("Library loaded again");\n'
        "  }\n"
        "  console.log(JSON.stringify(saved));\n"
        "})().catch(error => { console.error(error.stack || error); "
        "process.exit(1); });\n"
    )
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
    assert re.search(
        r'<button[^>]+data-jspdf-src="[^"\n]*' + re.escape(reference), template
    )
    assert not re.search(r"<script[^>]+jspdf", template, re.I)
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
    assert re.search(
        r'<button id="export-chat-button"[^>]*type="button"[^>]*disabled>', template
    )
    assert "Download this conversation as a PDF" in template
    assert (
        'id="export-chat-status" class="export-chat-status" role="status" '
        'aria-live="polite"'
    ) in template
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


_EVALUATION_RUNNER = (
    "\n"
    "const fs = require('fs');\n"
    "const vm = require('vm');\n"
    "const assert = require('assert');\n"
    "class Element {\n"
    "  constructor(tag = 'div') {\n"
    "    this.tag = tag;\n"
    "    this.children = [];\n"
    "    this.dataset = {};\n"
    "    this.attributes = {};\n"
    "    this.handlers = {};\n"
    "    this.value = '';\n"
    "    this._text = '';\n"
    "    const classes = new Set();\n"
    "    this.classList = {\n"
    "      add: name => classes.add(name),\n"
    "      remove: name => classes.delete(name),\n"
    "      contains: name => classes.has(name),\n"
    "      toggle: (name, force) => force ? classes.add(name) : "
    "classes.delete(name),\n"
    "    };\n"
    "  }\n"
    "  set innerHTML(value) { throw new Error('Unsafe innerHTML assignment'); }\n"
    "  set textContent(value) { this._text = String(value); this.children = []; }\n"
    "  get textContent() { return this._text + this.children.map(child => "
    "child.textContent).join(''); }\n"
    "  get lastChild() { return this.children[this.children.length - 1]; }\n"
    "  append(...children) { this.children.push(...children); }\n"
    "  appendChild(child) { this.append(child); }\n"
    "  replaceChildren(...children) { this._text = ''; this.children = children; "
    "}\n"
    "  setAttribute(key, value) { this.attributes[key] = value; }\n"
    "  addEventListener(type, handler) { this.handlers[type] = handler; }\n"
    "  showModal() {}\n"
    "}\n"
    "const nodes = {};\n"
    "const node = id => nodes[id] || (nodes[id] = new Element());\n"
    "const attack = '<img src=x onerror=alert(1)>';\n"
    "let data = {\n"
    "  reports: [\n"
    "    {reason: 'other', reason_label: 'Other', comment: attack, question: "
    "attack, answer: attack,\n"
    "     include_conversation: true, provider: attack, model: attack, "
    "conversation_id: attack,\n"
    "     citations: [attack], turns: [{role: 'user', text: attack}]},\n"
    "    {reason: 'made_up', reason_label: attack, comment: 'A visible comment', "
    "question: 'Q', answer: 'A'},\n"
    "    {reason: 'incorrect_info', reason_label: 'Incorrect information', "
    "comment: '', question: 'Q', answer: 'A'},\n"
    "  ],\n"
    "  summary: {total: 205, reasons: [\n"
    "    {key: 'other', label: 'Other', count: 200},\n"
    "    {key: 'made_up', label: attack, count: 4},\n"
    "    {key: 'incorrect_info', label: 'Incorrect information', count: 1},\n"
    "    {key: 'outdated', label: 'Outdated information', count: 0},\n"
    "  ]},\n"
    "};\n"
    "const sandbox = {\n"
    "  console, Headers, setTimeout, clearTimeout,\n"
    "  window: {setTimeout, clearTimeout},\n"
    "  document: {getElementById: node, querySelectorAll: () => [], "
    "createElement: tag => new Element(tag)},\n"
    "  fetch: async path => ({\n"
    "    ok: path.includes('issue-reports'), status: 401,\n"
    "    headers: {get: () => 'application/json'},\n"
    "    json: async () => path.includes('issue-reports') ? data : {error: 'Sign "
    "in'},\n"
    "  }),\n"
    "};\n"
    "vm.createContext(sandbox);\n"
    "vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);\n"
    "(async () => {\n"
    "  await new Promise(resolve => setTimeout(resolve, 0));\n"
    "  await vm.runInContext('state.reviewer = \"Reviewer\"; loadIssueReports()', "
    "sandbox);\n"
    "  const summary = node('issue-reports-summary');\n"
    "  const filters = node('issue-reports-filters');\n"
    "  const list = node('issue-reports-list');\n"
    "  const status = node('issue-reports-status');\n"
    "  assert.strictEqual(summary.children.length, 4);\n"
    "  assert(summary.children[0].textContent.includes('Other200Most reported'));\n"
    "  assert(summary.children[0].classList.contains('issue-reports-summary__reas"
    "on--top'));\n"
    "  assert.strictEqual(summary.children[1].children[0].textContent, attack);\n"
    "  assert.strictEqual(summary.children[3].children[1].textContent, '0');\n"
    "  assert.strictEqual(filters.children.length, 5);\n"
    "  assert.strictEqual(filters.children[0].textContent, 'All');\n"
    "  assert.strictEqual(filters.children[0].attributes['aria-pressed'], "
    "'true');\n"
    "  assert.strictEqual(status.textContent, 'Showing 3 of 205 reports');\n"
    "  assert(node('issue-reports-limit').textContent.includes('3 newest loaded "
    "reports'));\n"
    "  assert.strictEqual(list.children.length, 3);\n"
    '  assert(list.children[0].textContent.includes("Student\'s description" + '
    "attack));\n"
    "  assert(list.children[1].textContent.includes('CommentA visible comment'));\n"
    "  assert(list.children[2].textContent.includes('Comment—'));\n"
    "  filters.children[1].handlers.click();\n"
    "  assert.strictEqual(list.children.length, 1);\n"
    "  assert.strictEqual(status.textContent, 'Showing 1 of 205 reports');\n"
    "  assert.strictEqual(filters.children[1].attributes['aria-pressed'], "
    "'true');\n"
    "  assert.strictEqual(filters.children[0].attributes['aria-pressed'], "
    "'false');\n"
    "  await vm.runInContext('loadIssueReports()', sandbox);\n"
    "  assert.strictEqual(status.textContent, 'Showing 1 of 205 reports');\n"
    "  filters.children[4].handlers.click();\n"
    "  assert.strictEqual(status.textContent, 'Showing 0 of 205 reports');\n"
    "  assert.strictEqual(list.textContent, 'No loaded reports for this "
    "reason.');\n"
    "  filters.children[0].handlers.click();\n"
    "  assert.strictEqual(list.children.length, 3);\n"
    "  data = {reports: [], summary: {total: 0, reasons: "
    "data.summary.reasons.map(r => ({...r, count: 0}))}};\n"
    "  await vm.runInContext('loadIssueReports()', sandbox);\n"
    "  assert.strictEqual(status.textContent, 'Showing 0 of 0 reports');\n"
    "  assert.strictEqual(list.textContent, 'No student reports yet.');\n"
    "  assert(!summary.textContent.includes('Most reported'));\n"
    "  assert.strictEqual(node('issue-reports-limit').textContent, '');\n"
    "  console.log('ok');\n"
    "})().catch(error => { console.error(error); process.exit(1); });\n"
)


def test_evaluation_report_summary_and_filters_render_safely(tmp_path):
    template = (ROOT / "templates" / "evaluation.html").read_text(encoding="utf-8")
    for filename in ["evaluation.js", "evaluation.css"]:
        assert f"filename='{filename}', v='20261017-dynamic-suggestions'" in template
    assert 'role="status" aria-live="polite"' in template
    assert 'role="group" aria-label="Filter reports by reason"' in template
    runner = tmp_path / "check_evaluation_reports.js"
    runner.write_text(_EVALUATION_RUNNER, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "evaluation.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert completed.stdout.strip() == "ok"
