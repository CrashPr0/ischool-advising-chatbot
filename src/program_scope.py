"""Lightweight program hints for retrieval; ambiguous queries keep their ranking."""

import json
import re

PROGRAM_KEYS = frozenset({"isda", "mlis", "mara", "msis"})


def expand_program_tokens(text: str) -> str:
    """Connect new program acronyms to the words used on official pages."""
    for acronym, name in (
        ("mara", "archives records administration"),
        ("msis", "informatics"),
    ):
        text = re.sub(
            rf"\b{acronym}\b",
            lambda match: f"{match.group(0)} {name}",
            text,
            flags=re.I,
        )
    return text


def normalize_program(program):
    """Ignore missing or invalid selections, including non-string JSON values."""
    return (
        program
        if isinstance(program, str) and program in PROGRAM_KEYS | {"both"}
        else None
    )


def selected_programs(query: str, program=None) -> set[str]:
    program = normalize_program(program)
    # "Not sure / both" keeps the pre-dropdown behavior, keyword hints included.
    if program in PROGRAM_KEYS:
        return {program.upper()}
    return query_programs(query)


def query_programs(query: str) -> set[str]:
    programs = set()
    if re.search(
        r"\b(?:mara\d*[a-z]?|(?:master\s+of\s+)?archives\s+and\s+records\s+administration)\b",
        query, re.I,
    ):
        programs.add("MARA")
    if re.search(
        r"\b(?:msis\d*[a-z]?|infm\s*\d{3}[a-z]?|informatics|m\.?s\.?\s+(?:in\s+)?information\s+science|"
        r"master(?:['’]s)?\s+(?:of\s+science\s+in|of|in)\s+information\s+science)\b",
        query, re.I,
    ):
        programs.add("MSIS")
    if re.search(
        r"\b(?:mlis|library\s+(?:and\s+information\s+)?science|master(?:['’]s|s)?)\b",
        query,
        re.I,
    ):
        # A named master's program must not also imply MLIS.
        if not programs or re.search(
            r"\b(?:mlis|library\s+(?:and\s+information\s+)?science)\b", query, re.I
        ):
            programs.add("MLIS")
    if re.search(
        r"\b(?:isda\d*[a-z]?|bsisda|undergraduate|bachelor(?:['’]s|s)?)\b", query, re.I
    ):
        programs.add("ISDA")
    return programs


def metadata_programs(metadata: dict) -> set[str]:
    value = metadata.get("programs", [])
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = re.split(r"[,\s]+", value)
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {str(program).strip().upper() for program in value}


def filter_program_documents(documents, program=None):
    """An explicit program selection admits only chunks tagged for that program."""
    program = normalize_program(program)
    if program not in PROGRAM_KEYS:
        return list(documents)
    return [
        doc for doc in documents
        if program.upper() in metadata_programs(doc.metadata or {})
    ]


def program_document_priority(metadata: dict, query: str, wanted: set[str]):
    """Prefer the selected program, then titles matching a clear question intent.

    Shared graduate pages may mention MARA only in their opening paragraph.
    Their admission/application titles remain useful evidence for later chunks.
    """
    programs = metadata_programs(metadata)
    scope = 0 if programs & wanted else (1 if not programs else 2)
    title = str(metadata.get("title", ""))
    intent = None
    if re.search(r"\b(?:admission|admissions|gpa|gre)\b", query, re.I):
        intent = r"\badmissions?\b"
    elif re.search(r"\b(?:apply|application)\b", query, re.I) and not re.search(
        r"\b(?:graduate|graduation|candidacy|award)\b", query, re.I
    ):
        intent = r"\b(?:application|apply)\b"
    courses = re.findall(r"\b(MARA|MSIS|INFM|INFO|ISDA)\s*(\d{2,3}[a-z]?)\b", query, re.I)
    if wanted <= {"MARA", "MSIS"} and not intent and not courses and re.search(
        r"\b(?:core\s+courses?|required\s+(?:core\s+)?(?:courses?|units))\b",
        query, re.I,
    ):
        intent = r"\brequired\s+courses\b"
    title_match = bool(intent and re.search(intent, title, re.I)) or any(
        re.search(rf"\b{department}\s*{number}\b", title, re.I)
        for department, number in courses
    )
    return scope, 0 if title_match else 1


def prefer_program_documents(documents, query: str, program=None):
    """Stable preference within candidates, retaining untagged fallback evidence.

    An explicit selection overrides query hints; both keeps the original order. Catalog
    recency ordering within each program is preserved.
    """
    wanted = selected_programs(query, program)
    if len(wanted) != 1:
        return list(documents)

    return sorted(documents, key=lambda doc: program_document_priority(doc.metadata or {}, query, wanted))
