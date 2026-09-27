"""Catalog year identity and current-catalog preference at retrieval time.

SJSU catalog snapshots are kept for every year that is in the corpus. Students
on older catalog rights can still be answered from those pages. When the
current catalog and an older catalog both describe the same course or program,
retrieval gives the current catalog the better slot. Registrar pages and
iSchool pages stay non-catalog. If one of them is in the top results and
gives a different title for a course the current catalog already lists there,
the current course page is pulled into that result set.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlparse

from langchain_core.documents import Document

LOGGER = logging.getLogger(__name__)

# Last resort when data/catalog_identity.json is missing or unreadable.
# Catoid numbers are not chronological. The live map is the JSON file, which
# the catalog check updates from the published catalog menu. Startup and each
# request read that file only; they do not call catalog.sjsu.edu.
_FALLBACK_YEARS = {
    "1": "2019-2020",
    "2": "2020-2021",
    "10": "MyRoadmaps",
    "12": "2021-2022",
    "13": "2022-2023",
    "14": "2023-2024",
    "15": "2024-2025",
    "17": "2025-2026",
    "23": "2026-2027",
}
_FALLBACK_CURRENT_CATALOG_YEAR = "2026-2027"
_FALLBACK_CURRENT_CATALOG_CATOID = "23"
# Month-day after which the next catalog year should already be current.
# SJSU fall instruction usually begins in the second half of August.
DEFAULT_CATALOG_STALE_CUTOFF = "08-20"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_CATALOG_HOST = "catalog.sjsu.edu"
_CATOID_PARAM = re.compile(r"[?&]catoid=(\d+)\b", re.IGNORECASE)
_SOURCE_URL_LINE = re.compile(
    r"^Source URL:\s*(https?://catalog\.sjsu\.edu/\S+)",
    re.IGNORECASE | re.MULTILINE,
)
_ACADEMIC_YEAR = re.compile(r"^20\d{2}-20\d{2}$")
_ACADEMIC_YEAR_LABEL = re.compile(
    r"\b(20\d{2}-20\d{2})\s+Academic Catalog\b",
    re.IGNORECASE,
)
_HEADING_COURSE = re.compile(
    r"^\s*([A-Za-z]{2,6})\s+(\d{2,3}[A-Za-z]?)\s*[-–—]\s+\S",
)
_TITLED_MENTION = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{2,6})\s+(\d{2,3}[A-Za-z]?)\s*[-–—]\s*([^,.\n\[\]]+)",
)
_HEADING_TITLE = re.compile(
    r"^([A-Za-z]{2,6})\s+(\d{2,3}[A-Za-z]?)\s*[-–—]\s*(.+)$",
)
_YEAR_LINE = re.compile(r"^\s*20\d{2}-20\d{2}\s+Academic Catalog\s*$", re.IGNORECASE)
_CUTOFF = re.compile(r"^(\d{2})-(\d{2})$")


def catalog_identity_path() -> Path:
    """Committed catalog menu: catoid, academic year, and which one is current."""
    return Path(__file__).resolve().parents[1] / "data" / "catalog_identity.json"


def academic_year_start(year: str) -> Optional[int]:
    """Return the first year of ``YYYY-YYYY`` when the span is exactly one year."""
    if not isinstance(year, str) or not _ACADEMIC_YEAR.fullmatch(year.strip()):
        return None
    start_text, end_text = year.strip().split("-")
    start, end = int(start_text), int(end_text)
    if end != start + 1:
        return None
    return start


def load_catalog_identity(path: Optional[Path] = None) -> dict:
    """Read the committed catalog map. A bad file falls back to the last known map.

    The returned year table is a copy. Callers can add a catoid in tests
    without changing the fallback used on the next failed read.
    """
    fallback = {
        "current_catoid": _FALLBACK_CURRENT_CATALOG_CATOID,
        "current_year": _FALLBACK_CURRENT_CATALOG_YEAR,
        "years_by_catoid": dict(_FALLBACK_YEARS),
    }
    identity_path = catalog_identity_path() if path is None else path
    try:
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if path is None and identity_path.is_file():
            LOGGER.warning(
                "Catalog identity file %s could not be read (%s); using the built-in map",
                identity_path,
                error,
            )
        return fallback
    if not isinstance(payload, dict):
        return fallback
    years = payload.get("years_by_catoid")
    if not isinstance(years, dict):
        return fallback
    cleaned: dict[str, str] = {}
    for key, value in years.items():
        catoid = str(key).strip()
        label = value.strip() if isinstance(value, str) else ""
        if catoid.isdigit() and label:
            cleaned[catoid] = label
    current_catoid = str(payload.get("current_catoid") or "").strip()
    current_year = str(payload.get("current_year") or "").strip()
    if (
        not cleaned
        or not current_catoid.isdigit()
        or academic_year_start(current_year) is None
    ):
        return fallback
    mapped = cleaned.get(current_catoid, "")
    if academic_year_start(mapped) is not None:
        current_year = mapped
    else:
        cleaned[current_catoid] = current_year
    return {
        "current_catoid": current_catoid,
        "current_year": current_year,
        "years_by_catoid": cleaned,
    }


_LOADED_IDENTITY = load_catalog_identity()
CATALOG_YEAR_BY_CATOID = _LOADED_IDENTITY["years_by_catoid"]
DEFAULT_CURRENT_CATALOG_YEAR = _LOADED_IDENTITY["current_year"]
DEFAULT_CURRENT_CATALOG_CATOID = _LOADED_IDENTITY["current_catoid"]


def catalog_recency_rerank_enabled() -> bool:
    """Return whether current-catalog chunks should outrank older ones.

    Unset enables the preference. Empty and any value other than true/yes/on/1
    keep fused order. Read on each call so tests can flip it without a restart.
    """
    raw = os.getenv("CATALOG_RECENCY_RERANK", "true")
    return raw.strip().lower() in _TRUE_VALUES


def current_catalog_year() -> str:
    """Academic year treated as current when a chunk has no catoid."""
    raw = os.getenv("CURRENT_CATALOG_YEAR", DEFAULT_CURRENT_CATALOG_YEAR)
    year = raw.strip() if isinstance(raw, str) else ""
    return year or DEFAULT_CURRENT_CATALOG_YEAR


def current_catalog_catoid() -> str:
    """Catoid treated as the current SJSU catalog.

    The environment wins, then ``data/catalog_identity.json``, then the
    built-in fallback. This does not contact the catalog site.
    """
    raw = os.getenv("CURRENT_CATALOG_CATOID", DEFAULT_CURRENT_CATALOG_CATOID)
    catoid = raw.strip() if isinstance(raw, str) else ""
    return catoid or DEFAULT_CURRENT_CATALOG_CATOID


def year_for_catoid(catoid: str, years: Optional[dict] = None) -> str:
    """Academic year for a catoid in the committed map.

    Labels that are not ``YYYY-YYYY`` (such as MyRoadmaps) are ignored so the
    caller can fall back to a year printed on the snapshot.
    """
    table = CATALOG_YEAR_BY_CATOID if years is None else years
    raw = table.get(str(catoid).strip(), "") if isinstance(table, dict) else ""
    if not isinstance(raw, str) or academic_year_start(raw) is None:
        return ""
    return raw


def catalog_stale_cutoff() -> tuple[int, int]:
    """Month and day after which the new academic year should be current.

    ``CATALOG_STALE_CUTOFF`` is ``MM-DD``. An empty or invalid value uses
    August 20.
    """
    raw = os.getenv("CATALOG_STALE_CUTOFF", DEFAULT_CATALOG_STALE_CUTOFF)
    match = _CUTOFF.fullmatch(raw.strip() if isinstance(raw, str) else "")
    if not match:
        return (8, 20)
    month, day = int(match.group(1)), int(match.group(2))
    try:
        # 2000 is a leap year, so February 29 is a real cutoff and February 31 is not.
        date(2000, month, day)
    except ValueError:
        return (8, 20)
    return month, day


def expected_catalog_year(today: Optional[date] = None) -> str:
    """Academic year that should be current on ``today``.

    Before the cutoff in calendar year Y the expected catalog is (Y-1)-Y.
    On the cutoff and after it, the expected catalog is Y-(Y+1).
    """
    current = today or date.today()
    month, day = catalog_stale_cutoff()
    start = (
        current.year
        if (current.month, current.day) >= (month, day)
        else current.year - 1
    )
    return f"{start}-{start + 1}"


def catalog_staleness_report(today: Optional[date] = None) -> dict:
    """Compare the configured catalog year with the year fall term should use.

    ``catalog_may_be_stale`` is true only when the configured year is an older
    academic year than the one expected for ``today``. A newer configured year
    (published early) is not stale. An unreadable year is not flagged.
    """
    configured = current_catalog_year()
    expected = expected_catalog_year(today)
    configured_start = academic_year_start(configured)
    expected_start = academic_year_start(expected)
    stale = (
        configured_start is not None
        and expected_start is not None
        and configured_start < expected_start
    )
    return {
        "catalog_may_be_stale": stale,
        "catalog_year": configured,
        "catalog_catoid": current_catalog_catoid(),
        "expected_catalog_year": expected,
    }


def catalog_prompt_note(today: Optional[date] = None) -> str:
    """Tell the model which catalog year is configured.

    When the date is past the fall cutoff and the configured year is older,
    the note asks for that year to be named and for the student to hear that
    a newer catalog may have been published.
    """
    report = catalog_staleness_report(today)
    year = report["catalog_year"]
    note = (
        f"The configured current SJSU catalog is {year}. "
        "When you use a catalog source, cite the catalog year printed on that source."
    )
    if report["catalog_may_be_stale"]:
        expected = report["expected_catalog_year"]
        note += (
            f" Fall term for {expected} has started and the configured catalog "
            f"is still {year}. Tell the student the catalog year the sources use "
            "and that a newer catalog may have been published."
        )
    return note


def _is_catalog_url(url: str) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlparse(url.strip())
    return (
        parsed.scheme in {"http", "https"} and parsed.netloc.casefold() == _CATALOG_HOST
    )


def _catoid_from_url(url: str) -> str:
    match = _CATOID_PARAM.search(url or "")
    return match.group(1) if match else ""


def catalog_source_url(metadata: dict, text: str = "") -> str:
    """Return this chunk's own catalog URL, never a link inside another page.

    An iSchool or registrar page keeps its own ``source_url``. Catalog links
    in that page's body do not make the chunk a catalog snapshot.
    """
    raw = metadata.get("source_url") if isinstance(metadata, dict) else None
    url = raw.strip() if isinstance(raw, str) else ""
    if url:
        return url if _is_catalog_url(url) else ""
    match = _SOURCE_URL_LINE.search(text or "")
    if not match:
        return ""
    found = match.group(1).rstrip(").,]}>\"'")
    return found if _is_catalog_url(found) else ""


def _explicit_year(metadata: dict) -> str:
    raw = metadata.get("catalog_year") if isinstance(metadata, dict) else None
    if not isinstance(raw, str):
        return ""
    year = raw.strip()
    return year if _ACADEMIC_YEAR.fullmatch(year) else ""


def _year_in_text(text: str) -> str:
    match = _ACADEMIC_YEAR_LABEL.search(text or "")
    return match.group(1) if match else ""


def _chunk_is_current(catoid: str, year: str) -> bool:
    """Catoid decides currency. The year is the fallback when catoid is absent."""
    if catoid:
        return catoid == current_catalog_catoid()
    return bool(year) and year == current_catalog_year()


def apply_catalog_metadata(metadata: dict, text: str = "") -> dict:
    """Stamp ``catoid``, ``catalog_year``, and ``is_current`` on catalog chunks.

    Non-catalog metadata is returned unchanged. A catoid in the committed
    catalog map supplies the academic year. A year already stored on the
    snapshot, or a ``YYYY-YYYY Academic Catalog`` line, fills in catoids that
    map does not name. ``is_current`` follows ``CURRENT_CATALOG_CATOID`` at
    call time. This does not contact the catalog site.
    """
    if not isinstance(metadata, dict):
        return metadata
    url = catalog_source_url(metadata, text)
    if not url:
        return metadata
    catoid = _catoid_from_url(url)
    if not catoid:
        return metadata
    year = year_for_catoid(catoid) or _explicit_year(metadata) or _year_in_text(text)
    metadata["catoid"] = catoid
    if year:
        metadata["catalog_year"] = year
    metadata["is_current"] = _chunk_is_current(catoid, year)
    return metadata


def metadata_is_current(metadata: dict) -> bool:
    """Read ``is_current`` after a Chroma round trip (bool, int, or string)."""
    if not isinstance(metadata, dict) or "is_current" not in metadata:
        return False
    value = metadata.get("is_current")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_VALUES
    return False


def _heading_course(text: str) -> str:
    match = _HEADING_COURSE.match(text or "")
    if not match:
        return ""
    return f"{match.group(1).casefold()}{match.group(2).casefold()}"


def _first_content_line(content: str) -> str:
    for line in (content or "").splitlines():
        stripped = line.strip()
        if not stripped or _YEAR_LINE.match(stripped):
            continue
        if stripped.lower().startswith("source url:"):
            continue
        return stripped
    return ""


def _program_label(title: str, content: str) -> str:
    raw = title.strip() if isinstance(title, str) else ""
    if not raw or _ACADEMIC_YEAR.fullmatch(raw):
        raw = _first_content_line(content)
    raw = _YEAR_LINE.sub("", raw).strip()
    raw = re.sub(r"\s+", " ", raw).strip().casefold()
    return raw


def catalog_subject_key(content: str, metadata: dict) -> str:
    """Group chunks that describe the same course or the same program.

    Course pages key on the heading course (``ISDA 150``), not on every
    prerequisite mentioned in the body. Program pages key on the program
    title, so the on-campus BS and the SJSU Online BS stay distinct.
    """
    if not isinstance(metadata, dict):
        return ""
    if not metadata.get("catoid") and not metadata.get("catalog_year"):
        return ""
    url = (
        metadata.get("source_url")
        if isinstance(metadata.get("source_url"), str)
        else ""
    )
    title = metadata.get("title") if isinstance(metadata.get("title"), str) else ""
    course = _heading_course(title) or _heading_course(_first_content_line(content))
    if "preview_course" in url or _heading_course(title):
        return f"course:{course}" if course else ""
    if "preview_program" in url:
        label = _program_label(title, content)
        return f"program:{label}" if label else ""
    if course:
        return f"course:{course}"
    return ""


def reorder_ids(
    doc_ids: Sequence[str], by_id: dict[str, tuple[str, dict]]
) -> list[str]:
    """Give current-catalog chunks the better slots of the same course or program.

    Non-catalog positions stay where fusion put them. Older catalog chunks
    keep their slots when the current catalog has no chunk for that subject
    in this candidate list. With ``CATALOG_RECENCY_RERANK`` off, the order is
    unchanged.
    """
    ordered = [doc_id for doc_id in doc_ids if doc_id]
    if not catalog_recency_rerank_enabled() or len(ordered) < 2:
        return ordered

    stamped: list[tuple[str, bool]] = []
    for doc_id in ordered:
        row = by_id.get(doc_id)
        if row is None:
            stamped.append(("", False))
            continue
        content, metadata = row
        meta = apply_catalog_metadata(dict(metadata or {}), content or "")
        key = catalog_subject_key(content or "", meta)
        stamped.append((key, metadata_is_current(meta) if key else False))

    groups: dict[str, list[int]] = {}
    for index, (key, _current) in enumerate(stamped):
        if key:
            groups.setdefault(key, []).append(index)

    result = list(ordered)
    for indices in groups.values():
        current = [index for index in indices if stamped[index][1]]
        older = [index for index in indices if not stamped[index][1]]
        if not current or not older:
            continue
        slots = sorted(indices)
        for slot, source in zip(slots, current + older):
            result[slot] = ordered[source]
    return result


def prepare_retrieved_document(document: Document) -> Document:
    """Copy catalog identity onto a hit without mutating the cached chunk."""
    content = getattr(document, "page_content", "") or ""
    metadata = apply_catalog_metadata(
        dict(getattr(document, "metadata", None) or {}), content
    )
    return Document(page_content=content, metadata=metadata)


def reorder_documents(documents: Sequence[Document]) -> list[Document]:
    """Stamp catalog identity, then apply :func:`reorder_ids`."""
    prepared = [prepare_retrieved_document(document) for document in documents]
    if not catalog_recency_rerank_enabled() or len(prepared) < 2:
        return prepared
    by_id = {
        str(index): (document.page_content, document.metadata)
        for index, document in enumerate(prepared)
    }
    ordered = reorder_ids([str(index) for index in range(len(prepared))], by_id)
    return [prepared[int(doc_id)] for doc_id in ordered]


def source_type_label(metadata: Optional[dict]) -> str:
    """Name a non-catalog source so the model does not treat it as the catalog."""
    metadata = metadata or {}
    raw = metadata.get("source_url")
    url = raw.strip().casefold() if isinstance(raw, str) else ""
    if "ischool.sjsu.edu" in url:
        kind = "iSchool web page"
    elif "registrar" in url:
        kind = "Registrar web page"
    elif url:
        kind = "SJSU web page"
    else:
        kind = "Advising document"
    return f"{kind} (not the official catalog)"


def document_heading(index: int, metadata: Optional[dict]) -> str:
    """Context label. Catalog hits name their year; other hits name the source type."""
    metadata = metadata or {}
    source = metadata.get("source", "unknown")
    year = metadata.get("catalog_year")
    if isinstance(year, str) and year.strip():
        return f"[Document {index} — {source} | Catalog year: {year.strip()}]"
    return f"[Document {index} — {source} | Source type: {source_type_label(metadata)}]"


def _course_code(department: str, number: str) -> str:
    return f"{department.casefold()}{number.casefold()}"


def _normalize_title(title: str) -> str:
    text = (
        (title or "").casefold().replace("–", " ").replace("—", " ").replace("-", " ")
    )
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _heading_parts(text: str) -> tuple[str, str]:
    """Return ``(course code, normalized title)`` from a heading line."""
    line = ""
    for raw in (text or "").splitlines():
        if raw.strip():
            line = raw.strip()
            break
    match = _HEADING_TITLE.match(line)
    if not match:
        return "", ""
    return _course_code(match.group(1), match.group(2)), _normalize_title(
        match.group(3)
    )


_COURSE_LIST = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{2,6})\s+"
    r"(\d{2,3}[A-Za-z]?(?:\s*,\s*(?:and\s+)?\d{2,3}[A-Za-z]?)*)",
    re.IGNORECASE,
)


def _course_codes_in(text: str) -> set[str]:
    """Course codes, including ``ISDA 80, 81, 150`` lists that share one prefix."""
    codes: set[str] = set()
    for match in _COURSE_LIST.finditer(text or ""):
        for number in re.findall(r"\d{2,3}[A-Za-z]?", match.group(2)):
            codes.add(_course_code(match.group(1), number))
    return codes


def _titled_mentions(text: str) -> dict[str, str]:
    """Map a course code to the title a non-catalog page printed after a dash."""
    found: dict[str, str] = {}
    for match in _TITLED_MENTION.finditer(text or ""):
        title = _normalize_title(match.group(3))
        if not title:
            continue
        found[_course_code(match.group(1), match.group(2))] = title
    return found


def _current_course_pages(
    records: Sequence[tuple[str, str, dict]],
) -> dict[str, Document]:
    """One current-catalog chunk per course, preferring the chunk with the heading."""
    pages: dict[str, Document] = {}
    for _doc_id, content, metadata in records:
        text = content or ""
        meta = apply_catalog_metadata(dict(metadata or {}), text)
        if not metadata_is_current(meta):
            continue
        code, title = _heading_parts(
            meta.get("title") if isinstance(meta.get("title"), str) else ""
        )
        if not code:
            code, title = _heading_parts(text)
        if not code or not title:
            continue
        key = catalog_subject_key(text, meta)
        if key and not key.startswith("course:"):
            continue
        document = Document(page_content=text, metadata=meta)
        current = pages.get(code)
        starts = bool(_heading_parts(text)[0])
        if current is None or starts:
            pages[code] = document
    return pages


def _is_current_catalog_document(document: Document) -> bool:
    metadata = getattr(document, "metadata", None) or {}
    return bool(metadata.get("catoid")) and metadata_is_current(metadata)


def include_authoritative_course_pages(
    documents: Sequence[Document],
    records: Sequence[tuple[str, str, dict]],
    k: int,
) -> list[Document]:
    """Pull a current course page in when a non-catalog hit contradicts its title.

    The current catalog chunk already in ``documents`` must mention the course
    number, and a non-catalog chunk must give a different dashed title. The
    pulled page replaces the lowest non-catalog hit that is not the
    contradicting one, so the elective list and other current-catalog chunks
    stay. ``CATALOG_RECENCY_RERANK=false`` leaves the list unchanged.
    """
    chosen = list(documents)
    if not catalog_recency_rerank_enabled() or k <= 0 or not records:
        return chosen

    catalog_codes: set[str] = set()
    for document in chosen:
        if _is_current_catalog_document(document):
            catalog_codes.update(_course_codes_in(document.page_content))
    if not catalog_codes:
        return chosen

    pages = _current_course_pages(records)
    wanted: list[Document] = []
    trigger_ids: set[int] = set()
    for index, document in enumerate(chosen):
        if _is_current_catalog_document(document):
            continue
        for code, title in _titled_mentions(document.page_content).items():
            if code not in catalog_codes:
                continue
            page = pages.get(code)
            if page is None:
                continue
            _code, official = _heading_parts(
                page.metadata.get("title")
                if isinstance(page.metadata.get("title"), str)
                else page.page_content
            )
            if not official or official == title:
                continue
            trigger_ids.add(index)
            if any(page.page_content in hit.page_content for hit in chosen + wanted):
                continue
            if any(hit.page_content == page.page_content for hit in wanted):
                continue
            wanted.append(page)
    if not wanted:
        return chosen

    insert_at = min(trigger_ids) if trigger_ids else len(chosen)
    for offset, page in enumerate(wanted):
        chosen.insert(insert_at + offset, page)
    protected = set(range(insert_at, insert_at + len(wanted)))
    protected.update(
        index + len(wanted) if index >= insert_at else index for index in trigger_ids
    )

    while len(chosen) > k:
        evict = None
        for index in range(len(chosen) - 1, -1, -1):
            if index in protected or _is_current_catalog_document(chosen[index]):
                continue
            evict = index
            break
        if evict is None:
            # Making room would drop a current-catalog chunk. Keep the fused list.
            return list(documents)
        del chosen[evict]
        protected = {
            (item if item < evict else item - 1) for item in protected if item != evict
        }
    return chosen
