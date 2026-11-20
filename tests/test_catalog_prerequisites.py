"""Structured catalog prerequisite table, its completeness check, and injection.

Parser samples are real lines from committed catalog course pages. The table
artifact must equal what the generator produces from the corpus. A question
that names courses gets their table entries in the model context whatever
retrieval returned. Offline: no network, no paid model.
"""

import json
from datetime import date
from pathlib import Path

import pytest
from langchain_core.documents import Document

from scripts import build_prerequisite_table, check_sjsu_catalog
from src import catalog_prerequisites as prereq
from src.catalog_update import CatalogCheckResult, check_for_newer_catalog
from src.knowledge_base import get_knowledge_base
from src.rag_chatbot import RAGAdvisingChatbot
from tests.test_catalog_update import _fetch, _pages, _program_html, _repo

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "data" / "advising_docs"
TABLE = ROOT / "data" / prereq.TABLE_FILENAME
ALLOWLIST = ROOT / "data" / prereq.ALLOWLIST_FILENAME
ISDA_130_URL = "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=202916"
ISDA_131_URL = "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=202917"
ISDA_131_PREREQ = "Prerequisite(s): ISDA 130."
ISDA_130_PREREQ = "Prerequisite(s): ISDA 20C, ISDA 20D, and ISDA 111."


def _pages_by_url():
    return {page.source_url: page for page in prereq.load_corpus_pages(DOCS)}


def _course_text(code):
    for page in prereq.load_corpus_pages(DOCS):
        if prereq.is_catalog_course_page(page) and page.text.startswith(f"{code} - "):
            return page.text
    raise AssertionError(f"{code} has no catalog course page in the corpus")


def _courses():
    return json.loads(TABLE.read_text(encoding="utf-8"))["courses"]


# ------------------------------------------------------------------ parser


def test_isda_131_prerequisite_is_isda_130():
    parsed = prereq.parse_requisites(_course_text("ISDA 131"))
    assert parsed["status"] == "listed"
    assert parsed["requisite_lines"] == [ISDA_131_PREREQ]
    assert parsed["prerequisite_codes"] == ["ISDA 130"]
    assert parsed["corequisite_codes"] == []
    assert parsed["pre_corequisite_codes"] == []


def test_isda_130_lists_three_prerequisites_with_and():
    parsed = prereq.parse_requisites(_course_text("ISDA 130"))
    assert parsed["requisite_lines"] == [ISDA_130_PREREQ]
    assert parsed["prerequisite_codes"] == ["ISDA 20C", "ISDA 20D", "ISDA 111"]
    assert "alternatives (or)" not in parsed["conditions"]


def test_corequisite_and_pre_corequisite_lines_are_separate():
    isda_110 = prereq.parse_requisites(_course_text("ISDA 110"))
    assert isda_110["requisite_lines"] == ["Corequisite(s): ISDA 101."]
    assert isda_110["corequisite_codes"] == ["ISDA 101"]
    assert isda_110["prerequisite_codes"] == []

    isda_121 = prereq.parse_requisites(_course_text("ISDA 121"))
    assert isda_121["requisite_lines"] == [
        "Prerequisite(s): ISDA 20C and ISDA 20D.",
        "Pre/Corequisite(s): ISDA 101.",
    ]
    assert isda_121["prerequisite_codes"] == ["ISDA 20C", "ISDA 20D"]
    assert isda_121["pre_corequisite_codes"] == ["ISDA 101"]
    assert isda_121["corequisite_codes"] == []

    # No trailing period on the catalog page; still verbatim.
    isda_123 = prereq.parse_requisites(_course_text("ISDA 123"))
    assert isda_123["requisite_lines"] == ["Pre/Corequisite(s): ISDA 122"]
    assert isda_123["pre_corequisite_codes"] == ["ISDA 122"]


def test_alternatives_examples_and_ge_text_stay_literal():
    isda_122 = prereq.parse_requisites(_course_text("ISDA 122"))
    assert isda_122["prerequisite_codes"] == ["ISDA 120", "ISDA 121"]
    assert "alternatives (or)" in isda_122["conditions"]

    isda_180 = prereq.parse_requisites(_course_text("ISDA 180"))
    assert isda_180["requisite_lines"] == [
        "Prerequisite(s): Fundamental knowledge of Python (e.g., ISDA 20B, "
        "ISDA 111 in BS ISDA)."
    ]
    assert isda_180["prerequisite_codes"] == ["ISDA 20B", "ISDA 111"]
    assert "examples only (e.g.)" in isda_180["conditions"]

    # "GE Area 2", "1A", and "1B" are not course codes.
    isda_80 = prereq.parse_requisites(_course_text("ISDA 80"))
    assert isda_80["prerequisite_codes"] == []
    assert "GE requirement" in isda_80["conditions"]
    isda_111 = prereq.parse_requisites(_course_text("ISDA 111"))
    assert isda_111["prerequisite_codes"] == ["ISDA 20A", "ISDA 20B", "STAT 95"]
    assert isda_111["pre_corequisite_codes"] == ["ISDA 101"]

    # Two-letter suffixes from other departments are read whole.
    isda_100 = prereq.parse_requisites(_course_text("ISDA 100"))
    assert isda_100["prerequisite_codes"][:2] == ["LLD 100WB", "COMM 100W"]


