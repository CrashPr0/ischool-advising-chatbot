"""Catalog roadmaps for BS ISDA course-planning questions.

"I want to graduate in two years. How should I plan my courses?" is answered
by the catalog roadmap's semester plan. Ranking alone can leave that plan
behind FAQ text that only shares "graduate" or "years", so a planning
question pins the plan chunk(s) of the matching roadmap. The roadmap is
chosen by the number of years in the question (2, 3, or 4), uses the 4-year
roadmap for a freshman, and otherwise defaults to the 2-Year SJSU Online
roadmap. Only indexed current-catalog roadmap chunks
are used; nothing is written into the corpus. No network call.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from langchain_core.documents import Document

try:
    from .catalog_recency import apply_catalog_metadata, metadata_is_current
    from .program_scope import metadata_programs, normalize_program, query_programs
except ImportError:  # pragma: no cover - src/ on sys.path
    from catalog_recency import apply_catalog_metadata, metadata_is_current
    from program_scope import metadata_programs, normalize_program, query_programs

_NUMBER = r"(?:[234]|two|three|four)"
_PLANNING_QUERY = re.compile(
    r"\broad\s*maps?\b"
    rf"|\b{_NUMBER}[\s-]*years?\s+(?:plan|roadmap|pathway|program|schedule|track)\b"
    rf"|\b(?:graduate|finish|complete|done)\b[^.?!]{{0,40}}?\bin\s+{_NUMBER}\s+years?\b"
    r"|\bplan(?:ning)?\s+(?:out\s+)?(?:my\s+|the\s+|our\s+)?"
    r"(?:courses|classes|schedule|semesters?)\b"
    r"|\b(?:courses|classes)\b[^.?!]{0,30}?\b(?:each|every|per|by)\s+semester\b"
    r"|\bsemester[\s-]+by[\s-]+semester\b",
    re.IGNORECASE,
)
# Phrases that name the roadmap itself or a plan length, so a question with
# no program selected may still be read as an ISDA roadmap question.
_EXPLICIT_ROADMAP = re.compile(
    rf"\broad\s*maps?\b|\b{_NUMBER}[\s-]*years?\b", re.IGNORECASE
)
_YEARS_IN_QUERY = re.compile(rf"\b({_NUMBER})[\s-]*years?\b", re.IGNORECASE)
_FRESHMAN = re.compile(r"\bfreshm[ae]n\b|\bfirst[\s-]+year\s+student", re.IGNORECASE)
_YEAR_WORDS = {"2": 2, "two": 2, "3": 3, "three": 3, "4": 4, "four": 4}
_TITLE_YEARS = re.compile(r"\((\d)-Year\b", re.IGNORECASE)
_HEADER_YEARS = re.compile(r"\b(\d)-year roadmap\b", re.IGNORECASE)
_YEAR_LINE = re.compile(r"^Year (\d)$", re.MULTILINE)
_TRANSFER_UNITS = re.compile(r"\bat least 60 transferable\b", re.IGNORECASE)
DEFAULT_ROADMAP_YEARS = 2


def is_roadmap_query(query: str, program=None) -> bool:
    """True for a BS ISDA course-planning or roadmap question.

    With ISDA selected, any planning phrase counts. With no program (or
    "both"), the question must also name a roadmap or a plan length and must
    not name only another program.
    """
    if not isinstance(query, str) or not _PLANNING_QUERY.search(query):
        return False
    program = normalize_program(program)
    if program == "isda":
        return True
    if program not in {None, "both"}:
        return False
    named = query_programs(query)
    if named and "ISDA" not in named:
        return False
    return bool("ISDA" in named or _EXPLICIT_ROADMAP.search(query))


def roadmap_years_in_query(query: str) -> int:
    """2, 3, or 4 from the question; 4 for a freshman; the 2-year roadmap otherwise."""
    match = _YEARS_IN_QUERY.search(query or "")
    if not match:
        return 4 if _FRESHMAN.search(query or "") else DEFAULT_ROADMAP_YEARS
    return _YEAR_WORDS.get(match.group(1).casefold(), DEFAULT_ROADMAP_YEARS)


def _title(metadata: dict) -> str:
    raw = metadata.get("title")
    return raw if isinstance(raw, str) else ""


def roadmap_years(content: str, metadata: dict) -> Optional[int]:
    """Plan length from the roadmap title, or from its retrieval header."""
    match = _TITLE_YEARS.search(_title(metadata)) or _HEADER_YEARS.search(
        content or ""
    )
    return int(match.group(1)) if match else None


def _current_isda(content: str, metadata: dict) -> Optional[dict]:
    meta = apply_catalog_metadata(dict(metadata or {}), content or "")
    if "ISDA" not in metadata_programs(meta) or not metadata_is_current(meta):
        return None
    return meta


def is_roadmap_plan_chunk(content: str, metadata: dict) -> bool:
    """A current ISDA roadmap chunk that carries a ``Year N`` semester plan."""
    if not _title(metadata).casefold().startswith("roadmap:"):
        return False
    return bool(_YEAR_LINE.search(content or "")) and bool(
        _current_isda(content, metadata)
    )


def include_roadmap_chunks(
    documents: Sequence[Document],
    records: Sequence[tuple[str, str, dict]],
    query: str,
    program=None,
    k: int = 8,
) -> list[Document]:
    """Put the matching roadmap's semester plan first for a planning question.

    For a roadmap whose title names SJSU Online, the current SJSU Online
    program chunk that states the 60 transferable unit admission requirement
    follows the plan, when it is indexed. Other questions, or an index with
    no roadmap for that length, keep ``documents`` unchanged.
    """
    chosen = list(documents)
    if k <= 0 or not records or not is_roadmap_query(query, program):
        return chosen
    want = roadmap_years_in_query(query)
    plan: list[tuple[int, Document]] = []
    online = False
    for _doc_id, content, metadata in records:
        if not is_roadmap_plan_chunk(content, metadata or {}):
            continue
        if roadmap_years(content, metadata or {}) != want:
            continue
        first_year = int(_YEAR_LINE.search(content).group(1))
        meta = apply_catalog_metadata(dict(metadata or {}), content or "")
        plan.append((first_year, Document(page_content=content, metadata=meta)))
        online = online or "sjsu online" in _title(metadata or {}).casefold()
    if not plan:
        return chosen
    pinned = [document for _year, document in sorted(plan, key=lambda item: item[0])]
    if online:
        for _doc_id, content, metadata in records:
            meta = _current_isda(content, metadata or {})
            if (
                meta
                and "preview_program" in str(meta.get("source_url") or "")
                and not _title(meta).casefold().startswith("roadmap:")
                and "sjsu online" in _title(meta).casefold()
                and _TRANSFER_UNITS.search(content or "")
            ):
                pinned.append(Document(page_content=content, metadata=meta))
                break
    pinned = pinned[:k]
    pinned_text = {document.page_content for document in pinned}
    # A hit that already holds a pinned chunk keeps its own metadata.
    existing = {doc.page_content: doc for doc in chosen if doc.page_content in pinned_text}
    pinned = [existing.get(document.page_content, document) for document in pinned]
    rest = [doc for doc in chosen if doc.page_content not in pinned_text]
    return (pinned + rest)[:k]


ROADMAP_PROMPT_NOTE = (
    "Roadmap instruction: a catalog roadmap is in the sources below. Answer a "
    "course-planning question with its semester-by-semester sequence (each Year "
    "and Fall/Spring semester, with the courses and units exactly as published) "
    "and cite the roadmap's Source URL. The plan length is the one in the "
    "roadmap's title and Year headings; do not restate the page's generic "
    "\"four years\" sentence as the length of a 2-Year or 3-Year roadmap. If the "
    "sources state the SJSU Online admission requirement (for example, at least "
    "60 transferable units), say that the 2-Year plan assumes it; do not add "
    "requirements the sources do not state. Say that a roadmap is a sample plan "
    "to use with the catalog, and that the student should confirm their own plan "
    "with [BSISDA Advising](https://ischool.sjsu.edu/bsisda-advising).\n\n"
)


def roadmap_prompt_note(query: str, documents: Sequence, program=None) -> str:
    """Source-grounded answer guidance when a roadmap plan is in the context."""
    if not is_roadmap_query(query, program):
        return ""
    for document in documents or []:
        content = getattr(document, "page_content", "") or ""
        metadata = getattr(document, "metadata", None) or {}
        if isinstance(metadata, dict) and is_roadmap_plan_chunk(content, metadata):
            return ROADMAP_PROMPT_NOTE
    return ""
