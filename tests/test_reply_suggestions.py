"""Follow-up suggestion chips: backend parsing, the chat route, and the page script."""

import json
import re
import subprocess
from pathlib import Path

import pytest

import app as app_module
from src.reply_suggestions import clean_suggestions, split_reply_suggestions

ROOT = Path(__file__).resolve().parents[1]


# ── parsing and validation ──


def test_trailing_line_is_split_off_the_answer():
    text = 'ISDA needs 120 units.\n\nSUGGESTIONS: ["Tell me about MLIS", "Core courses"]'
    answer, suggestions = split_reply_suggestions(text)
    assert answer == "ISDA needs 120 units."
    assert suggestions == ["Tell me about MLIS", "Core courses"]


def test_text_without_a_marker_is_unchanged():
    text = "Plain answer.\n"
    assert split_reply_suggestions(text) == (text, [])


@pytest.mark.parametrize(
    "tail",
    ["SUGGESTIONS: [not json]", 'SUGGESTIONS: ["a", ]', "SUGGESTIONS: [1, 2]", "SUGGESTIONS: []"],
)
def test_malformed_line_is_removed_and_yields_no_chips(tail):
    answer, suggestions = split_reply_suggestions(f"Answer.\n{tail}")
    assert suggestions == []
    assert "SUGGESTIONS" not in answer


def test_marker_in_the_middle_is_left_alone():
    text = 'SUGGESTIONS: ["x"]\nMore answer text.'
    assert split_reply_suggestions(text) == (text, [])


def test_clean_caps_count_length_and_drops_empty_and_duplicates():
    value = ["  ", "One", "one", "x" * 81, 7, None, "Two  words", "Three", "Four"]
    assert clean_suggestions(value) == ["One", "Two words", "Three"]


def test_clean_rejects_non_lists():
    for value in (None, "text", {"a": "b"}, 3):
        assert clean_suggestions(value) == []


@pytest.mark.parametrize(
    "bad",
    [
        "See https://example.com",
        "Visit www.sjsu.edu",
        "[site](http://x.test)",
        "Email me at student@sjsu.edu",
        "Call 408-555-0123",
        "My ID is 012345678",
        "mailto:a@b.co",
    ],
)
def test_links_and_pii_are_dropped(bad):
    assert clean_suggestions([bad, "Safe one"]) == ["Safe one"]


# ── model integration ──


def test_prompt_allows_suggestions_but_never_instead_of_answering():
    from src.rag_chatbot import RAGAdvisingChatbot

    prompt = RAGAdvisingChatbot.SYSTEM_PROMPT
    assert "SUGGESTIONS:" in prompt
    assert "Answer the question first" in prompt
    assert "Never ask a question instead of answering" in prompt


def _bot_replying(content):
    from tests.test_rag_chatbot import _bare_bot

    bot = _bare_bot()
    bot.chain.invoke = lambda payload: type("Reply", (), {"content": content})()
    return bot


def test_chat_strips_the_line_and_returns_clean_suggestions():
    bot = _bot_replying(
        'DATA 201 is required.\nSUGGESTIONS: ["ISDA or MLIS?", "https://x.test", "ISDA or MLIS?"]'
    )
    result = bot.chat("What are the required courses?")
    assert result["response"] == "DATA 201 is required."
    assert result["suggestions"] == ["ISDA or MLIS?"]


def test_chat_without_a_line_has_no_suggestions_key():
    result = _bot_replying("DATA 201 is required.").chat("What are the required courses?")
    assert result["response"] == "DATA 201 is required."
    assert "suggestions" not in result


# ── route ──


class _Bot:
    def __init__(self, suggestions):
        self.suggestions = suggestions

    def chat(self, message, history=None, program=None):
        result = {
            "user_message": message,
            "response": "Answer",
            "source": "test",
            "confidence": None,
            "retrieved_docs": [],
        }
        if self.suggestions is not None:
            result["suggestions"] = self.suggestions
        return result


