# Retrieval diagnostic (2026-09-26)

Offline check of the committed advising corpus with the production embedding
default. `EMBEDDING_PROVIDER` was unset, so `src/config.py` used hashing.
No OpenAI or Azure OpenAI calls were made. Chat's default depth is
`RAG_TOP_K=5`.

## How the index is built

Advising evidence lives in `data/advising_docs/`. `DocumentProcessor` reads
`.txt` and `.pdf` files under that root and skips `scrape/`, `meta/`, and
`__pycache__/`. Companion YAML in `meta/` is stored as chunk metadata. The
loader splits text at 600 characters with 150 characters of overlap and
replaces the Chroma collection.

`POST /api/load-docs` (admin token) queues that rebuild:
`_run_docs_index_job` → `RAGAdvisingChatbot.process_documents()` →
`DocumentProcessor.process_and_index()`. The same path is
`python -m src.build_index`. This run indexed **20 documents / 189 chunks**.
`pdfs/` has no PDFs; the corpus is the scraped `.txt` files.

Hashing embeddings count casefolded `[a-z0-9]+` tokens into a fixed vector.
They do not use IDF, so the word "prerequisites" competes with a course code.

## (a) Does ISDA 150 appear, and what are its prerequisites?

ISDA 150 appears **twice**, both times as an elective example. Neither page
contains a prerequisite (the files do not contain "prereq"):

