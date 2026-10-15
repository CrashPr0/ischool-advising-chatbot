"""Offline guards for the phone layout and its static mirror."""

from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/style.css").read_text()
HTML_FILES = ("templates/index.html", "docs/index.html")
ASSET_PINS = {"style.css": "20261014-advisor-handoff", "script.js": "20261014-advisor-handoff"}


def mobile_rules():
    """Read only rules inside the phone breakpoint, respecting brace nesting."""
    for match in re.finditer(r"@media\s*\(max-width:\s*640px\)\s*\{", CSS):
        start = match.end()
        depth = 1
        end = start
        while depth:
            depth += (CSS[end] == "{") - (CSS[end] == "}")
            end += 1
        body = re.sub(r"/\*.*?\*/", "", CSS[start:end - 1], flags=re.S)
        for rule in re.finditer(r"([^{}]+)\{([^{}]+)\}", body):
            yield [selector.strip() for selector in rule[1].split(",")], rule[2]


def phone_has(selector, declaration):
    return any(selector in selectors and declaration in body
               for selectors, body in mobile_rules())


def test_viewport_and_asset_pins_in_both_pages():
    for name in HTML_FILES:
        html = (ROOT / name).read_text()
        assert "viewport-fit=cover" in html
        for asset, pin in ASSET_PINS.items():
            assert re.search(rf"{re.escape(asset)}[^\n<>]*{pin}", html)
        assert "20261011-mlis-pdf" not in html


def test_dynamic_viewport_and_scrollable_dialog():
    assert "min-height: 100vh; min-height: 100dvh;" in CSS
    dialog = re.search(r"\.report-dialog\s*\{([^}]+)\}", CSS)[1]
    assert dialog.index("100vh") < dialog.index("100dvh")
    assert "overflow-y: auto;" in dialog


def test_phone_tap_targets_and_report_rows():
    for selector in (
        ".report-issue-button", ".chat-tab-close", "#export-chat-button",
        "#clear-button", "#user-input", "#voice-listen-button", "#send-button",
        ".resource-list a", ".report-primary", ".report-secondary",
        "#report-issue-done", "#voice-language-select", "#voice-stop-button",
    ):
        assert phone_has(selector, "min-height: 44px;")
        assert phone_has(selector, "min-width: 44px;")
    for selector in (".report-reasons label", ".report-toggle"):
        assert phone_has(selector, "min-height: 44px;")
    for kind in ("radio", "checkbox"):
        selector = f'.report-dialog input[type="{kind}"]'
        assert phone_has(selector, "width: 20px;")
        assert phone_has(selector, "height: 20px;")


def test_phone_header_composer_and_program_layout():
    assert phone_has(".chat-header", "flex-direction: column;")
    assert phone_has(".header-actions button", "flex: 1;")
    assert phone_has(".chat-card", "overflow: visible;")
    assert phone_has(".composer", "position: sticky;")
    assert phone_has(".composer", "bottom: 0;")
    assert phone_has(".composer", "env(safe-area-inset-bottom)")
    assert phone_has(".message", "scroll-margin-bottom:")
    assert phone_has(".program-control", "flex-direction: column;")
    assert phone_has(".program-control select", "width: 100%;")
    assert phone_has(".program-control select", "font-size: 16px;")
    assert re.search(r"\.message a\s*\{\s*overflow-wrap: anywhere;", CSS)


class ComposerParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.divs = []
        self.input_in_composer = False
        self.program_in_composer = False
        self.footer_outside_composer = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            classes = attrs.get("class", "").split()
            if "composer-footer" in classes:
                self.footer_outside_composer = not any(
                    "composer" in ancestor for ancestor in self.divs)
            self.divs.append(classes)
        inside = any("composer" in ancestor for ancestor in self.divs)
        if attrs.get("id") == "user-input":
            self.input_in_composer = inside
        if attrs.get("id") == "program-select":
            self.program_in_composer = inside

    def handle_endtag(self, tag):
        if tag == "div":
            self.divs.pop()


def test_sticky_wrapper_contains_inputs_and_excludes_voice_footer():
    for name in HTML_FILES:
        parser = ComposerParser()
        parser.feed((ROOT / name).read_text())
        assert not parser.divs
        assert parser.input_in_composer
        assert parser.program_in_composer
        if name == "templates/index.html":
            assert parser.footer_outside_composer


def test_phone_layout_reveals_new_messages():
    from pathlib import Path
    script = (Path(__file__).resolve().parent.parent / "static" / "script.js").read_text(encoding="utf-8")
    assert "function revealMessageOnPhone" in script
    assert 'matchMedia("(max-width: 640px)")' in script
    assert "prefers-reduced-motion: reduce" in script
