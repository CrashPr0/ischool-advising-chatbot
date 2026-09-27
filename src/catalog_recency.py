"""Catalog year identity and current-catalog preference at retrieval time.

SJSU catalog snapshots are kept for every year that is in the corpus. Students
on older catalog rights can still be answered from those pages. When the
current catalog and an older catalog both describe the same course or program,
retrieval gives the current catalog the better slot. Registrar pages and
iSchool pages are not catalog snapshots and are left unchanged.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Sequence
from urllib.parse import urlparse

from langchain_core.documents import Document

# Published catalog list at catalog.sjsu.edu/misc/catalog_list.php?catoid=23
# (read 2026-09-27). Catoid numbers are not chronological.
CATALOG_YEAR_BY_CATOID = {
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

DEFAULT_CURRENT_CATALOG_YEAR = "2026-2027"
DEFAULT_CURRENT_CATALOG_CATOID = "23"

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
_YEAR_LINE = re.compile(r"^\s*20\d{2}-20\d{2}\s+Academic Catalog\s*$", re.IGNORECASE)


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
    """Catoid treated as the current SJSU catalog. Default is 23 (2026-2027)."""
    raw = os.getenv("CURRENT_CATALOG_CATOID", DEFAULT_CURRENT_CATALOG_CATOID)
    catoid = raw.strip() if isinstance(raw, str) else ""
    return catoid or DEFAULT_CURRENT_CATALOG_CATOID


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

    Non-catalog metadata is returned unchanged. A known catoid supplies the
    academic year from the catalog list. A year already stored on the snapshot,
    or a ``YYYY-YYYY Academic Catalog`` line, fills in catoids that list does
    not name. ``is_current`` follows ``CURRENT_CATALOG_CATOID`` at call time.
    """
    if not isinstance(metadata, dict):
        return metadata
    url = catalog_source_url(metadata, text)
    if not url:
        return metadata
    catoid = _catoid_from_url(url)
    if not catoid:
        return metadata
    year = (
        CATALOG_YEAR_BY_CATOID.get(catoid)
        or _explicit_year(metadata)
        or _year_in_text(text)
    )
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


def document_heading(index: int, metadata: Optional[dict]) -> str:
    """Context label. Catalog hits name their academic year; others do not."""
    metadata = metadata or {}
    source = metadata.get("source", "unknown")
    year = metadata.get("catalog_year")
    if isinstance(year, str) and year.strip():
        return f"[Document {index} — {source} | Catalog year: {year.strip()}]"
    return f"[Document {index} — {source}]"