def test_course_page_without_a_requisite_line_is_none_listed():
    parsed = prereq.parse_requisites(_course_text("ISDA 81"))
    assert parsed["status"] == "none listed"
    assert parsed["requisite_lines"] == []
    assert parsed["prerequisite_codes"] == []
    # Notes are kept verbatim but are never requisites.
    isda_101 = prereq.parse_requisites(_course_text("ISDA 101"))
    assert isda_101["status"] == "none listed"
    assert isda_101["notes"][0].startswith("Note(s): Must be taken before")


def test_shorthand_lists_and_label_variants():
    # Real iSchool page lines (MLIS Electives / MARA Required Courses).
    assert prereq.codes_in_requisite("INFO 200, 202, 204") == [
        "INFO 200", "INFO 202", "INFO 204",
    ]
    assert prereq.codes_in_requisite(
        "INFO 200, INFO 204 and INFO 260A or INFO 261A"
    ) == ["INFO 200", "INFO 204", "INFO 260A", "INFO 261A"]
    assert prereq.codes_in_requisite("MARA 200, 204, and six advanced courses.") == [
        "MARA 200", "MARA 204",
    ]
    parsed = prereq.parse_requisites(
        "Prerequisites: MARA 210 or MARA 211 or MARA 249\n"
        "Prerequisite: None\n"
    )
    assert parsed["prerequisite_codes"] == ["MARA 210", "MARA 211", "MARA 249"]
    assert parsed["requisite_lines"][1] == "Prerequisite: None"


def test_consent_and_declared_major_are_conditions_not_courses():
    # Wording of the catalog's consent and major restrictions (synthetic line).
    parsed = prereq.parse_requisites(
        "Prerequisite(s): INFO 202 or instructor consent. "
        "Allowed Declared Major: Library and Information Science.\n"
    )
    assert parsed["prerequisite_codes"] == ["INFO 202"]
    assert "instructor consent" in parsed["conditions"]
    assert "declared major" in parsed["conditions"]


# --------------------------------------------------------------- artifact


def test_checked_in_table_equals_the_generator_output():
    expected = prereq.table_json(prereq.build_table(prereq.load_corpus_pages(DOCS)))
    assert TABLE.read_text(encoding="utf-8") == expected, (
        "Run python scripts/build_prerequisite_table.py and commit the result."
    )


def test_table_has_every_catalog_course_page_in_the_corpus():
    courses = _courses()
    pages = [
        page for page in prereq.load_corpus_pages(DOCS)
        if prereq.is_catalog_course_page(page)
    ]
    codes = {page.text.split(" - ", 1)[0] for page in pages}
    assert codes == set(courses)
    for code, entry in courses.items():
        assert entry["code"] == code
        assert entry["status"] in {"listed", "none listed"}
        assert entry["source_url"].startswith("https://catalog.sjsu.edu/preview_course")
        assert entry["catalog_year"] == "2026-2027"
        assert entry["title"]
        assert (entry["status"] == "listed") == bool(entry["requisite_lines"])
        text = _course_text(code)
        for line in entry["requisite_lines"]:
            assert line in text.splitlines()
    assert courses["ISDA 131"]["requisite_lines"] == [ISDA_131_PREREQ]
    assert courses["ISDA 131"]["source_url"] == ISDA_131_URL
    assert courses["ISDA 81"]["status"] == "none listed"


def test_check_script_passes_on_the_committed_tree(capsys):
    assert build_prerequisite_table.main(["--check"]) == 0
    assert "is accounted for" in capsys.readouterr().out


# ----------------------------------------------------------- completeness


def test_completeness_passes_on_current_data_with_the_allowlist():
    pages = prereq.load_corpus_pages(DOCS)
    report = prereq.completeness_report(
        prereq.build_table(pages), pages, prereq.load_allowlist(ALLOWLIST)
    )
    assert report.ok, report.message()
    assert report.stale_allowlist == []
    # Courses outside the iSchool are reported, not required.
    assert "LLD 100WB" in report.external
    assert "ISDA 131" in report.covered


