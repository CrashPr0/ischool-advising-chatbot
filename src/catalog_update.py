"""Detect a newer SJSU catalog and refresh supported program snapshots.

The weekly workflow calls :func:`check_for_newer_catalog`. The web app does
not import this module. Fetching stays in the caller-supplied function so
tests can pass HTML fixtures and a live run is the only place that opens a
network connection.

A run writes files only after a newer academic year has been identified and
every discovered ISDA/MARA program page and ISDA course page has been read. Anything
else — the same catalog, a network error, a bot challenge, or HTML that no
longer matches the catalog menu — leaves the tree alone.
MSIS links are reported only, pending confirmation of the program's identity.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
import yaml

try:
    from .catalog_recency import academic_year_start, load_catalog_identity
except ImportError:  # pragma: no cover
    from catalog_recency import academic_year_start, load_catalog_identity

LOGGER = logging.getLogger(__name__)

CATALOG_ORIGIN = "https://catalog.sjsu.edu"
USER_AGENT = "iSchoolAdvisingBot/1.0 (+offline-ingestion)"
MIN_ISDA_COURSES = 8
MIN_ISDA_PROGRAMS = 1
MAX_COURSE_PAGES = 80
MAX_LISTING_FETCHES = 8
CATALOG_TOPICS = [
    "advising",
    "degree-requirements",
    "financial-aid",
    "registration",
]

_COURSE_TEXT = re.compile(
    r"^(?:INFO|ISDA|MARA|MSIS)\s+\d{2,3}[A-Za-z]?\b", re.IGNORECASE
)
_ISDA_COURSE_TEXT = re.compile(r"^ISDA\s+\d{2,3}[A-Za-z]?\b", re.IGNORECASE)
_SHOW_COURSE = re.compile(
    r"show(?:Hide)?Course\(\s*(?:this\s*,\s*)?'(\d+)'\s*,\s*'(\d+)'",
    re.IGNORECASE,
)
_CORE_HEADING = re.compile(r"Core Courses \(\d+ units\)")
_LABELED_FIELDS = (
    "Prerequisite(s)",
    "Pre/Corequisite(s)",
    "Corequisite(s)",
    "Grading",
    "Note(s)",
)
_NOISE_LINE = re.compile(
    r"^(back to top|print-friendly page\b|add to favorites\b|class schedule\b.*syllabus)",
    re.IGNORECASE,
)


@dataclass
class FetchResult:
    """One HTTP response, or the reason a fetch could not be used."""

    status: int
    body: str = ""
    error: str = ""
    blocked: bool = False


@dataclass(frozen=True)
class CatalogEdition:
    """One academic-year option from the catalog menu."""

    catoid: str
    year: str
    selected: bool
    archived: bool


@dataclass
class CatalogCheckResult:
    """Outcome of one catalog check. ``updated`` is the only status that writes."""

    status: str
    message: str
    catoid: str = ""
    year: str = ""
    previous_catoid: str = ""
    previous_year: str = ""
    files: list[str] = field(default_factory=list)
    msis_program_urls: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "catoid": self.catoid,
            "year": self.year,
            "previous_catoid": self.previous_catoid,
            "previous_year": self.previous_year,
            "files": list(self.files),
            "msis_program_urls": list(self.msis_program_urls),
        }


def _clean(value: str) -> str:
    text = (value or "").replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return text.strip()


def _unique(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def _absolute(href: str) -> str:
    raw = (href or "").strip()
    if not raw or raw.startswith("#") or raw.lower().startswith("javascript:"):
        return ""
    absolute = urljoin(CATALOG_ORIGIN + "/", raw)
    parsed = urlparse(absolute)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "catalog.sjsu.edu"
    ):
        return ""
    return absolute


def _query(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    found = parse_qs(parsed.query)
    return {key: values[-1] for key, values in found.items() if values}


def page_is_usable(result: FetchResult) -> bool:
    """True for a normal HTML page from the catalog. Challenges are not usable."""
    if result.error or result.blocked or result.status != 200:
        return False
    body = result.body or ""
    if not body.strip():
        return False
    lowered = body.casefold()
    if "gokuprops" in lowered or "awswafcookiedomainlist" in lowered:
        return False
    if "unable to locate the resource" in lowered:
        return False
    return True


def parse_published_catalogs(html: str) -> list[CatalogEdition]:
    """Read academic-year options from the catalog ``<select name="catalog">``."""
    soup = BeautifulSoup(html or "", "html.parser")
    select = soup.find("select", attrs={"name": "catalog"})
    if select is None:
        return []
    editions: list[CatalogEdition] = []
    for option in select.find_all("option"):
        catoid = str(option.get("value") or "").strip()
        if not catoid.isdigit():
            continue
        label = _clean(option.get_text(" ", strip=True))
        match = re.search(r"(20\d{2})-(20\d{2})", label)
        if not match:
            continue
        year = f"{match.group(1)}-{match.group(2)}"
        if academic_year_start(year) is None:
            continue
        editions.append(
            CatalogEdition(
                catoid=catoid,
                year=year,
                selected=option.has_attr("selected"),
                archived="archived catalog" in label.casefold(),
            )
        )
    return editions


def select_current_catalog(editions: list[CatalogEdition]) -> Optional[CatalogEdition]:
    """Return the single selected, non-archived academic catalog.

    Zero or several matches means the menu shape changed. The caller then
    leaves the repo alone instead of guessing.
    """
    chosen = [
        edition for edition in editions if edition.selected and not edition.archived
    ]
    if len(chosen) != 1:
        return None
    return chosen[0]


def is_newer_catalog(candidate_year: str, repo_year: str) -> bool:
    """True when ``candidate_year`` starts after ``repo_year``."""
    candidate = academic_year_start(candidate_year)
    current = academic_year_start(repo_year)
    if candidate is None or current is None:
        return False
    return candidate > current


def canonical_course_url(catoid: str, coid: str) -> str:
    return f"{CATALOG_ORIGIN}/preview_course_nopop.php?catoid={catoid}&coid={coid}"


def canonical_program_url(catoid: str, poid: str) -> str:
    return f"{CATALOG_ORIGIN}/preview_program.php?catoid={catoid}&poid={poid}"


def _course_url_from_anchor(anchor, catoid: str) -> str:
    onclick = anchor.get("onclick") or ""
    match = _SHOW_COURSE.search(onclick)
    if match and match.group(1) == catoid:
        return canonical_course_url(catoid, match.group(2))
    href = _absolute(anchor.get("href") or "")
    if not href or "preview_course" not in href:
        return ""
    query = _query(href)
    if query.get("catoid") != catoid or not str(query.get("coid") or "").isdigit():
        return ""
    return canonical_course_url(catoid, query["coid"])


def course_links(html: str, catoid: str) -> list[str]:
    """ISDA course preview URLs linked from a listing or program page."""
    soup = BeautifulSoup(html or "", "html.parser")
    found: list[str] = []
    for anchor in soup.find_all("a"):
        text = _clean(anchor.get_text(" ", strip=True))
        if not _ISDA_COURSE_TEXT.match(text):
            continue
        url = _course_url_from_anchor(anchor, catoid)
        if url:
            found.append(url)
    return _unique(found)


def _program_title_matches(title: str, program: str) -> bool:
    if program == "ISDA":
        return "information science and data analytics" in title.casefold()
    if program == "MARA":
        return bool(re.search(r"\barchives and records administration\b", title, re.I))
    if program == "MSIS":
        return bool(re.fullmatch(
            r"(?:information science,?\s+(?:MS|M\.S\.)|"
            r"(?:MS|M\.S\.|Master of Science)\s+(?:in\s+)?Information Science)",
            title.strip(), re.I,
        ))
    return False


def program_links(html: str, catoid: str, program: str = "ISDA") -> list[str]:
    """Official program links; MSIS discovery does not authorize ingestion."""
    soup = BeautifulSoup(html or "", "html.parser")
    found: list[str] = []
    for anchor in soup.find_all("a"):
        text = _clean(anchor.get_text(" ", strip=True)).casefold()
        if not _program_title_matches(text, program):
            continue
        href = _absolute(anchor.get("href") or "")
        if not href or "preview_program.php" not in href:
            continue
        query = _query(href)
        if query.get("catoid") != catoid or not str(query.get("poid") or "").isdigit():
            continue
        found.append(canonical_program_url(catoid, query["poid"]))
    return _unique(found)


def nav_url(html: str, label: str) -> str:
    """Absolute URL for a navbar label such as ``Course Descriptions``."""
    soup = BeautifulSoup(html or "", "html.parser")
    want = label.casefold()
    for anchor in soup.find_all("a"):
        if _clean(anchor.get_text(" ", strip=True)).casefold() != want:
            continue
        return _absolute(anchor.get("href") or "")
    return ""


def prefix_listing_links(html: str, catoid: str) -> list[str]:
    """Follow-up listing pages whose anchor text is the ISDA prefix."""
    soup = BeautifulSoup(html or "", "html.parser")
    found: list[str] = []
    for anchor in soup.find_all("a"):
        if _clean(anchor.get_text(" ", strip=True)).casefold() != "isda":
            continue
        url = _absolute(anchor.get("href") or "")
        if not url or "content.php" not in url:
            continue
        if f"catoid={catoid}" not in url and f"cur_cat_oid={catoid}" not in url:
            continue
        found.append(url)
    return _unique(found)


def next_listing_url(html: str, catoid: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for anchor in soup.find_all("a"):
        text = _clean(anchor.get_text(" ", strip=True)).casefold()
        if text not in {"next", "next page"}:
            continue
        url = _absolute(anchor.get("href") or "")
        if url and (f"catoid={catoid}" in url or f"cur_cat_oid={catoid}" in url):
            return url
    return ""


def _is_schedule_blurb(tag) -> bool:
    text = _clean(tag.get_text(" ", strip=True)).casefold()
    return "class schedule" in text and "syllabus" in text


def _normalize_course_title(raw: str) -> str:
    title = _clean(raw.replace("–", "-").replace("—", "-"))
    return re.sub(r"\s+-\s+", " - ", title, count=1)


def extract_course_snapshot(html: str, source_url: str, retrieved: str) -> str:
    """Turn an Acalog course preview into the committed snapshot text.

    An empty string means the page did not contain a supported iSchool course block.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    title_node = soup.find(id="course_preview_title")
    if title_node is None:
        return ""
    title = _normalize_course_title(title_node.get_text(" ", strip=True))
    if not _COURSE_TEXT.match(title):
        return ""
    container = title_node.parent
    children = list(container.children)
    try:
        start = children.index(title_node) + 1
    except ValueError:
        start = 0

    units = ""
    description_parts: list[str] = []
    repeatable = ""
    labels: dict[str, str] = {}
    seen_units = False
    index = start
    while index < len(children):
        child = children[index]
        name = getattr(child, "name", None)
        if name in {"script", "style"}:
            index += 1
            continue
        if name == "p" and _is_schedule_blurb(child):
            break
        if name == "div":
            break
        if name == "em":
            text = _clean(child.get_text(" ", strip=True))
            if re.fullmatch(r"\d+(?:-\d+)?", text) and not units:
                units = f"{text} unit(s)"
                seen_units = True
            elif text.lower().startswith("repeatable"):
                repeatable = text
            index += 1
            continue
        if name == "strong":
            label = _clean(child.get_text(" ", strip=True)).rstrip(":").strip()
            value_parts: list[str] = []
            cursor = index + 1
            while cursor < len(children):
                nxt = children[cursor]
                nxt_name = getattr(nxt, "name", None)
                if nxt_name in {"strong", "br"}:
                    break
                if (
                    nxt_name == "em"
                    and "repeatable" in _clean(nxt.get_text(" ", strip=True)).lower()
                ):
                    break
                if nxt_name == "p" and _is_schedule_blurb(nxt):
                    break
                if nxt_name == "div":
                    break
                if nxt_name is None:
                    value_parts.append(str(nxt))
                else:
                    value_parts.append(nxt.get_text(" ", strip=True))
                cursor += 1
            value = _clean(" ".join(value_parts))
            if label and value:
                labels[label] = value
            index = cursor
            continue
        if name is None and seen_units and not labels and not repeatable:
            text = _clean(str(child))
            if text:
                description_parts.append(text)
        index += 1

    if not units:
        return ""
    lines = [title, units]
    emitted: set[str] = set()
    for label in _LABELED_FIELDS:
        if label in labels:
            lines.append(f"{label}: {labels[label]}")
            emitted.add(label)
    for label, value in labels.items():
        if label not in emitted:
            lines.append(f"{label}: {value}")
    description = _clean(" ".join(description_parts))
    if description:
        lines.append(description)
    if repeatable:
        lines.append(repeatable)
    lines.append(f"Source URL: {source_url}")
    lines.append(f"Retrieved: {retrieved}")
    return "\n".join(lines) + "\n"


