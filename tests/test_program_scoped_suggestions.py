"""Every suggestion surface stays inside the selected program.

With MLIS selected the autocomplete used to offer ISDA questions: off-program
items only lost a few points instead of being filtered out. These tests pin
the fix for the typeahead (Python and browser matcher), starter chips,
dynamic starters, the verified top-five acknowledgement, and follow-up reply
chips, plus the client wiring that refreshes them on a program switch.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

import app as app_module
from question_suggestions import (
    filter_for_program,
    load_suggestion_catalog,
    rank_question_suggestions,
    visible_for_program,
)
from reply_suggestions import named_programs, scope_suggestions
from suggestion_usage import (
    POPULAR_QUESTION_ACKNOWLEDGEMENT,
    student_suggestion_payload,
    verified_popular_click,
)

ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = ("isda", "mlis", "mara", "msis")
PREFIXES = (
    "Can I",
    "can i",
    "how",
    "How do I",
    "how many",
    "what",
    "What is",
    "what are the",
    "core courses",
    "advising",
    "e-portfolio",
    "required courses",
    "apply",
    "graduate",
    "units",
    "Who is",
    "Do",
    "Are there",
    "where",
    "isda",
    "mlis",
    "info 2",
)


def _catalog():
    return load_suggestion_catalog()


def _texts(items):
    return [item["text"] for item in items]


def _id_for(text):
    return next(item["id"] for item in _catalog()["questions"] if item["text"] == text)


# ── Catalog tags ──


def test_catalog_tags_match_the_programs_each_question_names():
    """A question that names a program is tagged for exactly that program."""
    for item in _catalog()["questions"]:
        named = named_programs(item["text"])
        if named:
            assert set(item["programs"]) == named, item["text"]
        assert "both" not in item["programs"] or not named, item["text"]
    tags = {name for item in _catalog()["questions"] for name in item["programs"]}
    assert tags == {"both", *PROGRAMS}


def test_community_college_transfer_is_not_offered_to_graduate_programs():
    # Community college credit only applies to the undergraduate degree.
    item = next(
        item
        for item in _catalog()["questions"]
        if item["text"] == "Can I transfer community college credits?"
    )
    assert item["programs"] == ["isda"]


# ── Typeahead ranking ──


@pytest.mark.parametrize("program", PROGRAMS)
@pytest.mark.parametrize("query", PREFIXES)
def test_selected_program_only_sees_its_own_and_neutral_questions(program, query):
    ranked = rank_question_suggestions(query, _catalog()["questions"], program=program, limit=8)
    for item in ranked:
        assert visible_for_program(item, program), (program, query, item["text"])
        other = named_programs(item["text"]) - {program}
        assert not other, (program, query, item["text"])


@pytest.mark.parametrize("query", ["Can I", "how", "what", "What is"])
def test_mlis_prefixes_never_mention_isda(query):
    ranked = _texts(rank_question_suggestions(query, _catalog()["questions"], program="mlis", limit=8))
    assert ranked
    assert all(not re.search(r"\b(?:BS ?)?ISDA\b|BSISDA", text) for text in ranked), ranked


@pytest.mark.parametrize("query", ["how", "what", "What is", "what are the"])
def test_isda_prefixes_never_mention_graduate_programs(query):
    ranked = _texts(rank_question_suggestions(query, _catalog()["questions"], program="isda", limit=8))
    assert ranked
    for text in ranked:
        assert not re.search(r"\bMLIS\b|\bMARA\b|Informatics|\bMSIS\b|\bINFO \d", text), text


def test_can_i_with_mlis_returns_only_neutral_questions():
    ranked = rank_question_suggestions("Can I", _catalog()["questions"], program="mlis")
    assert _texts(ranked) == ["Can I take two core courses at the same time?"]
    assert all(item["programs"] == ["both"] for item in ranked)


@pytest.mark.parametrize(
    "program, expected",
    [
        ("mara", "What are the MARA required courses?"),
        ("msis", "What are the MS Informatics required courses?"),
        ("mlis", "What are the MLIS core courses?"),
        ("isda", "What are the ISDA core courses?"),
    ],
)
def test_each_program_gets_its_own_questions(program, expected):
    ranked = _texts(rank_question_suggestions("what are the", _catalog()["questions"], program=program))
    assert expected in ranked


def test_explicit_other_program_query_returns_nothing_for_a_selected_program():
    questions = _catalog()["questions"]
    assert rank_question_suggestions("isda 160", questions, program="mlis") == []
    assert rank_question_suggestions("MARA electives", questions, program="msis") == []


def test_no_program_puts_neutral_questions_first_and_spreads_programs():
    questions = _catalog()["questions"]
    how = rank_question_suggestions("how", questions)
    assert how[0]["programs"] == ["both"]
    # "What is" matches many ISDA course titles; no program may fill the list.
    for query in ("what is", "what", "how", "what are the"):
        ranked = rank_question_suggestions(query, questions, limit=6)
        assert ranked[0]["programs"] == ["both"], query
        for program in PROGRAMS:
            count = sum(1 for item in ranked if item["programs"] == [program])
            assert count <= 2, (query, program, _texts(ranked))


def test_no_program_still_returns_one_program_when_only_it_matches():
    ranked = rank_question_suggestions("isda 1", _catalog()["questions"], limit=6)
    assert len(ranked) == 6
    assert all(item["programs"] == ["isda"] for item in ranked)


def test_unknown_program_behaves_like_no_program():
    questions = _catalog()["questions"]
    assert rank_question_suggestions("how", questions, program="xyz") == (
        rank_question_suggestions("how", questions)
    )


# ── Starters ──


def test_fallback_starters_are_neutral_first_and_program_lists_stay_in_program():
    catalog = _catalog()
    by_id = {item["id"]: item for item in catalog["questions"]}
    shared = [by_id[name] for name in catalog["fallback_starter_ids"]]
    assert shared[0]["programs"] == ["both"]
    assert sum(1 for item in shared if item["programs"] == ["isda"]) <= 1
    assert sum(1 for item in shared if item["programs"] == ["mlis"]) <= 1
    for program in PROGRAMS:
        ids = catalog["fallback_starter_ids_by_program"][program]
        assert len(ids) == 4, program
        assert all(by_id[name]["programs"] == [program] for name in ids), program


def test_script_program_starters_mirror_the_catalog():
    script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    catalog = _catalog()
    by_id = {item["id"]: item for item in catalog["questions"]}
    for program in PROGRAMS:
        block = script.split(f"const {program.upper()}_STARTERS = [", 1)[1].split("];", 1)[0]
        rows = re.findall(r'\{ id: "([^"]+)", topic: "([^"]+)", text: "([^"]+)" \}', block)
        assert [row[0] for row in rows] == catalog["fallback_starter_ids_by_program"][program]
        for suggestion_id, _topic, text in rows:
            assert by_id[suggestion_id]["text"] == text


def test_page_starter_chips_match_the_shared_fallback():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    chips = re.findall(r'class="starter-chip"\s+data-question="([^"]+)"', template)
    by_id = {item["id"]: item for item in _catalog()["questions"]}
    assert chips == [by_id[name]["text"] for name in _catalog()["fallback_starter_ids"]]


def test_docs_sample_chips_carry_program_tags():
    docs = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    programs = {item["text"]: item["programs"] for item in _catalog()["questions"]}
    chips = re.findall(r'<button type="button"( data-programs="([^"]+)")? class="prompt-chip">([^<]+)</button>', docs)
    assert chips
    for _attr, tagged, text in chips:
        expected = programs[text]
        if expected == ["both"]:
            assert not tagged, text
        else:
            assert tagged.split() == expected, text


@pytest.fixture
def usage_store(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTION_SUGGESTIONS_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_SUGGESTIONS_ENABLED", "true")
    monkeypatch.setenv("SUGGESTION_RANKING_MODE", "all_time")
    monkeypatch.setenv("SUGGESTION_POPULARITY_MIN_COUNT", "3")
    monkeypatch.setenv("EVAL_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setattr(app_module, "_suggestion_usage_store", None)
    monkeypatch.setattr(app_module, "_suggestion_usage_store_key", None)
    return app_module.get_suggestion_usage_store()


def _pick(store, suggestion_id, times):
    for index in range(times):
        store.record_pick(
            suggestion_id=suggestion_id,
            event_type="dropdown",
            program="both",
            session_token=f"{suggestion_id}-{index}",
        )


def test_dynamic_starters_are_chosen_per_program(usage_store):
    isda_popular = _id_for("Are there scholarships for ISDA students?")
    mlis_popular = _id_for("What is INFO 200: Information Communities?")
    _pick(usage_store, isda_popular, 9)
    _pick(usage_store, mlis_popular, 5)
    payload = student_suggestion_payload(usage_store)
    assert payload["dynamic"] is True
    by_id = {item["id"]: item for item in payload["questions"]}
    starters = payload["starters_by_program"]
    assert set(starters) == set(PROGRAMS)
    for program, ids in starters.items():
        assert len(ids) == 4, program
        assert all(visible_for_program(by_id[name], program) for name in ids), program
    assert starters["isda"][0] == isda_popular
    assert starters["mlis"][0] == mlis_popular
    assert isda_popular not in starters["mlis"]
    assert mlis_popular not in starters["isda"]
    assert isda_popular not in starters["mara"] + starters["msis"]
    assert starters["mara"] == _catalog()["fallback_starter_ids_by_program"]["mara"]


def test_flag_off_payload_shape_is_unchanged(monkeypatch):
    monkeypatch.delenv("DYNAMIC_SUGGESTIONS_ENABLED", raising=False)
    payload = student_suggestion_payload(None)
    assert set(payload) == {"enabled", "questions", "dynamic"}


# ── Verified top-five acknowledgement ──


def test_top_five_is_ranked_within_the_selected_program(usage_store):
    questions = _catalog()["questions"]
    isda_items = [item for item in questions if item["programs"] == ["isda"]][:5]
    for index, item in enumerate(isda_items):
        _pick(usage_store, item["id"], 10 - index)
    mlis_item = next(item for item in questions if item["programs"] == ["mlis"])
    _pick(usage_store, mlis_item["id"], 1)
    click = {"id": mlis_item["id"], "text": mlis_item["text"]}
    # Globally the MLIS question is sixth; within MLIS it is the most picked.
    assert not verified_popular_click(usage_store, click, mlis_item["text"])
    assert verified_popular_click(usage_store, click, mlis_item["text"], "mlis")
    isda_click = {"id": isda_items[0]["id"], "text": isda_items[0]["text"]}
    assert verified_popular_click(usage_store, isda_click, isda_items[0]["text"])
    assert verified_popular_click(usage_store, isda_click, isda_items[0]["text"], "isda")
    assert not verified_popular_click(usage_store, isda_click, isda_items[0]["text"], "mlis")
    assert verified_popular_click(usage_store, isda_click, isda_items[0]["text"], "both")


class _Bot:
    def __init__(self, suggestions=None):
        self.calls = []
        self.suggestions = suggestions or []

    def chat(self, message, history=None, program=None):
        self.calls.append(program)
        return {
            "user_message": message,
            "response": "Normal answer.",
            "source": "rag",
            "confidence": 0.8,
            "retrieved_docs": [],
            "suggestions": list(self.suggestions),
        }


def test_chat_route_verifies_popularity_for_the_selected_program(
    usage_store, monkeypatch, ready_chat_index
):
    questions = _catalog()["questions"]
    isda_item = next(item for item in questions if item["programs"] == ["isda"])
    _pick(usage_store, isda_item["id"], 4)
    bot = _Bot()
    monkeypatch.setattr(app_module, "get_chatbot", lambda: bot)
    client = app_module.app.test_client()
    click = {"id": isda_item["id"], "text": isda_item["text"]}
    mlis = client.post(
        "/api/chat",
        json={"message": isda_item["text"], "program": "mlis", "suggestion_click": click},
    ).get_json()
    assert mlis["response"] == "Normal answer."
    isda = client.post(
        "/api/chat",
        json={"message": isda_item["text"], "program": "isda", "suggestion_click": click},
    ).get_json()
    assert isda["response"] == POPULAR_QUESTION_ACKNOWLEDGEMENT + "\n\nNormal answer."
    assert bot.calls == ["mlis", "isda"]


# ── Follow-up reply chips ──


def test_scope_suggestions_drops_chips_for_other_programs():
    chips = [
        "What are the ISDA core courses?",
        "How do I apply to the MLIS program?",
        "When is the fee payment deadline?",
        "Compare MLIS and ISDA units",
        "What is INFM 211?",
        "Is there a master's thesis option?",
    ]
    assert scope_suggestions(chips, "mlis") == [
        "How do I apply to the MLIS program?",
        "When is the fee payment deadline?",
        "Compare MLIS and ISDA units",
        "Is there a master's thesis option?",
    ]
    assert scope_suggestions(chips, "isda") == [
        "What are the ISDA core courses?",
        "When is the fee payment deadline?",
        "Compare MLIS and ISDA units",
        "Is there a master's thesis option?",
    ]
    assert scope_suggestions(chips, "msis") == [
        "When is the fee payment deadline?",
        "What is INFM 211?",
        "Is there a master's thesis option?",
    ]
    assert scope_suggestions(chips, "both") == chips
    assert scope_suggestions(chips, None) == chips


def test_chat_route_scopes_reply_chips(monkeypatch, ready_chat_index):
    bot = _Bot(["What are the ISDA core courses?", "What is the fee payment deadline?"])
    monkeypatch.setattr(app_module, "get_chatbot", lambda: bot)
    client = app_module.app.test_client()
    mlis = client.post("/api/chat", json={"message": "Hi", "program": "mlis"}).get_json()
    assert mlis["suggestions"] == ["What is the fee payment deadline?"]
    both = client.post("/api/chat", json={"message": "Hi", "program": "both"}).get_json()
    assert both["suggestions"] == bot.suggestions


# ── Browser wiring ──

_SWITCH_RUNNER = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const script = fs.readFileSync(process.argv[2], "utf8");
const matcher = fs.readFileSync(process.argv[3], "utf8");
const payload = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));

function el(attrs) {
  return {
    attributes: Object.assign({}, attrs || {}), children: [], hidden: false,
    textContent: "", className: "", id: "", parts: {},
    setAttribute(key, value) { this.attributes[key] = String(value); },
    getAttribute(key) { return key in this.attributes ? this.attributes[key] : null; },
    removeAttribute(key) { delete this.attributes[key]; },
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    querySelectorAll(selector) {
      return selector === "[role='option']"
        ? this.children.filter((child) => child.attributes.role === "option")
        : [];
    },
    querySelector(selector) { return this.parts[selector] || null; },
    scrollIntoView() {}, addEventListener() {}, focus() {},
  };
}

function chip(question, topic) {
  const node = el({ "data-question": question });
  node.parts[".starter-topic"] = el();
  node.parts[".starter-topic"].textContent = topic;
  node.parts[".starter-question"] = el();
  node.parts[".starter-question"].textContent = question;
  return node;
}

const starterChips = [
  chip("How do I schedule an advising appointment?", "General Advising"),
  chip("How do I add or drop a course?", "Registration"),
  chip("What are the ISDA core courses?", "Degree Requirements"),
  chip("What are the MLIS core courses?", "MLIS core courses"),
];
const promptChips = [
  el({ "data-programs": "isda" }), el({ "data-programs": "mlis" }), el({ "data-programs": "mara" }),
];
const list = el();
const fetched = [];
let response = { enabled: true, dynamic: false, questions: payload.questions };
const sandbox = {
  console, setTimeout, clearTimeout, Map, Array, JSON,
  document: {
    getElementById(id) {
      if (id === "question-suggestions") return list;
      if (id === "question-suggestions-status") return el();
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".starter-chip") return starterChips;
      if (selector === ".prompt-chip[data-programs]") return promptChips;
      return [];
    },
    createElement() { return el(); },
    createTextNode(text) { return { textContent: text }; },
  },
  userInput: el(),
  programSelect: { value: "isda" },
  lastSelectedProgram: "isda",
  normalizeProgram: (value) => ["isda", "mlis", "mara", "msis", "both"].includes(value) ? value : "both",
  apiUrl: (path) => path,
  fetch: async (url, options) => {
    fetched.push({ url, options });
    return { ok: true, json: async () => response };
  },
  updateAdvisorHandoffNotes() {}, tabsEnabled: () => false, getActiveConversation: () => null,
  saveConversations() {}, activeReplyKey: () => "default", sendMessage() {},
  pendingSuggestionClick: null,
};
vm.createContext(sandbox);
vm.runInContext(matcher, sandbox);
function slice(start, end) {
  const from = script.indexOf(start);
  const to = script.indexOf(end, from);
  assert(from >= 0 && to > from, start);
  vm.runInContext(script.slice(from, to), sandbox);
}
slice("const ISDA_STARTERS = [", "// This UI card is never a transcript turn");
slice("function onProgramChange()", "function tabsEnabled()");
slice('const suggestionList = document.getElementById("question-suggestions");', "\ninitConversations();");
const run = (code) => vm.runInContext(code, sandbox);
const shown = () => run("visibleSuggestions").map((item) => item.text);
const chipText = () => starterChips.map((node) => node.getAttribute("data-question"));
const chipIds = () => starterChips.map((node) => node.getAttribute("data-suggestion-id"));

(async () => {
  await run("loadQuestionSuggestions()");
  assert.strictEqual(fetched.length, 1);
  assert.strictEqual(fetched[0].url, "/api/question-suggestions");

  // ISDA selected, list open with ISDA questions.
  sandbox.userInput.value = "what are the";
  run("refreshQuestionSuggestions()");
  assert(run("suggestionsOpen"));
  assert(shown().includes("What are the ISDA core courses?"), shown());

  // Switch to MLIS: the open list is re-ranked immediately, no ISDA left.
  sandbox.programSelect.value = "mlis";
  run("onProgramChange()");
  assert(run("suggestionsOpen"));
  assert(shown().includes("What are the MLIS core courses?"), shown());
  assert(!shown().some((text) => /ISDA/.test(text)), shown());
  assert.deepStrictEqual(Array.from(chipText()), [
    "What are the MLIS core courses?", "How do I apply to the MLIS program?",
    "What is the MLIS e-portfolio?", "How do I schedule an MLIS advising appointment?",
  ]);
  assert.deepStrictEqual(Array.from(chipIds()), [
    "what-are-the-mlis-core-courses", "apply-to-the-mlis-program",
    "the-mlis-e-portfolio", "schedule-an-mlis-advising-appointment",
  ]);
  assert.deepStrictEqual(promptChips.map((node) => node.hidden), [true, false, true]);

  // MARA: no ISDA/MLIS questions in the typeahead.
  sandbox.programSelect.value = "mara";
  run("onProgramChange()");
  assert(shown().includes("What are the MARA required courses?"), shown());
  assert(!shown().some((text) => /ISDA|MLIS|Informatics/.test(text)), shown());

  // A switch to a program with no match closes the stale list.
  sandbox.userInput.value = "isda 160";
  sandbox.programSelect.value = "isda";
  run("onProgramChange()");
  assert(shown().includes("What is ISDA 160: User Experience (UX) Research and Design?"));
  sandbox.programSelect.value = "mlis";
  run("onProgramChange()");
  assert(!run("suggestionsOpen"));
  assert.deepStrictEqual(Array.from(shown()), []);

  // Back to "Not sure / both": the page's shared chips come back.
  sandbox.programSelect.value = "both";
  run("onProgramChange()");
  assert.deepStrictEqual(Array.from(chipText()), [
    "How do I schedule an advising appointment?", "How do I add or drop a course?",
    "What are the ISDA core courses?", "What are the MLIS core courses?",
  ]);
  assert.deepStrictEqual(promptChips.map((node) => node.hidden), [false, false, false]);

  // Dynamic starters: each program uses its own server-chosen list.
  response = Object.assign({}, response, {
    dynamic: true,
    starters: payload.dynamic.starters,
    starters_by_program: payload.dynamic.starters_by_program,
    popularity: {},
  });
  sandbox.programSelect.value = "mlis";
  run("lastSelectedProgram = 'mlis'");
  await run("loadQuestionSuggestions()");
  assert.deepStrictEqual(Array.from(chipIds()), payload.dynamic.starters_by_program.mlis);
  sandbox.programSelect.value = "both";
  run("onProgramChange()");
  assert.deepStrictEqual(Array.from(chipIds()), payload.dynamic.starters);
  sandbox.programSelect.value = "isda";
  run("onProgramChange()");
  assert.deepStrictEqual(Array.from(chipIds()), payload.dynamic.starters_by_program.isda);
})().catch((error) => { console.error(error && error.stack ? error.stack : error); process.exit(1); });
"""


