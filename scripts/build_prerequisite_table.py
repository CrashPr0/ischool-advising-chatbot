"""Regenerate data/catalog_prerequisites.json and check it is complete.

Reads the committed catalog course pages in data/advising_docs, writes the
structured prerequisite table, and lists every iSchool course code that a
program requirements page or roadmap names without a catalog course page
(minus the reviewed allowlist in data/catalog_prerequisites_allowlist.json).

    python scripts/build_prerequisite_table.py          # write, then check
    python scripts/build_prerequisite_table.py --check  # CI: no writes

Exit status 1 when the committed table differs from the generator output,
when a referenced code is missing, or when an allowlisted code already has a
course page. No network access.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.catalog_prerequisites import (  # noqa: E402
    ALLOWLIST_FILENAME,
    TABLE_FILENAME,
    build_table,
    completeness_report,
    load_allowlist,
    load_corpus_pages,
    table_json,
    write_table,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; fail if the committed table is out of date.",
    )
    args = parser.parse_args(argv)
    repo = Path(args.repo_root)
    data = repo / "data"
    pages = load_corpus_pages(data / "advising_docs")
    table = build_table(pages)
    table_path = data / TABLE_FILENAME
    status = 0
    if args.check:
        current = table_path.read_text(encoding="utf-8") if table_path.is_file() else ""
        if current != table_json(table):
            print(
                f"{table_path.relative_to(repo)} is out of date. Run "
                "python scripts/build_prerequisite_table.py and commit the result."
            )
            status = 1
    else:
        write_table(table_path, table)
        print(f"Wrote {table_path.relative_to(repo)} ({len(table['courses'])} courses).")
    report = completeness_report(table, pages, load_allowlist(data / ALLOWLIST_FILENAME))
    print(report.message())
    if report.allowlisted:
        print("Allowlisted (see the allowlist for reasons): " + ", ".join(report.allowlisted))
    if not report.ok or report.stale_allowlist:
        status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
