"""XSS-safe Markdown for bot replies. The renderer is dependency-free JavaScript."""

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import app as app_module

ROOT = Path(__file__).resolve().parents[1]

_RUNNER = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const sandbox = { URL, console };
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const html = {};
const plain = {};
cases.forEach((item) => {
  html[item.name] = sandbox.renderBotMarkdown(item.input);
  plain[item.name] = sandbox.botMarkdownPlainText(item.input);
});
process.stdout.write(JSON.stringify({ html, plain }));
"""

_ALLOWED_TAGS = {"p", "strong", "em", "code", "ul", "ol", "li", "a", "br"}


class _FragmentChecker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.anchors = []
        self.text = []
        self.problems = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag not in _ALLOWED_TAGS:
            self.problems.append(f"disallowed tag <{tag}>")
        attributes = {name.lower(): value for name, value in attrs}
        for name in attributes:
            if name.startswith("on"):
                self.problems.append(f"event handler {name}")
        if tag == "a":
            self.anchors.append(attributes)
            href = attributes.get("href", "")
            if not (
                href.startswith(("http://", "https://")) or href.startswith("mailto:")
            ):
                self.problems.append(f"unsafe href {href!r}")
            rel = attributes.get("rel", "")
            if "noopener" not in rel or "noreferrer" not in rel:
                self.problems.append(f"missing link rel {rel!r}")
            if attributes.get("target") != "_blank":
                self.problems.append("link target is not _blank")

    def handle_data(self, data):
        self.text.append(data)


def _render(tmp_path, cases):
    runner = tmp_path / "render_markdown.js"
    payload = tmp_path / "cases.json"
    runner.write_text(_RUNNER, encoding="utf-8")
    payload.write_text(json.dumps(cases), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(runner), str(ROOT / "static" / "markdown.js"), str(payload)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    return json.loads(completed.stdout)


def _check(html):
    parser = _FragmentChecker()
    parser.feed(html)
    parser.close()
    assert parser.problems == [], parser.problems
    return parser


CASES = [
    {"name": "bold", "input": "- **Core Courses**: essentials"},
    {"name": "underscore_bold", "input": "__bold__"},
    {"name": "italic", "input": "*italic* and _also_"},
    {"name": "snake_and_math", "input": "snake_case and 2*3 and 2*3*4 and a_b_c"},
    {"name": "code", "input": "Use `DATA 201` here."},
    {"name": "code_script", "input": "`<script>alert(1)</script>`"},
    {"name": "script", "input": "<script>alert(1)</script>"},
    {"name": "img", "input": "<img src=x onerror=alert(1)>"},
    {"name": "javascript_link", "input": "[x](javascript:alert(1))"},
    {"name": "javascript_mixed_case", "input": "[x](JaVaScRiPt:alert(1))"},
    {"name": "data_link", "input": "[x](data:text/html,<script>alert(1)</script>)"},
    {"name": "quote_breakout", "input": '[x](https://example.com/"onclick="alert(1))'},
    {
        "name": "mailto",
        "input": "[kaitlyn.stauber@sjsu.edu](mailto:kaitlyn.stauber@sjsu.edu)",
    },
    {"name": "mailto_javascript", "input": "[x](mailto:javascript:alert(1))"},
    {
        "name": "mailto_breakout",
        "input": '[x](mailto:a@b.com"onclick="alert(1))',
    },
    {"name": "https_link", "input": "[docs](https://example.com/path)"},
    {"name": "http_link", "input": "See http://example.com/a."},
    {"name": "bare_https", "input": "Catalog: https://catalog.sjsu.edu/"},
    {"name": "query_url", "input": "https://example.com/search?q=a&b=2"},
    {"name": "lists", "input": "- alpha\n* beta\n• gamma\n\n1. first\n2. second"},
    {
        "name": "loose_list",
        "input": "Hello\n\n- **Core Courses**: one\n\n- **Prerequisites**: two\n\nDone",
    },
    {
        "name": "headings",
        "input": "# Title\n## Section\n### Detail\n#### not a heading",
    },
    {"name": "breaks", "input": "line1\nline2\n\npara2"},
    {"name": "dollar", "input": "**price is $1**"},
    {"name": "raw_anchor", "input": '<a href="javascript:alert(1)">click</a>'},
    {"name": "entity_script", "input": "&#60;script&#62;alert(1)&#60;/script&#62;"},
]


def test_renderer_file_is_served_and_template_uses_it():
    client = app_module.app.test_client()
    script = client.get("/static/markdown.js")
    assert script.status_code == 200
    body = script.get_data(as_text=True)
    assert "function renderBotMarkdown" in body
    assert "function botMarkdownPlainText" in body

    page = client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "markdown.js" in html
    assert "script.js" in html
    assert html.index("markdown.js") < html.index("script.js")

    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert "filename='markdown.js'" in template
    chat = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    assert "container.textContent = text" in chat
    assert "container.innerHTML = renderBotMarkdown(text)" in chat
    assert "botMarkdownPlainText(responseText)" in chat


def test_safe_markdown_subset(tmp_path):
    rendered = _render(tmp_path, CASES)
    html = rendered["html"]
    plain = rendered["plain"]

    core = _check(html["bold"])
    assert "<ul><li><strong>Core Courses</strong>: essentials</li></ul>" == html["bold"]
    assert "Core Courses" in "".join(core.text)
    assert plain["bold"] == "Core Courses: essentials"
    assert "<" not in plain["bold"]

    assert html["underscore_bold"] == "<p><strong>bold</strong></p>"
    assert html["italic"] == "<p><em>italic</em> and <em>also</em></p>"
    assert html["snake_and_math"] == "<p>snake_case and 2*3 and 2*3*4 and a_b_c</p>"
    assert html["code"] == "<p>Use <code>DATA 201</code> here.</p>"
    assert (
        html["code_script"]
        == "<p><code>&lt;script&gt;alert(1)&lt;/script&gt;</code></p>"
    )
    assert plain["code_script"] == "<script>alert(1)</script>"
    assert "<code>" not in plain["code_script"]

    for name in (
        "script",
        "img",
        "javascript_link",
        "javascript_mixed_case",
        "data_link",
        "mailto",
        "mailto_javascript",
        "mailto_breakout",
        "quote_breakout",
        "https_link",
        "http_link",
        "bare_https",
        "query_url",
        "lists",
        "loose_list",
        "headings",
        "breaks",
        "dollar",
        "raw_anchor",
        "entity_script",
    ):
        checked = _check(html[name])
        assert "<script" not in html[name].lower()
        assert "<img" not in html[name].lower()
        for anchor in checked.anchors:
            assert "javascript:" not in anchor["href"].lower()

    assert html["script"] == "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"
    assert html["img"] == "<p>&lt;img src=x onerror=alert(1)&gt;</p>"
    assert "<a " not in html["javascript_link"]
    assert "<a " not in html["javascript_mixed_case"]
    assert "<a " not in html["data_link"]
    assert "<a " not in html["mailto_javascript"]
    assert "<a " not in html["mailto_breakout"]

    mail = _check(html["mailto"])
    assert len(mail.anchors) == 1
    assert mail.anchors[0]["href"] == "mailto:kaitlyn.stauber@sjsu.edu"
    assert "".join(mail.text) == "kaitlyn.stauber@sjsu.edu"
    assert plain["mailto"] == "kaitlyn.stauber@sjsu.edu"
    assert "<a " not in html["raw_anchor"]
    assert "&lt;a href=" in html["raw_anchor"]

    quote = _check(html["quote_breakout"])
    assert len(quote.anchors) == 1
    assert quote.anchors[0]["href"].startswith("https://example.com/")
    assert list(quote.anchors[0]) == ["class", "href", "target", "rel"]

    docs = _check(html["https_link"])
    assert docs.anchors[0]["href"] == "https://example.com/path"
    assert "".join(docs.text) == "docs"

    bare = _check(html["http_link"])
    assert bare.anchors[0]["href"] == "http://example.com/a"
    assert html["http_link"].endswith(".</p>")

    query = _check(html["query_url"])
    assert query.anchors[0]["href"] == "https://example.com/search?q=a&b=2"
    assert "q=a&b=2" in "".join(query.text)

    assert html["lists"].count("<ul>") == 1
    assert html["lists"].count("<li>") == 5
    assert "<ol>" in html["lists"]
    assert "<strong>Core Courses</strong>" in html["loose_list"]
    assert "<strong>Prerequisites</strong>" in html["loose_list"]
    assert html["loose_list"].count("<ul>") == 1
    assert html["headings"].count('class="md-heading') == 3
    assert "#### not a heading" in html["headings"]
    assert html["breaks"] == "<p>line1<br>line2</p><p>para2</p>"
    assert html["dollar"] == "<p><strong>price is $1</strong></p>"
    assert "<script" not in html["entity_script"]
    assert plain["dollar"] == "price is $1"
    assert "**" not in plain["loose_list"]
