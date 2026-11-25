"""Structured prerequisite table built from the catalog course pages.

Every committed catalog course page (``preview_course_nopop.php``) prints its
requisites on labeled lines such as ``Prerequisite(s): ISDA 130.``. This
module turns those lines into one deterministic table,
``data/catalog_prerequisites.json``:

    course code -> title, units, catalog year, source URL, the requisite lines
    verbatim, and the course codes that literally appear in each line.

A course page with no requisite line gets ``"status": "none listed"``. Codes
are only what the text prints ("INFO 200, 202" is read as INFO 200 and
INFO 202); nothing is inferred.

Two consumers:

* Answer time. :func:`catalog_prerequisite_note` puts the entries for the
  courses a question names into the model context as a labeled "Catalog
  prerequisite data" block, independent of retrieval ranking.
* Completeness. :func:`completeness_report` lists every iSchool course code a
  program requirements page or roadmap names that has no table entry and is
  not in the reviewed allowlist (``data/catalog_prerequisites_allowlist.json``).
  The catalog update job and the test suite fail on that list.

No network access. ``scripts/build_prerequisite_table.py`` regenerates the
artifact; the catalog update job regenerates it after a refresh.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence
from urllib.parse import parse_qs, urlparse

import yaml

try:
    from .catalog_recency import load_catalog_identity, year_for_catoid
    from .hybrid_retrieval import (
        COURSE_DEPARTMENTS,
        course_codes_in_query,
        expand_shared_prefix_course_codes,
    )
    from .program_scope import normalize_program
except ImportError:  # pragma: no cover - src/ on sys.path
    from catalog_recency import load_catalog_identity, year_for_catoid
    from hybrid_retrieval import (
        COURSE_DEPARTMENTS,
        course_codes_in_query,
        expand_shared_prefix_course_codes,
    )
    from program_scope import normalize_program

ROOT = Path(__file__).resolve().parents[1]
TABLE_FILENAME = "catalog_prerequisites.json"
ALLOWLIST_FILENAME = "catalog_prerequisites_allowlist.json"
SCHEMA_VERSION = 1
NONE_LISTED = "none listed"
LISTED = "listed"

# Prefixes the completeness check requires a course page for. Courses from
# other departments (LLD 100WB, STAT 95, CS 22A, ...) appear on iSchool
# requirement pages and roadmaps as GE, writing, or transfer options; their
# requisites belong to those departments, so they are reported but not
# required. See docs/catalog-prerequisites.md.
COMPLETENESS_PREFIXES = COURSE_DEPARTMENTS

# Selected program -> prefix used for a bare course number ("130 and 131").
PROGRAM_PREFIX = {"isda": "ISDA", "mlis": "INFO", "mara": "MARA", "msis": "INFM"}
MAX_NOTE_COURSES = 8

# Requisite labels. Longer forms first so "Pre/Corequisite(s)" is not read as
# "Corequisite(s)". The iSchool pages also print "Prerequisites:" and
# "Prerequisite:"; catalog course pages print the "(s)" forms.
_REQUISITE_LINE = re.compile(
    r"^(?P<label>"
    r"Pre\s*-?\s*/\s*Co-?requisites?(?:\(s\))?"
    r"|Co-?requisites?(?:\(s\))?"
    r"|Pre-?requisites?(?:\(s\))?"
    r")\s*:\s*(?P<value>.*\S)\s*$",
    re.IGNORECASE,
)
_NOTE_LINE = re.compile(r"^Notes?(?:\(s\))?\s*:\s*\S", re.IGNORECASE)
_UNITS_LINE = re.compile(r"^\d+(?:-\d+)?\s+unit\(s\)$")
_HEADING = re.compile(
    r"^(?P<code>[A-Z]{2,5}\s+\d{1,3}[A-Z]{0,2})\s+-\s+(?P<title>.+?)\s*$"
)
# Upper-case department, a space, a number, and up to two suffix letters
# (ISDA 20A, LLD 100WB). Case-sensitive so "the 1A" or "Area 2" never match.
_CODE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]{2,5}) ?(\d{1,3}[A-Z]{0,2})(?![A-Za-z0-9])"
)
# "INFO 200, 202" / "MARA 200, 204, and six advanced courses": a department
# printed once before a list of bare numbers.
_REQ_LIST = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]{2,5})( ?)(\d{1,3}[A-Z]{0,2})(?![A-Za-z0-9])"
    r"((?:\s*(?:,\s*(?:and|or)\b|,|/|\band\b|\bor\b)\s*\d{2,3}[A-Z]{0,2}"
    r"(?![A-Za-z0-9]))+)"
)
_REQ_LIST_TAIL = re.compile(
    r"(\s*(?:,\s*(?:and|or)\b|,|/|\band\b|\bor\b)\s*)(\d{2,3}[A-Z]{0,2})"
)
# Code-like tokens that are not courses: GE areas and similar labels.
_NOT_COURSES = {"GE", "UD", "US", "AREA", "BUS", "HTML"}
_CONDITIONS = (
    ("instructor consent", re.compile(r"\b(?:instructor|department)\s+consent\b|\bconsent of\b", re.I)),
    ("declared major", re.compile(r"\bdeclared\s+major\b|\ballowed\s+declared\b", re.I)),
    ("upper division standing", re.compile(r"\bupper[\s-]division\s+standing\b", re.I)),
    ("GE requirement", re.compile(r"\bGE\b|\bGeneral Education\b", re.I)),
    ("or equivalent", re.compile(r"\bor\s+equivalent\b", re.I)),
    ("examples only (e.g.)", re.compile(r"\be\.g\.", re.I)),
    ("alternatives (or)", re.compile(r"\bor\b", re.I)),
)
_DEPARTMENT_CODE = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(COURSE_DEPARTMENTS) + r")\s*\d{2,3}[A-Za-z]{0,2}"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_BARE_NUMBER = re.compile(
    r"(?<![A-Za-z0-9$.:#-])(\d{2,3}[A-Za-z]?)(?![A-Za-z0-9%:])"
    r"(?!\s*(?:units?|credits?|hours?|years?|semesters?|weeks?|days?|"
    r"percent|students?|transferable|gpa)\b)",
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def normalize_code(department: str, number: str) -> str:
    return f"{department.strip().upper()} {number.strip().upper()}"


def code_sort_key(code: str) -> tuple:
    department, _, number = code.partition(" ")
    digits = re.match(r"\d+", number)
    return (department, int(digits.group(0)) if digits else 0, number)


def requisite_kind(label: str) -> str:
    """``prerequisite``, ``corequisite``, or ``pre_corequisite`` for a label."""
    folded = re.sub(r"[\s-]", "", label.casefold())
    if folded.startswith("pre/co"):
        return "pre_corequisite"
    if folded.startswith("co"):
        return "corequisite"
    return "prerequisite"


def codes_in_requisite(text: str) -> list[str]:
    """Course codes literally printed in one requisite line, in order.

    A department printed once before a list ("INFO 200, 202, 204") applies to
    each number in that list. Nothing else is added.
    """

    def expand(match: re.Match) -> str:
        department = match.group(1)
        tail = _REQ_LIST_TAIL.sub(
            lambda item: f"{item.group(1)}{department} {item.group(2)}",
            match.group(4),
        )
        return f"{department}{match.group(2)}{match.group(3)}{tail}"

    expanded = _REQ_LIST.sub(expand, text or "")
    found: list[str] = []
    for match in _CODE.finditer(expanded):
        if match.group(1).upper() in _NOT_COURSES:
            continue
        code = normalize_code(match.group(1), match.group(2))
        if code not in found:
            found.append(code)
    return found


def requisite_conditions(text: str) -> list[str]:
    """Non-course qualifiers present in a requisite line (labels only)."""
    return [label for label, pattern in _CONDITIONS if pattern.search(text or "")]


def parse_requisites(text: str) -> dict:
    """Parse the labeled requisite and note lines of one course page text."""
    lines: list[str] = []
    codes = {"prerequisite": [], "corequisite": [], "pre_corequisite": []}
    conditions: list[str] = []
    notes: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _REQUISITE_LINE.match(line)
        if match:
            lines.append(line)
            kind = requisite_kind(match.group("label"))
            for code in codes_in_requisite(match.group("value")):
                if code not in codes[kind]:
                    codes[kind].append(code)
            for condition in requisite_conditions(match.group("value")):
                if condition not in conditions:
                    conditions.append(condition)
        elif _NOTE_LINE.match(line):
            notes.append(line)
    return {
        "status": LISTED if lines else NONE_LISTED,
        "requisite_lines": lines,
        "prerequisite_codes": codes["prerequisite"],
        "corequisite_codes": codes["corequisite"],
        "pre_corequisite_codes": codes["pre_corequisite"],
        "conditions": conditions,
        "notes": notes,
    }


@dataclass
class CorpusPage:
    """One committed snapshot (txt plus meta) or a record about to be written."""

    doc_id: str
    source_url: str
    title: str
    text: str
    catalog_year: str = ""


def _catoid(url: str) -> str:
    values = parse_qs(urlparse(url or "").query).get("catoid") or [""]
    return values[-1]


def is_catalog_course_page(page: CorpusPage) -> bool:
    url = page.source_url or ""
    return "catalog.sjsu.edu" in url and "preview_course" in url


def is_requirement_page(page: CorpusPage) -> bool:
    """Program requirements pages and roadmaps.

    Catalog program pages (``preview_program.php``; roadmaps are program pages
    titled ``Roadmap: ...``) and the iSchool ``*-required-courses`` pages.
    Elective lists are not requirement pages.
    """
    url = page.source_url or ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "catalog.sjsu.edu" and "preview_program.php" in parsed.path:
        return True
    return host == "ischool.sjsu.edu" and parsed.path.rstrip("/").endswith(
        "-required-courses"
    )


def load_corpus_pages(docs_root: Path) -> list[CorpusPage]:
    """Every ``meta/*.yml`` with its ``txt`` file, sorted by doc_id."""
    docs_root = Path(docs_root)
    pages: list[CorpusPage] = []
    for meta_path in sorted((docs_root / "meta").glob("*.yml")):
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if not isinstance(meta, dict):
            continue
        doc_id = str(meta.get("doc_id") or meta_path.stem)
        text_path = docs_root / "txt" / f"{doc_id}.txt"
        if not text_path.is_file():
            continue
        pages.append(
            CorpusPage(
                doc_id=doc_id,
                source_url=str(meta.get("source_url") or ""),
                title=str(meta.get("title") or ""),
                text=text_path.read_text(encoding="utf-8"),
                catalog_year=str(meta.get("catalog_year") or ""),
            )
        )
    return pages


def _page_year(page: CorpusPage, years: dict) -> str:
    return page.catalog_year or year_for_catoid(_catoid(page.source_url), years)


def course_entry(page: CorpusPage, years: dict) -> Optional[dict]:
    """The table entry for one catalog course page, or None if it has no heading."""
    lines = [line.strip() for line in page.text.splitlines() if line.strip()]
    heading = _HEADING.match(lines[0]) if lines else None
    if heading is None:
        return None
    units = next((line for line in lines[1:4] if _UNITS_LINE.match(line)), "")
    code = _clean(heading.group("code")).upper()
    entry = {
        "code": code,
        "title": _clean(heading.group("title")),
        "units": units,
        "catalog_year": _page_year(page, years),
        "source_url": page.source_url,
        "doc_id": page.doc_id,
    }
    entry.update(parse_requisites(page.text))
    return entry


def _entry_rank(entry: dict, current_catoid: str) -> tuple:
    year = entry.get("catalog_year") or ""
    is_current = _catoid(entry.get("source_url", "")) == current_catoid
    return (is_current, year, entry.get("source_url", ""))


def build_table(
    pages: Iterable[CorpusPage],
    years: Optional[dict] = None,
    current_catoid: str = "",
) -> dict:
    """Deterministic table from the given pages. Latest catalog wins per code."""
    if years is None or not current_catoid:
        identity = load_catalog_identity()
        years = identity["years_by_catoid"] if years is None else years
        current_catoid = current_catoid or identity["current_catoid"]
    chosen: dict[str, dict] = {}
    for page in pages:
        if not is_catalog_course_page(page):
            continue
        entry = course_entry(page, years)
        if entry is None:
            continue
        held = chosen.get(entry["code"])
        if held is None or _entry_rank(entry, current_catoid) > _entry_rank(
            held, current_catoid
        ):
            chosen[entry["code"]] = entry
    courses = {code: chosen[code] for code in sorted(chosen, key=code_sort_key)}
    return {
        "schema_version": SCHEMA_VERSION,
        "description": (
            "Generated by scripts/build_prerequisite_table.py from the catalog "
            "course pages in data/advising_docs. Do not edit by hand. "
            "requisite_lines are verbatim; *_codes list only course codes "
            "printed in those lines; status 'none listed' means the course "
            "page prints no requisite line."
        ),
        "courses": courses,
    }


def build_table_from_docs(docs_root: Path, extra_pages: Sequence[CorpusPage] = (), **kwargs) -> dict:
    pages = {page.source_url: page for page in load_corpus_pages(docs_root)}
    for page in extra_pages:
        pages[page.source_url] = page
    return build_table(pages.values(), **kwargs)


def table_json(table: dict) -> str:
    return json.dumps(table, indent=2, ensure_ascii=False) + "\n"


def write_table(path: Path, table: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(table_json(table), encoding="utf-8")


# ---------------------------------------------------------------- completeness


def referenced_codes(text: str) -> list[str]:
    """Course codes printed on a requirement page, shared-prefix lists included."""
    expanded = expand_shared_prefix_course_codes(text or "", COMPLETENESS_PREFIXES)
    found: list[str] = []
    for match in _CODE.finditer(expanded):
        if match.group(1).upper() in _NOT_COURSES:
            continue
        code = normalize_code(match.group(1), match.group(2))
        if code not in found:
            found.append(code)
    return found


def load_allowlist(path: Path) -> dict[str, dict]:
    """Reviewed codes that may lack a course page, each with a reason."""
    path = Path(path)
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    codes = payload.get("codes") if isinstance(payload, dict) else None
    if not isinstance(codes, dict):
        raise ValueError(f"{path} must contain a 'codes' object")
    for code, item in codes.items():
        if not isinstance(item, dict) or not str(item.get("reason") or "").strip():
            raise ValueError(f"{path}: {code} needs a non-empty reason")
    return codes


@dataclass
class CompletenessReport:
    """Codes referenced by requirement pages compared with the table."""

    missing: dict[str, list[str]] = field(default_factory=dict)
    allowlisted: dict[str, list[str]] = field(default_factory=dict)
    stale_allowlist: list[str] = field(default_factory=list)
    external: dict[str, list[str]] = field(default_factory=dict)
    covered: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    def message(self) -> str:
        lines = []
        if self.missing:
            lines.append(
                f"{len(self.missing)} course code(s) referenced by program "
                "requirement pages or roadmaps have no catalog course page in "
                "the prerequisite table and are not allowlisted:"
            )
            for code in sorted(self.missing, key=code_sort_key):
                lines.append(f"  - {code} (referenced by {', '.join(self.missing[code])})")
        else:
            lines.append(
                "Every iSchool course code referenced by program requirement "
                f"pages or roadmaps is accounted for: {len(self.covered)} with a "
                f"catalog course page, {len(self.allowlisted)} allowlisted."
            )
        if self.stale_allowlist:
            lines.append(
                "Allowlisted codes that now have a course page (remove them "
                "from the allowlist): " + ", ".join(self.stale_allowlist)
            )
        return "\n".join(lines)


def completeness_report(
    table: dict,
    pages: Iterable[CorpusPage],
    allowlist: Optional[dict] = None,
) -> CompletenessReport:
    allowlist = allowlist or {}
    courses = table.get("courses", {})
    report = CompletenessReport()
    referenced: dict[str, list[str]] = {}
    for page in sorted(pages, key=lambda item: item.source_url):
        if not is_requirement_page(page):
            continue
        for code in referenced_codes(page.text):
            referenced.setdefault(code, [])
            if page.source_url not in referenced[code]:
                referenced[code].append(page.source_url)
    prefixes = set(COMPLETENESS_PREFIXES)
    for code in sorted(referenced, key=code_sort_key):
        sources = referenced[code]
        if code.split(" ", 1)[0] not in prefixes:
            report.external[code] = sources
        elif code in courses:
            report.covered.append(code)
        elif code in allowlist:
            report.allowlisted[code] = sources
        else:
            report.missing[code] = sources
    report.stale_allowlist = sorted(
        (code for code in allowlist if code in courses), key=code_sort_key
    )
    return report


# ----------------------------------------------------------------- answer time


def table_path_for_docs(docs_root) -> Path:
    """``data/catalog_prerequisites.json`` next to ``data/advising_docs``."""
    return Path(str(docs_root)).expanduser().resolve().parent / TABLE_FILENAME


@lru_cache(maxsize=4)
def _load_table_cached(path: str, mtime: float) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    courses = payload.get("courses") if isinstance(payload, dict) else None
    return courses if isinstance(courses, dict) else {}


def load_table_courses(path: Path) -> dict:
    """``code -> entry`` from a committed table, or {} when it is absent."""
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return {}
    return _load_table_cached(str(path), mtime)


def question_course_codes(query: str, program=None, known: Optional[set] = None) -> list[str]:
    """Course codes a question names, in order.

    Explicit codes and shared-prefix lists ("ISDA 130 and 131",
    "ISDA 130/131", "ISDA 130, 131") are always read. A bare number ("Can I
    take 130 and 131 together?") uses the selected program's prefix and is
    kept only when ``known`` (the table's codes) contains it.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    expanded = expand_shared_prefix_course_codes(query, COURSE_DEPARTMENTS)
    codes = course_codes_in_query(expanded, COURSE_DEPARTMENTS)
    prefix = PROGRAM_PREFIX.get(normalize_program(program) or "")
    if prefix and known is not None:
        # Blank out spans already read as codes so their numbers are not
        # read again as bare numbers.
        masked = _DEPARTMENT_CODE.sub(
            lambda match: " " * len(match.group(0)), expanded
        )
        for match in _BARE_NUMBER.finditer(masked):
            code = normalize_code(prefix, match.group(1))
            if code in known and code not in codes:
                codes.append(code)
    return codes


def format_entry(entry: dict) -> str:
    year = entry.get("catalog_year") or "catalog"
    lines = [
        f"{entry['code']} - {entry.get('title', '')} ({year} catalog course page)",
        f"Source URL: {entry.get('source_url', '')}",
    ]
    if entry.get("status") == NONE_LISTED:
        lines.append(
            "Requisites: none listed (the catalog course page prints no "
            "Prerequisite(s), Corequisite(s), or Pre/Corequisite(s) line)."
        )
    else:
        lines.extend(entry.get("requisite_lines") or [])
        for key, label in (
            ("prerequisite_codes", "Prerequisite courses named"),
            ("corequisite_codes", "Corequisite courses named"),
            ("pre_corequisite_codes", "Pre/corequisite courses named"),
        ):
            if entry.get(key):
                lines.append(f"{label}: {', '.join(entry[key])}")
    lines.extend(entry.get("notes") or [])
    return "\n".join(lines)


def catalog_prerequisite_note(
    query: str, program=None, courses: Optional[dict] = None
) -> tuple[str, list[dict]]:
    """The "Catalog prerequisite data" block for the courses a question names.

    Returns ``(text, entries)``. Text is "" when the question names no course
    in the table. ``entries`` lets the caller allow the entries' source links.
    """
    if not courses:
        return "", []
    codes = question_course_codes(query, program, set(courses))
    entries = [courses[code] for code in codes if code in courses][:MAX_NOTE_COURSES]
    if not entries:
        return "", []
    parts = [
        "--- Catalog prerequisite data ---",
        "Parsed from the official catalog course pages for the courses named "
        "in the question. Requisite lines are copied verbatim; the course "
        "lists show only codes printed in those lines. Use this data for "
        "prerequisite, corequisite, and course-order questions, and cite the "
        "Source URL.",
        "",
    ]
    for entry in entries:
        parts.append(format_entry(entry))
        parts.append("")
    parts.append("--- End of catalog prerequisite data ---")
    return "\n".join(parts) + "\n\n", entries
