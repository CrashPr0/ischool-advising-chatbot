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
