"""Lightweight program hints for retrieval; ambiguous queries keep their ranking."""

import json
import re

PROGRAM_KEYS = frozenset({"isda", "mlis", "mara", "msis"})


def expand_program_tokens(text: str) -> str:
    """Connect new program acronyms to the words used on official pages."""
    for acronym, name in (
        ("mara", "archives records administration"),
        ("msis", "information science"),
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
        r"\b(?:msis\d*[a-z]?|m\.?s\.?\s+(?:in\s+)?information\s+science|"
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


def prefer_program_documents(documents, query: str, program=None):
    """Stable preference within candidates, retaining untagged fallback evidence.

    An explicit selection overrides query hints; both keeps the original order. Catalog
    recency ordering within each program is preserved.
    """
    wanted = selected_programs(query, program)
    if len(wanted) != 1:
        return list(documents)

    def priority(document):
        programs = metadata_programs(document.metadata or {})
        return 0 if programs & wanted else (1 if not programs else 2)

    return sorted(documents, key=priority)
