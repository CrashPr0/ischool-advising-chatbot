# Advising documents (RAG)

This folder is the **source** for Retrieval Augmented Generation (RAG).

## Folder layout

- `pdfs/`: official advising PDFs (preferred)
- `meta/`: companion metadata (one YAML per PDF)
- `txt/`: optional plain-text docs (mainly for quick prototyping)
- `scrape/seed_urls.txt`: reviewed BS ISDA, MLIS, and SJSU pages for the offline crawler
- `scrape/manifest.json`: deterministic inventory and content hashes

Files under `scrape/` are crawler controls and are never indexed as advising
evidence.

If you don’t use subfolders, the app will still look for `*.pdf` / `*.txt` recursively under `data/advising_docs/`.

## Adding or updating a document

1. Add/replace the PDF under `pdfs/`
2. Create/update the companion YAML under `meta/` (same basename as the PDF)
3. Rebuild the index (see repo README)

The corpus covers the SJSU iSchool BS ISDA and MLIS programs. Do not add MARA,
Informatics, MSIDT, or Teacher Librarian material. University-wide policies are
appropriate when they apply to ISDA or MLIS students; label program-specific
requirements with companion metadata `programs: [ISDA]` or `programs: [MLIS]`.

`sync_pages` replaces manifest entries for the current run’s programs and
preserves entries whose programs do not overlap. Crawl each program separately
with `--program ISDA` or `--program MLIS` and a reviewed seed subset. A combined
seed list must not be crawled with a single program label. Older crawler versions
rewrite the entire manifest: use a temporary docs root and merge the results
when using those versions. Rebuild the index after a corpus change.
