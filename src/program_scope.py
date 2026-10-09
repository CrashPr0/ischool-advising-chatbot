"""Lightweight program hints for retrieval; ambiguous queries keep their ranking."""

import json
import re


def normalize_program(program):
    """Ignore missing or invalid selections, including non-string JSON values."""
    return (
        program
        if isinstance(program, str) and program in {"isda", "mlis", "both"}
        else None
    )


def selected_programs(query: str, program=None) -> set[str]:
    program = normalize_program(program)
    # "Not sure / both" keeps the pre-dropdown behavior, keyword hints included.
    if program in {"isda", "mlis"}:
        return {program.upper()}
    return query_programs(query)


def query_programs(query: str) -> set[str]:
    programs = set()
    if re.search(r"\b(?:mlis|library\s+(?:and\s+information\s+)?science|master(?:['’]s|s)?)\b", query, re.I):
        programs.add("MLIS")
    if re.search(r"\b(?:isda\d*[a-z]?|bsisda|undergraduate|bachelor(?:['’]s|s)?)\b", query, re.I):
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
