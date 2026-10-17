"""The empty chat screen states what the assistant is, and is not, for."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

NOTICE = (
    "This assistant only answers iSchool advising questions using official "
    "iSchool website information. It is not a replacement for an advisor, "
    "and it is not for personal, emergency, or wellbeing issues."
)


def _notice(html: str) -> str:
    match = re.search(
        r'<p class="scope-notice" role="note">(.*?)</p>',
        html,
        flags=re.S,
    )
    assert match, "scope notice missing"
    return re.sub(r"\s+", " ", match.group(1)).strip()


def test_empty_chat_screen_states_the_scope_and_points_to_an_advisor():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    empty = template.split('id="empty-state"', 1)[1].split("</div>", 1)[0]
    notice = _notice(empty)
    assert NOTICE in notice
    assert 'href="https://ischool.sjsu.edu/advising-appointments"' in notice
    assert "Contact an advisor" in notice
    assert 'role="note"' in empty

    docs = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert NOTICE in _notice(docs)


def test_scope_notice_does_not_add_crisis_wording():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    notice = _notice(template).lower()
    for phrase in ("988", "suicide", "crisis", "hotline", "counseling center"):
        assert phrase not in notice
    styles = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ".scope-notice {" in styles
    assert "color: var(--text-main)" in styles.split(".scope-notice {", 1)[1].split("}", 1)[0]
