"""Popularity is verified independently of the request and never changes model input."""

import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

import app as app_module
from src.question_suggestions import load_suggestion_catalog
from src.suggestion_usage import POPULAR_QUESTION_ACKNOWLEDGEMENT

PREFIX = POPULAR_QUESTION_ACKNOWLEDGEMENT
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def popular_chat(monkeypatch, ready_chat_index):
    questions = load_suggestion_catalog()["questions"][:7]
    monkeypatch.setenv("QUESTION_SUGGESTIONS_ENABLED", "true")
    monkeypatch.setenv("SUGGESTION_RANKING_MODE", "all_time")
    monkeypatch.setattr(app_module, "_suggestion_usage_store", None)
    monkeypatch.setattr(app_module, "_suggestion_usage_store_key", None)
    store = app_module.get_suggestion_usage_store()
    for index, item in enumerate(questions[:6]):
        for pick in range(7 - index):
            store.record_pick(
                suggestion_id=item["id"],
                event_type="dropdown",
                program="both",
                session_token=f"browser-{pick}",
            )
    calls = []

    class Bot:
        def chat(self, message, history=None):
            calls.append({"message": message, "history": history})
            return {
                "user_message": message,
                "response": "Normal answer.",
                "source": "rag",
                "confidence": 0.8,
                "retrieved_docs": [],
            }

    monkeypatch.setattr(app_module, "get_chatbot", lambda: Bot())
    return app_module.app.test_client(), questions, store, calls


def test_acknowledgement_wording_is_fixed():
    assert PREFIX == (
        "Glad you asked — a lot of students have questions about this topic."
    )


@pytest.mark.parametrize("index", range(5))
def test_top_five_click_prefixes_answer_and_only_assistant_history(popular_chat, index):
    client, questions, store, calls = popular_chat
    item = questions[index]
    picks_before = store.counts()[item["id"]]
    body = client.post(
        "/api/chat",
        json={
            "message": item["text"],
            "suggestion_click": {
                "id": item["id"],
                "text": item["text"],
                "hidden": "marker-998877",
            },
        },
    ).get_json()
    expected = PREFIX + "\n\nNormal answer."
    assert body["response"] == expected
    assert "suggestion_click" not in body
    assert calls == [{"message": item["text"], "history": []}]
    history = client.get("/api/history").get_json()["history"]
    assert history == [{"user": item["text"], "assistant": expected, "source": "rag"}]
    dumped = json.dumps(body) + json.dumps(history) + json.dumps(calls)
    assert "suggestion_click" not in dumped
    assert "marker-998877" not in dumped
    # The chat request does not add a pick row or keep the extra client field.
    assert store.counts()[item["id"]] == picks_before
    raw = Path(store.database_path).read_bytes()
    assert b"marker-998877" not in raw
    assert b"suggestion_click" not in raw
    with sqlite3.connect(store.database_path) as connection:
        stored_questions = [
            row[0]
            for row in connection.execute(
                "SELECT question FROM anonymized_student_questions"
            )
        ]
    assert stored_questions == [item["text"]]
    assert all("marker-998877" not in text for text in stored_questions)


@pytest.mark.parametrize(
    "kind",
    [
        "typed",
        "sixth",
        "unpicked",
        "forged-rank",
        "wrong-id",
        "wrong-text",
        "boolean",
        "non-string-id",
        "empty-id",
        "hidden",
        "reset",
        "lookup-failure",
    ],
)
def test_unverified_requests_get_normal_answer(popular_chat, monkeypatch, kind):
    client, questions, store, calls = popular_chat
    item = questions[
        5 if kind in {"sixth", "forged-rank"} else 6 if kind == "unpicked" else 0
    ]
    click = {"id": item["id"], "text": item["text"]}
    if kind == "forged-rank":
        click.update(top_5=True, rank=1, popularity=999999)
    elif kind == "wrong-id":
        click["id"] = questions[5]["id"]
    elif kind == "wrong-text":
        click["text"] = "a different question"
    elif kind == "boolean":
        click = True
    elif kind == "non-string-id":
        click["id"] = 1
    elif kind == "empty-id":
        click["id"] = ""
    elif kind in {"hidden", "reset"}:
        store.moderate(
            item["id"],
            "hide" if kind == "hidden" else "reset",
            reason="Test moderation",
            reviewer="test-reviewer",
        )
    elif kind == "lookup-failure":

        def unavailable():
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(store, "counts", unavailable)
    payload = {"message": item["text"]}
    if kind != "typed":
        payload["suggestion_click"] = click
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    assert response.get_json()["response"] == "Normal answer."
    assert calls[0]["message"] == item["text"]
    assert "suggestion_click" not in calls[0]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("QUESTION_SUGGESTIONS_ENABLED", "off"),
        ("SUGGESTION_RANKING_MODE", "recent"),
    ],
)
def test_disabled_suggestion_settings_skip_the_acknowledgement(
    popular_chat, monkeypatch, name, value
):
    client, questions, _, calls = popular_chat
    monkeypatch.setenv(name, value)
    item = questions[0]
    response = client.post(
        "/api/chat",
        json={"message": item["text"], "suggestion_click": item},
    )
    assert response.status_code == 200
    assert response.get_json()["response"] == "Normal answer."
    assert calls[0]["message"] == item["text"]