def test_program_switch_refreshes_typeahead_and_starters_in_the_browser(tmp_path):
    catalog = _catalog()
    by_program = catalog["fallback_starter_ids_by_program"]
    data = {
        "questions": catalog["questions"],
        "dynamic": {
            "starters": [
                _id_for("Are there scholarships for ISDA students?"),
                *catalog["fallback_starter_ids"][:3],
            ],
            "starters_by_program": {
                "isda": [_id_for("Are there scholarships for ISDA students?"), *by_program["isda"][:3]],
                "mlis": [_id_for("What is INFO 200: Information Communities?"), *by_program["mlis"][:3]],
                "mara": by_program["mara"],
                "msis": by_program["msis"],
            },
        },
    }
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(data), encoding="utf-8")
    runner = tmp_path / "switch.js"
    runner.write_text(_SWITCH_RUNNER, encoding="utf-8")
    result = subprocess.run(
        [
            "node",
            str(runner),
            str(ROOT / "static" / "script.js"),
            str(ROOT / "static" / "suggestions.js"),
            str(payload),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_script_ranks_with_the_selected_program_and_refreshes_on_every_switch():
    script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    refresh = script.split("function refreshQuestionSuggestions() {", 1)[1].split("\n}\n", 1)[0]
    assert "normalizeProgram(programSelect ? programSelect.value : lastSelectedProgram)" in refresh
    assert "rankQuestionSuggestions(query, suggestionCatalog, {\n    program," in refresh
    change = script.split("function onProgramChange() {", 1)[1].split("\n}\n", 1)[0]
    assert "applyProgramStarters(lastSelectedProgram);" in change
    assert "refreshSuggestionsForProgram();" in change
    paint = script.split("function paintConversation(convo) {", 1)[1].split("\n}\n", 1)[0]
    assert "applyProgramStarters(normalizeProgram(convo.program));" in paint
    assert "scheduleQuestionSuggestions();" in paint
    loader = script.split("async function loadQuestionSuggestions() {", 1)[1].split("\n}\n", 1)[0]
    assert "data.starters_by_program" in loader
    assert 'if (programSelect) programSelect.addEventListener("change", onProgramChange);' in script


def test_filter_for_program_keeps_neutral_questions_for_everyone():
    questions = _catalog()["questions"]
    neutral = {item["text"] for item in questions if item["programs"] == ["both"]}
    for program in PROGRAMS:
        visible = {item["text"] for item in filter_for_program(questions, program)}
        assert neutral <= visible
        assert all(
            program in item["programs"] or "both" in item["programs"]
            for item in filter_for_program(questions, program)
        )
    assert len(filter_for_program(questions, "both")) == len(questions)