def test_allowlist_entries_are_reviewed_and_still_needed():
    allowlist = prereq.load_allowlist(ALLOWLIST)
    pages = prereq.load_corpus_pages(DOCS)
    report = prereq.completeness_report(prereq.build_table(pages), pages, {})
    for code, item in allowlist.items():
        assert item["reason"].strip()
        assert isinstance(item.get("temporary"), bool)
        assert code in report.missing, f"{code} no longer needs an allowlist entry"


def test_completeness_fails_with_a_clear_list_when_a_course_page_is_removed():
    pages = [page for page in prereq.load_corpus_pages(DOCS) if page.source_url != ISDA_131_URL]
    report = prereq.completeness_report(
        prereq.build_table(pages), pages, prereq.load_allowlist(ALLOWLIST)
    )
    assert not report.ok
    assert list(report.missing) == ["ISDA 131"]
    roadmap = "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19291"
    assert roadmap in report.missing["ISDA 131"]
    message = report.message()
    assert "1 course code(s)" in message and "  - ISDA 131 (referenced by" in message


def test_requirement_page_detection():
    page = prereq.CorpusPage
    assert prereq.is_requirement_page(page("a", "https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19291", "", ""))
    assert prereq.is_requirement_page(page("b", "https://ischool.sjsu.edu/mara-required-courses", "", ""))
    assert not prereq.is_requirement_page(page("c", "https://ischool.sjsu.edu/mlis-electives", "", ""))
    assert prereq.referenced_codes("Required Courses — MARA\n30 Units\nINFO285") == ["INFO 285"]


# ------------------------------------------------------------- lookup


@pytest.mark.parametrize(
    "question, program, expected",
    [
        ("Can I take ISDA 130 and 131 at the same time?", None, ["ISDA 130", "ISDA 131"]),
        ("Can I take ISDA 130/131 together?", None, ["ISDA 130", "ISDA 131"]),
        ("ISDA 130, 131 in one term?", None, ["ISDA 130", "ISDA 131"]),
        ("Can I take 130 and 131 at the same time?", "isda", ["ISDA 130", "ISDA 131"]),
        ("Can I take 130/131 together?", "isda", ["ISDA 130", "ISDA 131"]),
        ("What do I need before ISDA 141 and 140?", "mlis", ["ISDA 141", "ISDA 140"]),
    ],
)
def test_question_course_codes(question, program, expected):
    assert prereq.question_course_codes(question, program, set(_courses())) == expected


@pytest.mark.parametrize("program", [None, "both"])
def test_bare_numbers_need_one_selected_program(program):
    question = "Can I take 130 and 131 at the same time?"
    assert prereq.question_course_codes(question, program, set(_courses())) == []


def test_bare_numbers_that_are_quantities_or_unknown_are_ignored():
    known = set(_courses())
    assert prereq.question_course_codes("I have 120 units and a 3.5 GPA", "isda", known) == []
    assert prereq.question_course_codes("Is 999 offered in Fall 2026?", "isda", known) == []


def test_note_block_lists_each_named_course():
    text, entries = prereq.catalog_prerequisite_note(
        "Can I take 130 and 131 at the same time?", "isda", _courses()
    )
    assert [entry["code"] for entry in entries] == ["ISDA 130", "ISDA 131"]
    assert text.startswith("--- Catalog prerequisite data ---")
    assert ISDA_130_PREREQ in text and ISDA_131_PREREQ in text
    assert f"Source URL: {ISDA_131_URL}" in text
    assert "Prerequisite courses named: ISDA 130" in text
    none_text, _ = prereq.catalog_prerequisite_note("Prereqs for ISDA 81?", None, _courses())
    assert "Requisites: none listed" in none_text
    assert prereq.catalog_prerequisite_note("How do I apply?", None, _courses()) == ("", [])


# ------------------------------------------------------------ injection


class _UnrelatedRetrieval:
    """Retrieval that never returns either course page."""

    def __init__(self, docs_path, documents=None):
        self.docs_path = str(docs_path)
        self.documents = documents if documents is not None else [
            Document(
                page_content="Advising appointments are available online.",
                metadata={"source_url": "https://ischool.sjsu.edu/bsisda-advising", "programs": ["ISDA"]},
            )
        ]

    def retrieve_relevant_docs(self, query, k=6, program=None):
        return list(self.documents)


class _Echo:
    def __init__(self, answer):
        self.answer = answer
        self.payload = None

    def invoke(self, payload):
        self.payload = payload
        return type("Reply", (), {"content": self.answer})()


