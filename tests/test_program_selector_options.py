"""Program picker lists every supported program in the page, template and script."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ["both", "isda", "mlis", "mara", "msis"]


def _option_values(path):
    html = (ROOT / path).read_text(encoding="utf-8")
    select = re.search(r'<select id="program-select".*?</select>', html, re.S).group(0)
    return re.findall(r'<option value="([^"]+)"', select)


def test_pages_list_all_programs_in_order():
    assert _option_values("docs/index.html") == KEYS
    assert _option_values("templates/index.html") == KEYS


def test_script_accepts_new_program_keys():
    script = (ROOT / "static/script.js").read_text(encoding="utf-8")
    assert '["isda", "mlis", "mara", "msis", "both"].includes(value)' in script
    assert 'case "mara": return "MARA";' in script
    assert 'case "msis": return "MSIS";' in script
    assert "mara: { email" in script and "msis: { email" in script
    suggestions = (ROOT / "static/suggestions.js").read_text(encoding="utf-8")
    assert "mara: true" in suggestions and "msis: true" in suggestions


def test_mara_sample_questions_are_wired():
    script = (ROOT / "static/script.js").read_text(encoding="utf-8")
    assert "MARA_STARTERS" in script and "applyProgramStarters(lastSelectedProgram)" in script
    docs = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    for question in (
        "What are the MARA required courses?",
        "How many units does the MARA program require?",
        "How do MARA electives work?",
        "What is the MARA 289 e-Portfolio?",
    ):
        assert question in script
        assert f'class="prompt-chip">{question}</button>' in docs


def test_msis_sample_questions_are_wired():
    script = (ROOT / "static/script.js").read_text(encoding="utf-8")
    assert "MSIS_STARTERS" in script
    docs = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert "PROGRAM_STARTERS = { mara: MARA_STARTERS, msis: MSIS_STARTERS }" in script
    for question in (
        "What are the MS Informatics required courses?",
        "What is the MS Informatics course load?",
        "What is the INFM 211 Informatics capstone course?",
        "How does MS Informatics advising work?",
    ):
        assert question in script
        assert f'class="prompt-chip">{question}</button>' in docs


def test_pick_events_skip_programs_the_usage_store_rejects():
    script = (ROOT / "static/script.js").read_text(encoding="utf-8")
    assert 'const SUGGESTION_EVENT_PROGRAMS = ["isda", "mlis", "both"];' in script
    assert "if (!SUGGESTION_EVENT_PROGRAMS.includes(program)) return;" in script
    store = (ROOT / "src/suggestion_usage.py").read_text(encoding="utf-8")
    assert '_PROGRAMS = {"isda", "mlis", "both"}' in store


def test_page_copy_names_mara_and_msis():
    root = Path(__file__).resolve().parents[1]
    for page in ("docs/index.html", "templates/index.html"):
        text = (root / page).read_text(encoding="utf-8")
        assert "MARA" in text and "MS Informatics" in text


def test_flask_sidebar_offers_mara_topics():
    template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
    for question in (
        "What are the MARA required courses?",
        "What is the MARA 289 e-Portfolio?",
    ):
        assert f'data-hint="{question}"' in template
