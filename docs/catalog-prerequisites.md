# Catalog prerequisite table

`data/catalog_prerequisites.json` is a structured copy of the requisite lines
on every committed catalog course page (`preview_course_nopop.php`) in
`data/advising_docs`. It is generated; do not edit it by hand.

```
python scripts/build_prerequisite_table.py          # regenerate, then check
python scripts/build_prerequisite_table.py --check  # CI: no writes, exit 1 on problems
```

## What an entry holds

```json
"ISDA 131": {
  "code": "ISDA 131",
  "title": "Information Architecture",
  "units": "3 unit(s)",
  "catalog_year": "2026-2027",
  "source_url": "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=202917",
  "doc_id": "web-c4f4b8d9d56cb77b",
  "status": "listed",
  "requisite_lines": ["Prerequisite(s): ISDA 130."],
  "prerequisite_codes": ["ISDA 130"],
  "corequisite_codes": [],
  "pre_corequisite_codes": [],
  "conditions": [],
  "notes": []
}
```

- `requisite_lines` are copied verbatim. Labels read: `Prerequisite(s)`,
  `Corequisite(s)`, `Pre/Corequisite(s)`, and the variants `Prerequisite:`,
  `Prerequisites:`, `Corequisite:`, `Pre-/Corequisite:`.
- `*_codes` list only course codes printed in that line. A department printed
  once before a list ("INFO 200, 202, 204") applies to each number. GE areas,
  "1A", and similar tokens are not codes. Nothing is inferred: "e.g.",
  "or", consent, and major restrictions stay in the raw line, and
  `conditions` only labels them (`alternatives (or)`, `examples only (e.g.)`,
  `instructor consent`, `declared major`, `GE requirement`, ...).
- `status: "none listed"` means the course page prints no requisite line.
- `notes` keeps `Note(s):` lines verbatim (timing advice such as "Should be
  taken in the first semester of junior year"). They are not requisites.
- When several catalog years have a page for one code, the current catalog
  (then the latest year) wins.

## Answer time

`RAGAdvisingChatbot` reads the table next to the corpus
(`<docs_path>/../catalog_prerequisites.json`). When a question names courses,
their entries go into the prompt as a `--- Catalog prerequisite data ---`
block, whatever retrieval returned, on the hybrid and vector-only paths.
Course names are read with the shared-prefix expansion from
`src/hybrid_retrieval.py` ("ISDA 130 and 131", "ISDA 130/131",
"ISDA 130, 131"). A bare number ("Can I take 130 and 131 together?") uses the
selected program's prefix (ISDA, INFO for MLIS, MARA, INFM for MSIS) and is
kept only if the table has that code; with no program or "both", bare numbers
are ignored. The entries' source URLs pass the reply link allowlist.
SYSTEM_PROMPT rule 11 tells the model to answer requisite and ordering
questions from this block.

## Completeness check

Every iSchool course code (ISDA, INFO, INFM, MARA, LIBR) printed on a program
requirements page or roadmap must have a table entry or a reviewed entry in
`data/catalog_prerequisites_allowlist.json` (`reason`, `temporary`).
Requirement pages are catalog program pages (`preview_program.php`, roadmaps
included) and the iSchool `*-required-courses` pages. Elective lists are not
requirement pages.

Codes from other departments (LLD 100WB, ENGL 1A, STAT 95, MATH 30, CS 22A,
...) are reported as `external` but not required. They are real courses, but
they appear on iSchool pages as GE, writing, or transfer options; their
requisites are set by those departments and their pages are outside the
iSchool corpus this bot answers from.

The check runs:

- in pytest (`tests/test_catalog_prerequisites.py`) and as its own step in
  `pr-tests.yml`;
- in the catalog update job. `check_for_newer_catalog` builds the table for
  the corpus as it would be after the refresh. Missing codes return status
  `incomplete` with the list, nothing is written, and
  `scripts/check_sjsu_catalog.py` exits 1, so no pull request is opened.
  After a successful refresh the job writes the table and
  `check-sjsu-catalog.yml` re-runs `--check` before pytest.

An allowlisted code that later gets a course page fails the check until it is
removed from the allowlist.