def test_ranking_is_recomputed_when_counts_change(popular_chat):
    client, questions, store, _ = popular_chat
    item = questions[5]
    for pick in range(10):
        store.record_pick(
            suggestion_id=item["id"],
            event_type="chip",
            program="both",
            session_token=f"new-browser-{pick}",
        )
    response = client.post(
        "/api/chat", json={"message": item["text"], "suggestion_click": item}
    )
    assert response.get_json()["response"].startswith(PREFIX)


def test_browser_click_metadata_is_separate_and_consumed_once(tmp_path):
    runner = tmp_path / "check.js"
    runner.write_text(
        r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[2], "utf8");
const sent = [], rendered = [], replies = [];
const noop = () => {};
const sandbox = {
  AbortController, pendingSuggestionClick: null, replyOperations: new Map(),
  activeReplyKey: () => "default", userInput: {value: ""}, conversationDrafts: new Map(),
  hideSampleChips: noop, clearReplySuggestions: noop, closeQuestionSuggestions: noop,
  updateMeta: noop, addMessage: (...args) => rendered.push(args), showTypingIndicator: noop,
  updateReplyControls: noop, renderTabs: noop, programSelect: null, lastSelectedProgram: "both",
  normalizeProgram: x => x, apiUrl: x => x, removeTypingIndicator: noop,
  deliverBotReply: noop, revealReply: (key, text) => replies.push(text), recordSuggestionPick: noop,
  fetch: async (url, options) => {
    sent.push(JSON.parse(options.body));
    return {ok: true, json: async () => ({response: "Glad you asked — a lot of students have questions about this topic.\n\nNormal answer."})};
  }
};
vm.createContext(sandbox);
vm.runInContext(source.slice(source.indexOf("async function sendMessage("), source.indexOf("function reportContext(")), sandbox);
vm.runInContext(source.slice(source.indexOf("function applyQuestionSuggestion("), source.indexOf("function suggestionOptionFromEvent(")), sandbox);
async function send(code) {
  await vm.runInContext(code, sandbox);
  sandbox.replyOperations.clear();
}
(async () => {
  vm.runInContext('applyQuestionSuggestion("A question?", "curated-id")', sandbox);
  await send('sendMessage()');
  assert.deepStrictEqual(sent[0].suggestion_click, {text: "A question?", id: "curated-id"});
  assert.strictEqual(rendered[0][0], "A question?");
  assert(!JSON.stringify(rendered).includes("suggestion_click"));
  assert(!JSON.stringify(rendered).includes("curated-id"));
  assert(replies[0].startsWith("Glad you asked —"));
  sandbox.userInput.value = "A question?";
  await send('sendMessage()');
  assert(!sent[1].suggestion_click);
  vm.runInContext('applyQuestionSuggestion("A question?", "curated-id")', sandbox);
  sandbox.userInput.value = "An edited question?";
  await send('sendMessage()');
  assert(!sent[2].suggestion_click);
  await send('sendMessage("A question?", {text: "A question?", id: "curated-id"})');
  assert.strictEqual(sent[3].suggestion_click.id, "curated-id");
})().catch(error => { console.error(error); process.exit(1); });
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(runner), str(ROOT / "static/script.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    script = (ROOT / "static/script.js").read_text()
    assert (
        "pendingSuggestionClick = null;\n    scheduleQuestionSuggestions();" in script
    )
    assert "function leaveConversation() {\n  pendingSuggestionClick = null;" in script
    assert "sendMessage(q, { text: q })" in script
    assert "sendMessage(question, { text: question })" in script


def test_crisis_response_is_not_prefixed_or_saved(popular_chat, monkeypatch):
    client, questions, _, _ = popular_chat
    item = questions[0]

    class CrisisBot:
        def chat(self, message, history=None):
            return {
                "user_message": message,
                "response": "Crisis support.",
                "source": "safety",
                "confidence": None,
                "retrieved_docs": [],
                "safety_tier": "crisis",
            }

    monkeypatch.setattr(app_module, "get_chatbot", lambda: CrisisBot())
    response = client.post(
        "/api/chat", json={"message": item["text"], "suggestion_click": item}
    )
    assert response.get_json()["response"] == "Crisis support."
    assert response.get_json()["safety_tier"] == "crisis"
    assert client.get("/api/history").get_json()["history"] == []


@pytest.mark.parametrize("tier", ["distress", "concern"])
def test_safety_tiers_are_saved_without_the_acknowledgement(
    popular_chat, monkeypatch, tier
):
    client, questions, _, _ = popular_chat
    item = questions[0]
    reply = f"{tier} support."

    class SupportBot:
        def chat(self, message, history=None):
            return {
                "user_message": message,
                "response": reply,
                "source": "safety",
                "confidence": None,
                "retrieved_docs": [],
                "safety_tier": tier,
            }

    monkeypatch.setattr(app_module, "get_chatbot", lambda: SupportBot())
    response = client.post(
        "/api/chat", json={"message": item["text"], "suggestion_click": item}
    )
    body = response.get_json()
    assert body["response"] == reply
    assert body["safety_tier"] == tier
    assert PREFIX not in body["response"]
    history = client.get("/api/history").get_json()["history"]
    assert history == [{"user": item["text"], "assistant": reply, "source": "safety"}]


def test_empty_counts_never_invent_popularity(popular_chat):
    client, questions, store, _ = popular_chat
    for item in questions[:6]:
        store.moderate(
            item["id"], "reset", reason="Reset test counts", reviewer="test-reviewer"
        )
    item = questions[0]
    response = client.post(
        "/api/chat", json={"message": item["text"], "suggestion_click": item}
    )
    assert response.get_json()["response"] == "Normal answer."
