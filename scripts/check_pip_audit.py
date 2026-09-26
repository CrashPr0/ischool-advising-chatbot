#!/usr/bin/env python3
"""Report pip-audit findings and fail only when a fixed release exists."""

from __future__ import annotations

import json
import sys
from typing import Any


def load_report(payload: Any) -> list[dict]:
    """Accept either JSON shape pip-audit has emitted."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        dependencies = payload.get("dependencies", [])
        if isinstance(dependencies, list):
            return [item for item in dependencies if isinstance(item, dict)]
    raise ValueError(
        "pip-audit JSON must be a list or an object with a dependencies list"
    )


def classify_advisories(payload: Any) -> tuple[list[str], list[str]]:
    """Return unique ``(fixable, unfixed)`` advisory lines.

    pip-audit sometimes repeats the same id inside one package. An advisory
    with a non-empty ``fix_versions`` list is fixable. An empty list means
    upstream has not published a patched release yet.
    """
    fixable: list[str] = []
    unfixed: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for dependency in load_report(payload):
        name = str(dependency.get("name") or "?")
        version = str(dependency.get("version") or "?")
        for vulnerability in dependency.get("vulns") or []:
            if not isinstance(vulnerability, dict):
                continue
            vuln_id = str(vulnerability.get("id") or "unknown")
            key = (name, version, vuln_id)
            if key in seen:
                continue
            seen.add(key)
            fixes = [str(item) for item in (vulnerability.get("fix_versions") or [])]
            summary = f"{name}=={version} {vuln_id} fix={', '.join(fixes) or 'none'}"
            if fixes:
                fixable.append(summary)
            else:
                unfixed.append(summary)
    return fixable, unfixed


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: check_pip_audit.py REPORT.json", file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as handle:
        payload = json.load(handle)
    fixable, unfixed = classify_advisories(payload)
    if unfixed:
        print("Unfixed advisories (reported, not failing):")
        for line in unfixed:
            print(f"  {line}")
    else:
        print("No unfixed advisories.")
    if fixable:
        print("Fixable advisories:")
        for line in fixable:
            print(f"  {line}")
        return 1
    print("No fixable advisories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