def _post(monkeypatch, suggestions):
    monkeypatch.setattr(app_module, "get_chatbot", lambda: _Bot(suggestions))
    response = app_module.app.test_client().post("/api/chat", json={"message": "hi"})
    assert response.status_code == 200
    return response.get_json()


def test_route_includes_validated_suggestions(monkeypatch, ready_chat_index):
    body = _post(monkeypatch, ["A", "a", "B", "x@y.com", "C", "D"])
    assert body["suggestions"] == ["A", "B", "C"]


@pytest.mark.parametrize("value", [None, [], "nope", [" ", 3], ["https://x.test"]])
def test_route_omits_missing_or_invalid_suggestions(monkeypatch, ready_chat_index, value):
    assert "suggestions" not in _post(monkeypatch, value)


# ── page script ──

_RUNNER = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const start = source.indexOf("// ── FOLLOW-UP SUGGESTION CHIPS ──");
const end = source.indexOf("\nfunction speakBotReply");
if (start < 0 || end < 0) throw new Error("chip block not found");

function el(tag) {
  return {
    tag, children: [], classes: new Set(), handlers: {}, textContent: "", type: "",
    classList: { add(n) { this._s.add(n); }, _s: null },
    setAttribute() {},
    addEventListener(t, f) { this.handlers[t] = f; },
    appendChild(c) { this.children.push(c); c.parent = this; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); },
    querySelectorAll(sel) {
      const cls = sel.slice(1), out = [];
      const walk = (n) => n.children.forEach(c => { if (c.classes.has(cls)) out.push(c); walk(c); });
      walk(this);
      return out;
    },
  };
}
function make(tag) { const e = el(tag); e.classList._s = e.classes; return e; }

const sent = [];
const chatBox = make("div");
const sandbox = { document: { createElement: make }, chatBox, sendMessage: (t) => sent.push(t) };
vm.createContext(sandbox);
vm.runInContext(
  source.slice(start, end).replace(/^const /gm, "var ") + "\nthis.attach = attachReplySuggestions;",
  sandbox
);
const bot = make("div");
chatBox.appendChild(bot);

const cases = {};
cases.none = [undefined, null, [], "x", [" ", 5]].map(v => sandbox.attach(bot, v));
cases.noneChips = bot.querySelectorAll(".reply-suggestions").length;
const group = sandbox.attach(bot, ["Tell me about MLIS", "tell me about mlis", "Core courses", "", "a", "b"]);
cases.labels = group.children.map(c => c.textContent);
group.children[1].handlers.click();
cases.sent = sent;
const next = make("div"); chatBox.appendChild(next);
sandbox.attach(next, ["Only one"]);
cases.groups = chatBox.querySelectorAll(".reply-suggestions").length;
console.log(JSON.stringify(cases));
"""


def test_chips_render_only_with_suggestions_and_tap_sends_text(tmp_path):
    runner = tmp_path / "chips.js"
    runner.write_text(_RUNNER, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "script.js")],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    out = json.loads(completed.stdout)
    assert out["none"] == [None] * 5
    assert out["noneChips"] == 0
    assert out["labels"] == ["Tell me about MLIS", "Core courses", "a"]
    assert out["sent"] == ["Core courses"]
    assert out["groups"] == 1  # a new set replaces the old one


def test_script_wires_suggestions_into_send_and_delivery():
    script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    assert "suggestions: data.suggestions" in script
    assert re.search(r"attachReplySuggestions\(botWrapper, meta && meta\.suggestions\)", script)
    assert 'if (typeof clearReplySuggestions === "function") clearReplySuggestions();' in script
    assert 'chip.addEventListener("click", () => sendMessage(text))' in script
    for page in ("templates/index.html", "docs/index.html"):
        assert "20261023-reply-suggestions" in (ROOT / page).read_text(encoding="utf-8")
    assert ".reply-suggestion-chip" in (ROOT / "static" / "style.css").read_text(encoding="utf-8")