def course_listing_entries(html: str, prefix: str = "ISDA") -> list[tuple[str, str]]:
    """Return course code/title pairs from one official prefix listing.

    The listing is useful evidence that a number is (or is not) in a catalog
    even when an individual course preview was not captured. Entries stay in
    the order published by Acalog and duplicates are removed by course code.
    """
    normalized_prefix = str(prefix or "").strip().upper()
    if normalized_prefix not in {"INFO", "ISDA", "MARA", "MSIS"}:
        return []
    pattern = re.compile(
        rf"^({re.escape(normalized_prefix)}\s+\d{{2,3}}[A-Za-z]?)\s+-\s+(.+)$",
        re.IGNORECASE,
    )
    soup = BeautifulSoup(html or "", "html.parser")
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        text = _normalize_course_title(anchor.get_text(" ", strip=True))
        match = pattern.match(text)
        if not match:
            continue
        code = _clean(match.group(1)).upper()
        title = _clean(match.group(2))
        if code in seen or not title:
            continue
        seen.add(code)
        entries.append((code, title))
    return entries


def extract_course_listing_snapshot(
    html: str,
    source_url: str,
    retrieved: str,
    catalog_year: str,
    prefix: str = "ISDA",
) -> str:
    """Turn an Acalog prefix listing into a sourced catalog inventory."""
    normalized_prefix = str(prefix or "").strip().upper()
    entries = course_listing_entries(html, normalized_prefix)
    if not entries:
        return ""
    lines = [
        f"{catalog_year} Academic Catalog",
        f"{normalized_prefix} Course Descriptions",
        (
            f"The {catalog_year} catalog lists these {len(entries)} "
            f"{normalized_prefix} courses:"
        ),
        *[f"{code} - {title}" for code, title in entries],
        f"Source URL: {source_url}",
        f"Retrieved: {retrieved}",
    ]
    return "\n".join(lines) + "\n"