def _bot(processor, answer="ok"):
    bot = RAGAdvisingChatbot.__new__(RAGAdvisingChatbot)
    bot.knowledge_base = get_knowledge_base()
    bot.doc_processor = processor
    bot.chain = _Echo(answer)
    return bot


ANSWER = (
    "No. ISDA 131 lists \"Prerequisite(s): ISDA 130.\", so take ISDA 130 first. "
    f"[ISDA 131 - Information Architecture]({ISDA_131_URL})"
)


@pytest.mark.parametrize(
    "question, program",
    [
        ("Can I take ISDA 130 and 131 at the same time?", None),
        ("Can I take ISDA 130 and 131 at the same time?", "isda"),
        ("Can I take 130 and 131 at the same time?", "isda"),
        ("Can I take ISDA 130/131 together?", None),
    ],
)
def test_context_contains_both_entries_independent_of_retrieval(question, program):
    bot = _bot(_UnrelatedRetrieval(DOCS), ANSWER)
    result = bot.chat(question, program=program)
    context = bot.chain.payload["context"]
    assert "--- Catalog prerequisite data ---" in context
    block = context.split("--- Catalog prerequisite data ---", 1)[1].split(
        "--- End of catalog prerequisite data ---", 1
    )[0]
    assert "ISDA 130 - User Centered Interface Architecture" in block
    assert "ISDA 131 - Information Architecture" in block
    assert ISDA_131_PREREQ in block and ISDA_130_PREREQ in block
    assert f"Source URL: {ISDA_130_URL}" in block and f"Source URL: {ISDA_131_URL}" in block
    # The table's source link survives the link allowlist although retrieval
    # never returned the ISDA 131 page.
    assert f"({ISDA_131_URL})" in result["response"]


def test_table_alone_is_enough_context_when_retrieval_is_empty():
    bot = _bot(_UnrelatedRetrieval(DOCS, documents=[]), ANSWER)
    result = bot.chat("Can I take ISDA 130 and 131 at the same time?")
    assert result["source"] == "llm"
    assert ISDA_131_PREREQ in bot.chain.payload["context"]


def test_corpus_without_a_table_adds_no_block(tmp_path):
    (tmp_path / "advising_docs").mkdir()
    bot = _bot(_UnrelatedRetrieval(tmp_path / "advising_docs"))
    bot._unlisted_course_response = lambda message: ""
    bot.chat("Can I take ISDA 130 and 131 at the same time?")
    assert "Catalog prerequisite data" not in bot.chain.payload["context"]


@pytest.fixture(scope="module")
def corpus_processor(tmp_path_factory):
    from src.config import Config
    from src.document_processor import DocumentProcessor

    patch = pytest.MonkeyPatch()
    patch.setattr(Config, "EMBEDDING_PROVIDER", "hashing")
    patch.setattr(Config, "VECTOR_STORE_PATH", str(tmp_path_factory.mktemp("prereq-table")))
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    yield processor
    patch.undo()


@pytest.mark.parametrize("hybrid", ["true", "false"])
def test_regression_isda_130_and_131_same_time_on_both_retrieval_paths(
    corpus_processor, monkeypatch, hybrid
):
    monkeypatch.setenv("HYBRID_RETRIEVAL", hybrid)
    bot = _bot(corpus_processor, ANSWER)
    bot.chat("Can I take ISDA 130 and 131 at the same time?", program="isda")
    context = bot.chain.payload["context"]
    block = context.split("--- End of catalog prerequisite data ---", 1)[0]
    assert "ISDA 130 - User Centered Interface Architecture" in block
    assert "ISDA 131 - Information Architecture (2026-2027 catalog course page)" in block
    assert ISDA_131_PREREQ in block


def test_rule_11_points_at_the_catalog_prerequisite_data_block():
    rule = RAGAdvisingChatbot.SYSTEM_PROMPT.split("11. ", 1)[1].split("12. ", 1)[0]
    assert '"Catalog prerequisite data" block' in rule
    assert "verbatim" in rule
    assert '"None listed"' in rule
    assert "Never infer a requisite the sources do not state." in rule


# ---------------------------------------------------------- update job


