"""Lightweight program hints for retrieval; ambiguous queries keep their ranking."""

import json
import re


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


def prefer_program_documents(documents, query: str):
    """Stable preference within candidates, retaining untagged fallback evidence.

    Queries naming both programs (or neither) keep the original order. Catalog
    recency ordering within each program is preserved.
    """
    wanted = query_programs(query)
    if len(wanted) != 1:
        return list(documents)

    def priority(document):
        programs = metadata_programs(document.metadata or {})
        return 0 if programs & wanted else (1 if not programs else 2)

    return sorted(documents, key=priority)