def _isolate_core_heading(text: str) -> str:
    return _CORE_HEADING.sub(lambda match: f"\n{match.group(0)}\n", text)


def extract_program_snapshot(
    html: str, source_url: str, retrieved: str, catalog_year: str,
    program: str = "ISDA",
) -> str:
    """Turn an Acalog program page into snapshot text, or return an empty string."""
    if program not in {"ISDA", "MARA"}:
        return ""  # MSIS content remains disabled pending program confirmation.
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    title_node = soup.find("h1")
    title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    node = (
        soup.find(id="acalog-content")
        or soup.find(id="acalog-page-content")
        or soup.find("main")
    )
    if node is None and title_node is not None:
        node = title_node.parent
    if node is None:
        return ""
    if program == "MARA" and not _program_title_matches(title, program):
        return ""
    if program == "ISDA" and (
        "information science and data analytics"
        not in (title + " " + node.get_text(" ", strip=True)).casefold()
    ):
        return ""
    body_lines = []
    for raw in node.get_text("\n", strip=True).splitlines():
        line = _clean(raw)
        if not line or _NOISE_LINE.match(line):
            continue
        if line == title:
            continue
        body_lines.append(line)
    body = _isolate_core_heading("\n".join(body_lines))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if program == "ISDA" and "ISDA" not in body and "ISDA" not in title:
        return ""
    lines = [f"{catalog_year} Academic Catalog"]
    if title:
        lines.append(title)
    lines.append(f"Source URL: {source_url}")
    lines.append("")
    if body:
        lines.append(body)
    lines.append(f"Retrieved: {retrieved}")
    return "\n".join(lines).rstrip() + "\n"


