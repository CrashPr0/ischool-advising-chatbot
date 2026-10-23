# Advising documents (RAG)

This folder is the **source** for Retrieval Augmented Generation (RAG).

## Folder layout

- `pdfs/`: official advising PDFs (preferred)
- `meta/`: companion metadata (one YAML per PDF)
- `txt/`: optional plain-text docs (mainly for quick prototyping)
- `scrape/seed_urls.txt`: reviewed BS ISDA, MLIS, and SJSU pages (MARA and MSIS snapshots were saved from official ischool.sjsu.edu pages; see each `meta/` `source_url`) for the offline crawler
- `scrape/manifest.json`: deterministic inventory and content hashes

Files under `scrape/` are crawler controls and are never indexed as advising
evidence.

If you don’t use subfolders, the app will still look for `*.pdf` / `*.txt` recursively under `data/advising_docs/`.

## Adding or updating a document

1. Add/replace the PDF under `pdfs/`
2. Create/update the companion YAML under `meta/` (same basename as the PDF)
3. Rebuild the index (see repo README)

The corpus covers the SJSU iSchool BS ISDA, MLIS, and MARA (Master of Archives
and Records Administration) programs. The `msis` key is reserved for MS in
Information Science pending Chris's confirmation; it has no advising content.
Do not ingest MSIS or MS Informatics material or treat them as equivalent.
Add only official ischool.sjsu.edu program pages or catalog.sjsu.edu program
and course pages. Do not add MSIDT or Teacher Librarian material.
University-wide policies are appropriate when they apply to these students;
label program-specific requirements with companion metadata `programs: [ISDA]`,
`[MLIS]`, or `[MARA]`. MARA pages may describe MARA's approved electives from
other programs; this does not authorize ingesting those programs' pages.

`sync_pages` replaces manifest entries for the current run’s programs and
preserves entries whose programs do not overlap. Crawl each program separately
with `--program ISDA` or `--program MLIS` and a reviewed seed subset. A combined
seed list must not be crawled with a single program label. Older crawler versions
rewrite the entire manifest: use a temporary docs root and merge the results
when using those versions. Rebuild the index after a corpus change.