- `txt/web-9b0cdf930b5f0408.txt` — [BSISDA Electives](https://ischool.sjsu.edu/bsisda-electives)
- `txt/web-89ec3e6f3a71dd4a.txt` — [BSISDA Academics](https://ischool.sjsu.edu/bsisda-academics)

Shared sentence:

> Major electives may be selected from courses in the ISDA program such as
> ISDA 150 – Special Topics in Information Science and Data Analytics,
> ISDA 160 – User Experience (UX) Research and Design, OR …

The electives page links to
`https://catalog.sjsu.edu/preview_course_nopop.php?catoid=14&coid=131442`.
That catalog page is not in the corpus. **No prerequisite for ISDA 150 is
stated anywhere in the indexed text.**

## (b) Hashing top-5 vs hybrid top-5

Questions were run against one local hashing index. "Rank" is the first chunk
that contains the target string (full list for hashing; top 20 for hybrid).
Target strings:

- ISDA 150: the literal `ISDA 150` (there is no prerequisite sentence)
- ISDA 111: `Prerequisites: ISDA 20A and ISDA 20B`
- ISDA 120: `ISDA 120 Information Security` (that chunk also contains
  `Prerequisites: ISDA 20C, ISDA 20D, and ISDA 111`)
- ISDA 140: `Prerequisite: ISDA 111` on the ISDA 140 section

| Question | Hashing rank | In top-5 off | Hybrid rank | In top-5 on |
| --- | ---: | --- | ---: | --- |
| Prerequisites for ISDA 150? | 16 | no | 1 | yes (title only) |
| Prerequisites for ISDA 111? | 11 | no | 4 | yes |
| Prerequisites for ISDA 120? | 9 | no | 1 | yes |
| Prerequisites for ISDA 140? | 4 | yes | 2 | yes |

### What are the prerequisites for ISDA 150?

Hashing top-5 (none name ISDA 150):

1. `web-d00062418e57497c.txt` — BSISDA catalog overview ("prepares students to work with data, technology and people").
2. `web-29833fb8ad8ff493.txt` — `Prerequisites: ISDA 111 and ISDA 20C` / ISDA 170 capstone.
3. `web-e1d0b41b95ae681a.txt` — `Prerequisite: Must be taken in last semester of ISDA program`.
4. `web-e1d0b41b95ae681a.txt` — ISDA 131 prerequisites (`ISDA 20C, ISDA 20D, and ISDA 111`).
5. `web-e1d0b41b95ae681a.txt` — ISDA 140 section, same shared prerequisite line.

Hybrid top-5:

1. `web-89ec3e6f3a71dd4a.txt` — academics page, **ISDA 150 elective title**.
2. `web-29833fb8ad8ff493.txt` — ISDA 170 / `Prerequisites: ISDA 111 and ISDA 20C`.
3. `web-9b0cdf930b5f0408.txt` — electives page, **ISDA 150 elective title**.
4. `web-29833fb8ad8ff493.txt` — `Prerequisites: ISDA 20A and ISDA 20B` / ISDA 120.
5. `web-e1d0b41b95ae681a.txt` — same ISDA 120 chunk on the other required-courses page.

### What are the prerequisites for ISDA 111?

Published text, on both required-course pages: **Prerequisites: ISDA 20A and ISDA 20B (or equivalents).** The 600-character splitter puts that sentence at the start of the next chunk, which then continues into ISDA 120. The course title sits at the end of the previous chunk.

Hashing top-5 never includes that sentence (first hit is rank 11). The neighbors are other courses that *list* ISDA 111 as a prerequisite (ISDA 141, ISDA 170, ISDA 131, ISDA 140) plus the catalog overview.

Hybrid places the `ISDA 20A and ISDA 20B` chunk at rank 4 (and the duplicate page at rank 5).

### What are the prerequisites for ISDA 120?

Published text: **Prerequisites: ISDA 20C, ISDA 20D, and ISDA 111.** Hashing rank 9, so the default top-5 is the catalog overview, the capstone line, the "last semester" line, and the ISDA 131 / ISDA 140 sections. Hybrid rank 1 is the chunk that contains the ISDA 120 heading and that prerequisite line.

### What are the prerequisites for ISDA 140?

Published text: **Requires proficiency in Python programming. Prerequisite: ISDA 111.** Hashing already returns that section at rank 4. Hybrid moves it to rank 2. This one was not a miss under top-5.

## Missing content or ranking?

**ISDA 150's prerequisites are missing content.** The indexed pages only give the course title and an unfetched catalog URL. A correct answer cannot list prerequisites from this corpus.

**Finding the ISDA 150 mention is a ranking failure.** Hashing places it 16th, outside the top 5 the bot sends to the model, so the reply is made without the only passage that names the course. Hybrid retrieval (`HYBRID_RETRIEVAL=true`) ranks that passage first. The passage still has no prerequisites.

**ISDA 111 and ISDA 120 are ranking failures, not missing text.** Their prerequisite sentences are in the required-courses pages. Hashing top-5 does not include them (ranks 11 and 9). Hybrid brings them into the top 5 (ranks 4 and 1). ISDA 140 was already inside the hashing top 5 (rank 4) and stays inside (rank 2).

The hashing misses are the same shape: chunks that share "prerequisites" and "ISDA" outrank the chunk that actually contains the course code, because the embedder does not down-weight common tokens.

## Follow-up (2026-09-26): catalog course snapshots

Re-ran the same offline method after adding the 2026-2027 catalog course pages (`catalog.sjsu.edu`, catoid 23) for every current ISDA-prefixed course. `EMBEDDING_PROVIDER` stayed hashing. No OpenAI or Azure calls. The index is now **43 documents / 220 chunks**. Questions and target strings are unchanged. Hashing rank is over the full chunk list; hybrid rank is within the fused top 20 (`HYBRID_RETRIEVAL=true`).

| Question | Hashing before | Hashing after | Hybrid before | Hybrid after |
| --- | ---: | ---: | ---: | ---: |
| Prerequisites for ISDA 150? | 16 (not in top 5) | 7 (not in top 5) | 1 (title only) | 1 (catalog prerequisite) |
| Prerequisites for ISDA 111? | 11 (not in top 5) | 12 (not in top 5) | 4 | 3 |
| Prerequisites for ISDA 120? | 9 (not in top 5) | 9 (not in top 5) | 1 | 1 |
| Prerequisites for ISDA 140? | 4 | 4 | 2 | 2 |

The ISDA 150 hybrid hit is now `web-174ca44ffd773efd.txt`, which contains the catalog sentence `Prerequisite(s): ISDA 110.` Hashing still leaves that chunk outside the top 5.

The same target strings for ISDA 111, 120, and 140 still land on the iSchool required-courses chunks, not the new catalog pages. Those catalog sentences (`Prerequisite(s): …`) rank 8 / 33 / 20 with hashing and 8 / 12 / 8 with hybrid, all outside the top 5. The catalog label is `Prerequisite(s)`, so it does not share the token `prerequisites` with the question.

### Requisite-token normalization (same day)

BM25 now folds `prerequisite`, `prerequisites`, `Prerequisite(s)`, `prereq`, and `prereqs` into one token, and does the same for corequisite and pre/corequisite spellings. A trailing plural `s` is stripped from alphabetic tokens of length 5 or more. Tokens that contain a digit, including `isda20a`, `isda150`, and `isda20s`, are not stripped. Hashing embeddings are unchanged. Same questions, same target strings, same 43-document / 220-chunk index. No OpenAI or Azure calls.

| Question | Hashing before | Hashing after | Hybrid before | Hybrid after |
| --- | ---: | ---: | ---: | ---: |
| Prerequisites for ISDA 150? | 7 (not in top 5) | 7 (not in top 5) | 1 (catalog prerequisite) | 1 (catalog prerequisite) |
| Prerequisites for ISDA 111? | 12 (not in top 5) | 12 (not in top 5) | 3 | 7 (not in top 5) |
| Prerequisites for ISDA 120? | 9 (not in top 5) | 9 (not in top 5) | 1 | 1 |
| Prerequisites for ISDA 140? | 4 | 4 | 2 | 2 |

"Before" is the catalog-snapshot run above. Hashing ranks did not move. Hybrid ISDA 150 stays on `web-174ca44ffd773efd.txt` (`Prerequisite(s): ISDA 110.`).

Catalog-sentence hybrid ranks moved from 1 / 8 / 12 / 8 to 1 / 6 / 11 / 5. ISDA 140's catalog sentence (`Prerequisite(s): ISDA 111.`) is now inside the hybrid top 5. ISDA 111's catalog sentence is rank 6, and ISDA 120's is rank 11. The shared token is now common, so course-code matches decide the remaining order.