def core_heading(text: str) -> str:
    """The core-course heading when it is its own line, else an empty string."""
    for line in (text or "").splitlines():
        if _CORE_HEADING.fullmatch(line.strip()):
            return line.strip()
    return ""


def _doc_id(url: str) -> str:
    return f"web-{sha256(url.encode('utf-8')).hexdigest()[:16]}"


def _snapshot_record(
    url: str,
    text: str,
    title: str,
    catalog_year: str,
    programs: Optional[list[str]] = None,
) -> dict:
    content = text if text.endswith("\n") else text + "\n"
    record = {
        "url": url,
        "title": title or url,
        "text": content,
        "catalog_year": catalog_year,
        "doc_id": _doc_id(url),
        "content_sha256": sha256(content.encode("utf-8")).hexdigest(),
        "programs": list(programs or ["ISDA"]),
    }
    heading = core_heading(content)
    if "preview_program.php" in url and heading:
        record["atomic_section"] = heading
    elif title.endswith("Course Descriptions"):
        record["atomic_section"] = title
    return record


def _write_snapshot(docs_root: Path, record: dict, retrieved_at: str) -> None:
    doc_id = record["doc_id"]
    text_path = docs_root / "txt" / f"{doc_id}.txt"
    meta_path = docs_root / "meta" / f"{doc_id}.yml"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(record["text"], encoding="utf-8")
    metadata = {
        "doc_id": doc_id,
        "title": record["title"],
        "owner": "SJSU",
        "source_type": "webpage",
        "source_url": record["url"],
        "retrieved_at": retrieved_at,
        "content_sha256": record["content_sha256"],
        "programs": list(record.get("programs") or ["ISDA"]),
        "topics": list(CATALOG_TOPICS),
    }
    if "preview_program.php" in record["url"]:
        metadata["catalog_year"] = record["catalog_year"]
    if record.get("atomic_section"):
        metadata["atomic_section"] = record["atomic_section"]
    meta_path.write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _merge_manifest(docs_root: Path, records: list[dict]) -> None:
    manifest_path = docs_root / "scrape" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.is_file():
        try:
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            current = {}
    else:
        current = {}
    documents = current.get("documents") if isinstance(current, dict) else None
    if not isinstance(documents, list):
        documents = []
    by_url = {}
    for item in documents:
        if isinstance(item, dict) and isinstance(item.get("source_url"), str):
            by_url[item["source_url"]] = item
    for record in records:
        by_url[record["url"]] = {
            "doc_id": record["doc_id"],
            "source_url": record["url"],
            "title": record["title"],
            "content_sha256": record["content_sha256"],
            "programs": list(record.get("programs") or ["ISDA"]),
            "topics": list(CATALOG_TOPICS),
        }
    manifest = {
        "schema_version": 1,
        "documents": sorted(by_url.values(), key=lambda item: item["source_url"]),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _append_seeds(docs_root: Path, urls: list[str], year: str, catoid: str) -> None:
    seed_path = docs_root / "scrape" / "seed_urls.txt"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    existing = seed_path.read_text(encoding="utf-8") if seed_path.is_file() else ""
    missing = [url for url in urls if url not in existing]
    if not missing:
        return
    block = "\n".join(
        [
            "",
            f"# Official {year} SJSU catalog (catoid {catoid}), added by the catalog check.",
            *missing,
            "",
        ]
    )
    seed_path.write_text(existing.rstrip() + block, encoding="utf-8")


def _replace_assignment(text: str, name: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(name)}=.*$", re.MULTILINE)
    replacement = f"{name}={value}"
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return text.rstrip() + f"\n{replacement}\n"


def update_current_catalog_settings(
    repo_root: Path, catoid: str, year: str, editions: list[CatalogEdition]
) -> list[str]:
    """Point the committed defaults at ``catoid`` / ``year`` and extend the map."""
    written: list[str] = []
    identity_path = repo_root / "data" / "catalog_identity.json"
    current = load_catalog_identity(identity_path)
    years = dict(current["years_by_catoid"])
    for edition in editions:
        years[edition.catoid] = edition.year
    years[catoid] = year
    ordered = {key: years[key] for key in sorted(years, key=lambda item: int(item))}
    payload = {
        "current_catoid": catoid,
        "current_year": year,
        "years_by_catoid": ordered,
    }
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    written.append("data/catalog_identity.json")

    env_path = repo_root / ".env.example"
    if env_path.is_file():
        env_path.write_text(
            _replace_assignment(
                _replace_assignment(
                    env_path.read_text(encoding="utf-8"),
                    "CURRENT_CATALOG_CATOID",
                    catoid,
                ),
                "CURRENT_CATALOG_YEAR",
                year,
            ),
            encoding="utf-8",
        )
        written.append(".env.example")

    readme_path = repo_root / "README.md"
    if readme_path.is_file():
        readme = readme_path.read_text(encoding="utf-8")
        updated = re.sub(
            r"`CURRENT_CATALOG_CATOID` \(\d+\)",
            f"`CURRENT_CATALOG_CATOID` ({catoid})",
            readme,
            count=1,
        )
        updated = re.sub(
            r"`CURRENT_CATALOG_YEAR` \(\d{4}-\d{4}\)",
            f"`CURRENT_CATALOG_YEAR` ({year})",
            updated,
            count=1,
        )
        if updated != readme:
            readme_path.write_text(updated, encoding="utf-8")
            written.append("README.md")
    return written


def pull_request_body(result: CatalogCheckResult) -> str:
    """Markdown for the review pull request. Main is not mentioned as a push target."""
    year = result.year
    catoid = result.catoid
    previous = result.previous_year or "the previous catalog"
    return "\n".join(
        [
            "## Summary",
            (
                f"catalog.sjsu.edu now treats **{year}** (catoid {catoid}) as the "
                f"current academic catalog. The previous committed catalog was {previous}."
            ),
            "",
            (
                "This pull request refreshes ISDA program and course snapshots and linked MARA program snapshots "
                "from that catalog and updates `data/catalog_identity.json`, "
                "`.env.example`, and the catalog year named in the README. Older "
                "catalog snapshots stay in the corpus."
            ),
            "",
            (
                "Please review the snapshot text before merging. Merging to `main` "
                "deploys and rebuilds the advising index. If App Service has "
                "`CURRENT_CATALOG_CATOID` or `CURRENT_CATALOG_YEAR` set, that value "
                "overrides the file, so clear or update it when you accept this catalog."
            ),
            "",
            "MSIS content remains disabled pending program confirmation.",
            *[f"Detected MSIS program link (not fetched): {url}" for url in result.msis_program_urls],
            "",
            "## Test plan",
            (
                "- [ ] pytest passed in the Check SJSU catalog workflow before "
                "this pull request was opened"
            ),
            f"- [ ] ISDA program pages and course pages are present for catoid {catoid}",
            (
                "- [ ] `catalog_may_be_stale` on `/readyz` matches the year you "
                "expect after merge"
            ),
            "",
        ]
    )


def _unavailable(message: str, previous: dict) -> CatalogCheckResult:
    LOGGER.warning("%s", message)
    return CatalogCheckResult(
        status="unavailable",
        message=message,
        previous_catoid=str(previous.get("current_catoid") or ""),
        previous_year=str(previous.get("current_year") or ""),
    )


def _fetch_listing_courses(
    fetch, html: str, catoid: str, budget: list[int]
) -> list[str]:
    """Collect ISDA course URLs from a listing page and a few follow-up pages."""
    found = course_links(html, catoid)
    pending = prefix_listing_links(html, catoid)
    seen_pages: set[str] = set()
    while pending and budget[0] > 0 and len(found) < MAX_COURSE_PAGES:
        url = pending.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        budget[0] -= 1
        result = fetch(url)
        if not page_is_usable(result):
            LOGGER.warning("ISDA course listing could not be read: %s", url)
            continue
        found.extend(course_links(result.body, catoid))
        nxt = next_listing_url(result.body, catoid)
        if nxt and nxt not in seen_pages:
            pending.append(nxt)
    return _unique(found)


def check_for_newer_catalog(
    repo_root: Path,
    fetch: Callable[[str], FetchResult],
    *,
    today: Optional[date] = None,
) -> CatalogCheckResult:
    """Refresh ISDA and linked MARA pages for a newer catalog; only detect MSIS.

    ``fetch`` performs the HTTP call. A result other than ``updated`` means
    no files were written.
    """
    repo_root = Path(repo_root)
    identity = load_catalog_identity(repo_root / "data" / "catalog_identity.json")
    previous_catoid = identity["current_catoid"]
    previous_year = identity["current_year"]
    list_url = f"{CATALOG_ORIGIN}/misc/catalog_list.php?catoid={previous_catoid}"
    listed = fetch(list_url)
    if not page_is_usable(listed):
        index_url = f"{CATALOG_ORIGIN}/index.php?catoid={previous_catoid}"
        listed = fetch(index_url)
    if not page_is_usable(listed):
        return _unavailable(
            "catalog.sjsu.edu could not be read. No pull request.",
            identity,
        )
    editions = parse_published_catalogs(listed.body)
    current = select_current_catalog(editions)
    if current is None:
        return _unavailable(
            "The catalog menu did not identify one current academic year. "
            "No pull request.",
            identity,
        )
    if not is_newer_catalog(current.year, previous_year):
        message = (
            f"SJSU catalog is still {previous_year} (catoid {previous_catoid}). "
            "No pull request."
        )
        LOGGER.info("%s", message)
        return CatalogCheckResult(
            status="unchanged",
            message=message,
            catoid=previous_catoid,
            year=previous_year,
            previous_catoid=previous_catoid,
            previous_year=previous_year,
        )

    home = fetch(f"{CATALOG_ORIGIN}/index.php?catoid={current.catoid}")
    if not page_is_usable(home):
        return _unavailable(
            f"Found catalog {current.year} (catoid {current.catoid}) but its "
            "home page could not be read. No pull request.",
            identity,
        )
    programs_url = nav_url(home.body, "Academic Programs")
    courses_url = nav_url(home.body, "Course Descriptions")
    if not programs_url and not courses_url:
        return _unavailable(
            f"Found catalog {current.year} (catoid {current.catoid}) but the "
            "program and course menus were missing. No pull request.",
            identity,
        )

    program_urls: list[str] = []
    mara_program_urls: list[str] = []
    msis_program_urls: list[str] = []
    course_urls: list[str] = []
    listing_budget = [MAX_LISTING_FETCHES]
    if programs_url:
        programs_page = fetch(programs_url)
        if page_is_usable(programs_page):
            program_urls.extend(program_links(programs_page.body, current.catoid))
            mara_program_urls = program_links(programs_page.body, current.catoid, "MARA")
            msis_program_urls = program_links(programs_page.body, current.catoid, "MSIS")
            course_urls.extend(course_links(programs_page.body, current.catoid))
        else:
            LOGGER.warning("Academic Programs page could not be read: %s", programs_url)
    if courses_url:
        courses_page = fetch(courses_url)
        if page_is_usable(courses_page):
            course_urls.extend(
                _fetch_listing_courses(
                    fetch, courses_page.body, current.catoid, listing_budget
                )
            )
        else:
            LOGGER.warning(
                "Course Descriptions page could not be read: %s", courses_url
            )

    program_urls = _unique(program_urls)
    if len(program_urls) < MIN_ISDA_PROGRAMS:
        return _unavailable(
            f"Found catalog {current.year} (catoid {current.catoid}) but no ISDA "
            "program page was linked. No pull request.",
            identity,
        )
    if len(program_urls) + len(mara_program_urls) > 10:
        return _unavailable(
            f"Catalog {current.year} linked {len(program_urls) + len(mara_program_urls)} supported program "
            "pages, which is more than this check will refresh. No pull request.",
            identity,
        )

    retrieved = (today or date.today()).isoformat()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    records: list[dict] = []
    for url in program_urls + mara_program_urls:
        program = "MARA" if url in mara_program_urls else "ISDA"
        result = fetch(url)
        if not page_is_usable(result):
            return _unavailable(
                f"{program} program page could not be read ({url}). No pull request.",
                identity,
            )
        text = extract_program_snapshot(result.body, url, retrieved, current.year, program)
        if not text:
            return _unavailable(
                f"{program} program page did not match the expected shape ({url}). "
                "No pull request.",
                identity,
            )
        if program == "ISDA":
            course_urls.extend(course_links(result.body, current.catoid))
        title = ""
        for line in text.splitlines():
            if (
                line
                and not line.endswith("Academic Catalog")
                and not line.startswith("Source URL:")
            ):
                title = line
                break
        records.append(_snapshot_record(url, text, title, current.year, [program]))

    course_urls = [url for url in _unique(course_urls) if "preview_course" in url]
    if len(course_urls) < MIN_ISDA_COURSES:
        return _unavailable(
            f"Found catalog {current.year} (catoid {current.catoid}) but only "
            f"{len(course_urls)} ISDA course pages were linked. No pull request.",
            identity,
        )
    if len(course_urls) > MAX_COURSE_PAGES:
        return _unavailable(
            f"Catalog {current.year} linked {len(course_urls)} ISDA course "
            "pages, which is more than this check will refresh. No pull request.",
            identity,
        )

    for url in course_urls:
        result = fetch(url)
        if not page_is_usable(result):
            return _unavailable(
                f"ISDA course page could not be read ({url}). No pull request.",
                identity,
            )
        text = extract_course_snapshot(result.body, url, retrieved)
        if not text or not text.startswith("ISDA "):
            return _unavailable(
                f"ISDA course page did not match the expected shape ({url}). "
                "No pull request.",
                identity,
            )
        title = text.splitlines()[0]
        records.append(_snapshot_record(url, text, title, current.year))

    docs_root = repo_root / "data" / "advising_docs"
    for record in records:
        _write_snapshot(docs_root, record, retrieved_at)
    _merge_manifest(docs_root, records)
    _append_seeds(
        docs_root,
        [record["url"] for record in records],
        current.year,
        current.catoid,
    )
    settings = update_current_catalog_settings(
        repo_root, current.catoid, current.year, editions
    )
    message = f"Refreshed ISDA snapshots for {current.year} (catoid {current.catoid})."
    if mara_program_urls:
        message += f" Refreshed {len(mara_program_urls)} MARA program page(s)."
    message += (f" Detected {len(msis_program_urls)} MSIS program link(s); "
                "MSIS content remains disabled pending confirmation.")
    LOGGER.info("%s", message)
    files = [f"data/advising_docs/txt/{record['doc_id']}.txt" for record in records]
    files.extend(
        f"data/advising_docs/meta/{record['doc_id']}.yml" for record in records
    )
    files.extend(
        [
            "data/advising_docs/scrape/manifest.json",
            "data/advising_docs/scrape/seed_urls.txt",
            *settings,
        ]
    )
    return CatalogCheckResult(
        status="updated",
        message=message,
        catoid=current.catoid,
        year=current.year,
        previous_catoid=previous_catoid,
        previous_year=previous_year,
        files=files,
        msis_program_urls=msis_program_urls,
    )