def test_catalog_update_writes_the_prerequisite_table(tmp_path):
    repo = _repo(tmp_path)
    result = check_for_newer_catalog(repo, _fetch(_pages()), today=date(2027, 9, 1))
    assert result.status == "updated"
    assert "data/catalog_prerequisites.json" in result.files
    written = (repo / "data" / "catalog_prerequisites.json").read_text(encoding="utf-8")
    courses = json.loads(written)["courses"]
    assert set(courses) == {f"ISDA {100 + index}" for index in range(8)}
    assert courses["ISDA 100"]["status"] == "none listed"
    assert courses["ISDA 100"]["catalog_year"] == "2027-2028"
    # The generator reads the written tree back to the same bytes.
    regenerated = prereq.build_table_from_docs(repo / "data" / "advising_docs")
    # Generator defaults use the repo's identity; compare with the written map.
    identity = json.loads((repo / "data" / "catalog_identity.json").read_text(encoding="utf-8"))
    regenerated = prereq.build_table(
        prereq.load_corpus_pages(repo / "data" / "advising_docs"),
        years=identity["years_by_catoid"],
        current_catoid=identity["current_catoid"],
    )
    assert prereq.table_json(regenerated) == written


def _pages_with_unfetched_course():
    pages = _pages()
    url = "https://catalog.sjsu.edu/preview_program.php?catoid=24&poid=21000"
    courses = [(f"ISDA {100 + index}", 300000 + index) for index in range(4)]
    pages[url] = _program_html(
        "Information Science and Data Analytics, BS", courses
    ).replace("</ul>", "</ul><p>Capstone: ISDA 199 Senior Project (3 units)</p>")
    return pages


def test_catalog_update_refuses_to_write_when_a_referenced_course_has_no_page(tmp_path, caplog):
    repo = _repo(tmp_path)
    identity_before = (repo / "data" / "catalog_identity.json").read_text(encoding="utf-8")
    result = check_for_newer_catalog(
        repo, _fetch(_pages_with_unfetched_course()), today=date(2027, 9, 1)
    )
    assert result.status == "incomplete"
    assert result.missing_course_codes == ["ISDA 199"]
    assert "ISDA 199 (referenced by https://catalog.sjsu.edu/preview_program.php?catoid=24&poid=21000)" in result.message
    assert "No pull request" in result.message
    assert (repo / "data" / "catalog_identity.json").read_text(encoding="utf-8") == identity_before
    assert not (repo / "data" / "catalog_prerequisites.json").exists()
    assert list((repo / "data" / "advising_docs" / "meta").glob("web-*.yml")) == []


def test_reviewed_allowlist_lets_the_update_proceed(tmp_path):
    repo = _repo(tmp_path)
    (repo / "data" / prereq.ALLOWLIST_FILENAME).write_text(
        json.dumps({"codes": {"ISDA 199": {"reason": "fixture", "temporary": False}}}),
        encoding="utf-8",
    )
    result = check_for_newer_catalog(
        repo, _fetch(_pages_with_unfetched_course()), today=date(2027, 9, 1)
    )
    assert result.status == "updated"


def test_check_script_exits_nonzero_on_an_incomplete_refresh(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    result = CatalogCheckResult(
        status="incomplete", message="1 course code(s) ... ISDA 199",
        missing_course_codes=["ISDA 199"],
    )
    monkeypatch.setattr(check_sjsu_catalog, "check_for_newer_catalog", lambda root, client: result)
    report = tmp_path / "result.json"
    pr_body = tmp_path / "pr.md"
    assert check_sjsu_catalog.main(["--result", str(report), "--pr-body", str(pr_body)]) == 1
    assert json.loads(report.read_text(encoding="utf-8"))["missing_course_codes"] == ["ISDA 199"]
    assert not pr_body.exists()


def test_non_course_prerequisite_is_listed_without_codes():
    # INFO 202 (2026-2027 catalog): a requisite line that names no course.
    info_202 = prereq.parse_requisites(_course_text("INFO 202"))
    assert info_202["status"] == "listed"
    assert info_202["requisite_lines"] == ["Prerequisite(s): Demonstrated computer literacy."]
    assert info_202["prerequisite_codes"] == []
    assert prereq.parse_requisites(_course_text("INFO 203"))["status"] == "none listed"


def test_check_mode_fails_on_a_stale_table(tmp_path, capsys):
    data = tmp_path / "data"
    (data / "advising_docs" / "meta").mkdir(parents=True)
    (data / "advising_docs" / "txt").mkdir()
    (data / "catalog_identity.json").write_text(
        (ROOT / "data" / "catalog_identity.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (data / prereq.TABLE_FILENAME).write_text('{"courses": {}}\n', encoding="utf-8")
    assert build_prerequisite_table.main(["--check", "--repo-root", str(tmp_path)]) == 1
    assert "is out of date" in capsys.readouterr().out
    assert build_prerequisite_table.main(["--repo-root", str(tmp_path)]) == 0
    assert build_prerequisite_table.main(["--check", "--repo-root", str(tmp_path)]) == 0
