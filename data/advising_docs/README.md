# Advising documents (RAG)

This folder is the **source** for Retrieval Augmented Generation (RAG).

## Folder layout

- `pdfs/`: official advising PDFs (preferred)
- `meta/`: companion metadata (one YAML per PDF)
- `txt/`: optional plain-text docs (mainly for quick prototyping)

If you don’t use subfolders, the app will still look for `*.pdf` / `*.txt` recursively under `data/advising_docs/`.

## Adding or updating a document

1. Add/replace the PDF under `pdfs/`
2. Create/update the companion YAML under `meta/` (same basename as the PDF)
3. Rebuild the index (see repo README)

