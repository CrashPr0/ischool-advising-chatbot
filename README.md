# iSchool Senior Advising Chatbot (RAG)

A LangChain-based advising chatbot for SJSU iSchool BS ISDA and MLIS students. It supports:

- **Knowledge base answers** (instant responses for common questions)
- **RAG** (retrieves relevant documents and grounds the answer)
- **Multiple LLM providers** (OpenAI / Azure OpenAI / Anthropic / Hugging Face)
- **OpenAI-compatible endpoints** (DeepSeek, Qwen/DashScope, Zhipu/GLM, or a self-hosted gateway)
- **CLI** (`python -m src.main`) and **Web UI** (`app.py` Flask + `templates/` + `static/`)

The chatbot takes in student questions and makes an answer based on what it knows. This is to make sure that students get answers faster than emailing students, which should also decrease the workloads of advisors.

See the [faculty-facing architecture diagram](docs/architecture.md) for how the chatbot works.

---

## Team
- Christopher Velez (GitHub: CrashPr0)
- Marios Tawdros (GitHub: mariostawdros)
- Joshua Rieta (GitHub: cho-sua)

---
## Prerequisites
Use Python 3.11 or newer and configure at least one supported provider in
`.env`. The CLI and web app fail clearly when no provider is configured; the
knowledge base remains useful for tests and direct library callers without
making a network request. The deployed Azure URL in “External documents” is
the hosted app.

---
# Installation
## Quick start (CLI)

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
```

`requirements.txt` is the production/Azure dependency set and expects hosted
Azure OpenAI or OpenAI embeddings. For fully local Hugging Face embeddings,
install `requirements-local.txt` instead; it includes the production set plus
Sentence Transformers and PyTorch.

Set at least one API key in `.env`, then run:

```bash
python -m src.main
```

## Advising documents (RAG)

### Where to put documents

- **PDFs**: `data/advising_docs/pdfs/`
- **Scraped/cleaned webpage text (optional)**: `data/advising_docs/txt/`
- **Companion metadata (YAML)**: `data/advising_docs/meta/`

The loader searches recursively under `data/advising_docs/`, but the `pdfs/` + `meta/` layout is the recommended convention.
When a matching YAML file is present, its fields are carried into indexed
document metadata and can be used for stable IDs, titles, topics, and source
URLs.

### Offline website ingestion (scrape into txt + meta)

The scraper is **not** meant to run at query-time. It writes files your existing `DocumentProcessor` already ingests. The checked-in seed list covers reviewed BS ISDA, MLIS, and applicable SJSU-wide sources. MARA, Informatics, MSIDT, and Teacher Librarian remain excluded. Crawl program-specific seed subsets separately with `--program ISDA` or `--program MLIS`; do not label the combined seed list as one program. `sync_pages` preserves manifest entries for programs outside the current run.

1. Review seeds in `data/advising_docs/scrape/seed_urls.txt` (one URL per line). Copy only the program and applicable university seeds for the current crawl into a temporary seed file, such as `/tmp/isda-seeds.txt`.
2. Run:

```bash
python -m src.scrape_ischool \
  --docs-root ./data/advising_docs \
  --seeds /tmp/isda-seeds.txt \
  --allow-host ischool.sjsu.edu \
  --allow-host www.sjsu.edu \
  --max-urls 100 \
  --max-depth 0 \
  --delay-s 1.0 \
  --program ISDA \
  --topic advising
```

Optional: also pass a sitemap (bounded) to add more pages:

```bash
python -m src.scrape_ischool \
  --docs-root ./data/advising_docs \
  --seeds /tmp/isda-seeds.txt \
  --sitemap https://ischool.sjsu.edu/sitemap.xml \
  --allow-host ischool.sjsu.edu \
  --include-path '^/(bsisda|bachelor-science-information-science)' \
  --exclude-path '(mlis|mara|informatics|msidt|teacher-librarian)' \
  --max-urls 100 \
  --max-depth 1 \
  --delay-s 1.0 \
  --program ISDA \
  --topic advising
```

3. Rebuild the vector index (see below).

The crawler honors `robots.txt` and is bounded by host, path rules, crawl depth,
URL count, response size, timeout, and request delay. It removes common tracking
parameters, records content hashes and writes a deterministic source inventory
to `data/advising_docs/scrape/manifest.json`. Unchanged snapshots are not
rewritten. It never runs during a chat request.

### Companion metadata format

Create one YAML file per PDF with the same basename.

Example:
- `data/advising_docs/pdfs/degree_requirements.pdf`
- `data/advising_docs/meta/degree_requirements.yml`

Minimal fields:
- `doc_id`: stable slug used in citations
- `title`: human-friendly title
- `topics`: list of tags (used for filtering/eval)

See `data/advising_docs/meta/example.yml` for a full template.

### Rebuild the vector index

```bash
# Build (replaces the named advising collection deterministically)
python -m src.build_index --docs ./data/advising_docs

# Clean rebuild
python -m src.build_index --docs ./data/advising_docs --clean
```

The index persists to `Config.VECTOR_STORE_PATH`. Leave that variable unset.
Local development then uses `./data/chroma`. On Azure App Service the same
default is `/home/data/chroma` on the persistent `/home` share. Do not set
`VECTOR_STORE_PATH` to `/tmp/chroma`, `./data/chroma`, or
`/home/site/wwwroot/data/chroma`: `/tmp` and the Oryx extract disappear on
restart, and `/home/site/wwwroot` is replaced on every zip deploy. An explicit
value still overrides the default, which is why the Azure setting stays unset.
The web rebuild endpoint is admin-only; use the CLI for scheduled or local
indexing jobs.

The Azure deployment workflow rebuilds the production index from the committed
source snapshots and fails if the deployed collection is empty. Configure the
same strong `DOCS_LOAD_TOKEN` in Azure App Service and as a GitHub Actions
repository secret. Never place that token in this repository or a command log.

### CLI commands

- `help` - show commands
- `docs` - load the configured advising documents and rebuild the vector index
- `ALLOW_SAMPLE_DOCS=true` - opt in to the legacy local-only MLIS fixture
- `questions` - list hardcoded questions
- `clear` - clear conversation history
- `exit` - quit

---

## Quick start (Web UI)

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
python app.py
```

Open `http://localhost:5000`.

### Web API endpoints

- `GET /api/status`
- `GET /healthz` (liveness probe; does not require provider credentials or an index)
- `GET /readyz` (index receipt; 503 until `index_build.json` matches `deployment_version.txt` and the embedding provider, model, and dimension. The JSON also includes `catalog_may_be_stale`, which is a warning and does not change the HTTP status. Deploy probes and the Azure health check must keep using `/healthz`)
- `POST /api/chat` `{ "message": "..." }` (503 with a short message when the index receipt is missing, is for another release, or was built with a different embedder; the model is not called. `/healthz` stays up so a deploy can rebuild the index)
- `GET /api/history`
- `POST /api/clear-history`
- `POST /api/load-docs` (admin `X-Admin-Token` or `Authorization: Bearer ...` required)
- `GET /api/docs-stats` (same admin token; source-file and index counts)
- `GET /api/questions`
- `GET /api/voice-config`

Chat history is isolated per browser session, bounded, and held in worker
memory. Each `/api/chat` request passes that session's earlier turns into
the answer, so a follow-up can refer to the previous question. A reply
whose `safety_tier` is `crisis` is returned to the browser and is not
stored. History is cleared on restart; multi-worker deployments should use
a shared session store (such as Redis) if durable history is required.
Document index rebuilds are disabled unless `DOCS_LOAD_TOKEN` is configured.

### Browser voice mode

The web UI includes a credential-free voice path built on the browser Web
Speech API. Press **Start voice input** to dictate a question, review the
transcript, and press **Send**. Enable **Read replies aloud** to use the
browser's speech synthesis. Chrome and Edge generally provide the broadest
support; browsers may ask for microphone permission and may send recognition
audio to their own speech service. This app does not upload audio to its
server.

Voice is provider-agnostic: the chatbot still receives ordinary text through
`POST /api/chat`. Mandarin is available as the `zh-CN` locale, and the
transcribed text can be answered by any configured Chinese-capable LLM without
changing the voice UI. Recognition and synthesis remain optional browser
features, so text chat continues to work when unsupported.

The app exposes `GET /api/voice-config` using
`get_voice_config().as_dict()` from `src/voice_config.py`. The frontend uses
that response to apply the following environment settings and falls back to
safe browser defaults when an older deployment does not have the endpoint:

- `VOICE_ENABLED`, `VOICE_MODE=browser|disabled`
- `VOICE_STT_ENABLED`, `VOICE_TTS_ENABLED`
- `VOICE_LANGUAGES` (comma-separated BCP-47 locales, default `en-US,zh-CN`)
- `VOICE_DEFAULT_LANGUAGE`, `VOICE_AUTO_SEND` (default `false`)
- `VOICE_MAX_UTTERANCE_CHARS` (bounded to 100–10,000)

Keep `VOICE_AUTO_SEND=false` unless the deployment has reviewed microphone
consent and transcript-review requirements. A future server-side STT/TTS
adapter can implement the same config contract without changing `/api/chat`.

---

## Configuration (`.env`)

Minimum configuration is one provider key plus a default. Existing OpenAI,
Azure OpenAI, Anthropic, and Hugging Face settings remain supported:

```env
OPENAI_API_KEY=...
DEFAULT_LLM_PROVIDER=openai
```

Optional:

```env
ANTHROPIC_API_KEY=...
HUGGINGFACE_API_KEY=...
```

For deployment security, also set a stable `SESSION_SECRET_KEY`, a separate
`DOCS_LOAD_TOKEN`, and `SESSION_COOKIE_SECURE=true` when serving over HTTPS.
The app bounds request size, chat input, and request rates through
`MAX_REQUEST_BYTES`, `CHAT_MESSAGE_MAX_LENGTH`, `CHAT_RATE_LIMIT`, and
`CHAT_RATE_WINDOW_SECONDS`; generated responses are bounded by
`RAG_RESPONSE_MAX_CHARS`; chat retrieves `RAG_TOP_K` chunks (default 8,
clamped to 1–20). The completion cap stays `LLM_MAX_TOKENS` (512). History
retention is controlled by `MAX_HISTORY_MESSAGES` and `CHAT_SESSION_TTL_SECONDS`.

### Hybrid retrieval

Queries explicitly mentioning MLIS, library science, or a master's program prefer
chunks tagged `programs: [MLIS]`; ISDA, undergraduate, and bachelor's queries
prefer ISDA chunks. This stable preference runs within the hybrid candidate pool
before selecting top-k and also applies to the BM25 fallback when query embeddings
fail. The vector-only path reranks its existing top-k. Untagged sources remain
available as fallback evidence. Queries naming both programs or neither retain
their ranking; the prompt asks for clarification or clearly labels both programs
and never combines their requirements.

`HYBRID_RETRIEVAL` defaults to on. Chat fuses the vector ranking with local
BM25 scores using reciprocal rank fusion. The lexical side is what keeps
keyword questions such as "core courses" and "required classes" on the
complete BS ISDA core list. A question whose words are only about applying
to graduate also searches the registrar application terms, so "how do I
graduate" is not answered from elective text about graduate courses. On
Application Central, Deadline 1 and Deadline 2 are priority-registration
dates; the cutoff is the add deadline of the graduation term. An
apply-to-graduate question pins that page's eligibility rule and term
rows into the prompt even when ranking missed them, and the MySJSU
tutorial is shortened and placed after those rows. Set
`HYBRID_RETRIEVAL=false` for vector-only
search. Course codes are normalized, so `ISDA 150` and `ISDA150` hit the
same chunks. Fusion does not call an embedding or chat API. The flag is
read on each query. Rebuild the index when advising documents change; the
core-list chunk text is part of that index.

`CATALOG_RECENCY_RERANK` defaults to on. After fusion, a current-catalog
chunk takes the better slot of an older catalog year for the same course or
program. `CURRENT_CATALOG_CATOID` (23) and `CURRENT_CATALOG_YEAR` (2026-2027)
name that catalog. Registrar and iSchool pages are not reordered. If an
iSchool or other non-catalog page in the top results gives a different title
for a course the current catalog already lists there, that course's current
catalog page is pulled into the result. That pull does not drop an official
registrar graduation page, the undergraduate graduation tutorial, or a
commencement page. Older catalog snapshots stay in the index for students on
earlier catalog rights. Non-catalog context is labeled with its source type;
catalog context is labeled with its catalog year.

### Catalog updates

The committed catalog map is [`data/catalog_identity.json`](data/catalog_identity.json).
The app reads that file when it starts. `CURRENT_CATALOG_CATOID` and
`CURRENT_CATALOG_YEAR`, when set, override the file, including on Azure App
Service. An unknown catoid takes its year from the snapshot's stored
`catalog_year` or from a `YYYY-YYYY Academic Catalog` line in the text.
Startup and each request stay on the committed snapshots.

[`.github/workflows/check-sjsu-catalog.yml`](.github/workflows/check-sjsu-catalog.yml)
runs every Monday, and it can be started by hand: open the GitHub **Actions**
tab, choose **Check SJSU catalog**, and click **Run workflow**. The job reads
the catalog menu at `catalog.sjsu.edu` and treats the selected academic year
as the published current catalog. When that year is newer than
`data/catalog_identity.json`, the job downloads the ISDA program pages and
ISDA course pages for the new catoid, writes those snapshots next to the
older ones, and updates the identity file, `.env.example`, and the catalog
year named above. It then runs `pytest` and opens a pull request against
`main` for a person to review. The pull request is left for that review;
main stays as it is until someone merges.

The job finishes successfully with no pull request when the published catalog
is unchanged, when a catalog pull request is already open, or when the site
cannot be read. A network error, a bot challenge, or a page that no longer
has one selected academic year is logged as a warning and leaves the tree
alone.

The workflow asks for `contents: write` and `pull-requests: write` on
`GITHUB_TOKEN`. Under **Settings → Actions → General → Workflow permissions**,
the repository needs **Read and write permissions** and **Allow GitHub Actions
to create and approve pull requests**. A pull request opened with
`GITHUB_TOKEN` does not start the pull-request test workflow. This job runs
pytest before it opens the pull request. If `CURRENT_CATALOG_CATOID` or
`CURRENT_CATALOG_YEAR` is set on App Service, that value overrides the file
after the pull request is merged, so clear or update the App Service setting
when you accept the new catalog.

`GET /readyz` includes `catalog_may_be_stale`, `catalog_year`,
`catalog_catoid`, and `expected_catalog_year`. The flag is true when today's
date is on or after `CATALOG_STALE_CUTOFF` (default `08-20`) and the
configured catalog year is still an older academic year. SJSU fall
instruction usually begins in the second half of August, so August 20 is the
default point at which the next catalog year should already be current.
Before that date in calendar year Y the expected catalog is (Y-1)-Y; on that
date and after it, the expected catalog is Y-(Y+1). The HTTP status stays the
index receipt. A stale catalog is also written to the log. Answers name the
catalog year they are using, and when the flag is true they say a newer
catalog may have been published.

### Chinese models and other OpenAI-compatible services

The app uses the existing `langchain-openai` dependency and passes a provider's
OpenAI-compatible `base_url`, API key, and model name to `ChatOpenAI`. No
provider SDK or credential is hard-coded. Set one of these provider profiles as
the default:

```env
# DeepSeek (official endpoint; do not append /v1)
DEFAULT_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

# Alibaba Cloud Model Studio / DashScope (Qwen)
DEFAULT_LLM_PROVIDER=qwen
DASHSCOPE_API_KEY=...
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus

# Zhipu / Z.ai (GLM)
DEFAULT_LLM_PROVIDER=zhipu
ZAI_API_KEY=...
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4
ZHIPU_MODEL=glm-5.2
```

`dashscope` is accepted as an alias for `qwen`; `zai` and `zhipuai` are
accepted as aliases for `zhipu`. For an unlisted OpenAI-compatible service, use
the generic profile instead:

```env
DEFAULT_LLM_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_API_KEY=...
OPENAI_COMPATIBLE_BASE_URL=https://provider.example.com/v1
OPENAI_COMPATIBLE_MODEL=your-model-name
```

Provider endpoints and model availability vary by region and account. Check
the official documentation before deploying: [DeepSeek API](https://api-docs.deepseek.com/),
[Alibaba Cloud Model Studio OpenAI compatibility](https://help.aliyun.com/en/model-studio/compatibility-of-openai-with-dashscope),
and [Zhipu OpenAI compatibility](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction).

Important: **Never commit `.env`**. `.env.example` lists every variable the
application code reads, with the code default or a placeholder. Platform
variables (`PORT`, `WEBSITE_*`, `GITHUB_*`) are left out on purpose.

---

## Tests / formatting

```bash
python -m pytest -q
black .
flake8 .
```

Pull requests targeting `main` run that test command and the dependency audit in [`.github/workflows/pr-tests.yml`](.github/workflows/pr-tests.yml). The same workflow can be started with **Run workflow**. It uses Python 3.11 and `pip install -r requirements.txt` (that file applies `constraints.txt`), then `python -m pytest tests/ -q` and `scripts/check_pip_audit.py`. Those steps are copied from the Azure deploy workflow so a pull request cannot publish, and the job uses dummy `APP_URL` and `DOCS_LOAD_TOKEN` values instead of repository secrets. Enable a branch protection rule on `main` that requires the **pytest and pip-audit** check before merging. Until that rule exists, a broken test can still land and fail only after merge, which is what happened with PR #5.

## Azure GPT-4o mini vs. GPT-5.6 Luna benchmark

The repository includes a repeatable RAG A/B test. Each advising question is
retrieved once with the offline hashing index, then the Azure deployments named
`gpt-4o-mini` and `gpt-5.6-luna` receive the exact same system prompt and
document excerpts. This keeps Azure embedding rate limits and retrieval
variance out of the model comparison. Invocation order alternates to reduce
connection-warming bias. The generated Markdown report includes both answers,
retrieved sources, latency, reported token usage, and a 1–7 human-review rubric.
The default retrieves three chunks per question so prompts fit the current
student deployment's small token-per-minute allocation; both models always
receive the same three chunks.

Both models use the existing Azure endpoint and key. The defaults assume the
two deployment names above; set `AZURE_BENCHMARK_LEFT_DEPLOYMENT` and
`AZURE_BENCHMARK_RIGHT_DEPLOYMENT` (or use `--left-model` and
`--right-model`) if your Azure deployment names differ. Transient Azure
429/time-out responses first honor provider `Retry-After` and rate-limit reset
headers, then fall back to bounded exponential backoff. The benchmark paces
question pairs with `--request-delay` (60 seconds by default for the current
student deployment). Set `BENCHMARK_REQUEST_DELAY_SECONDS` or pass the flag
explicitly after increasing the Azure deployment's rate limit.

Then run the curated benchmark in `evals/advising_questions.json`. That file is
the review-queue source: a smaller set of non-overlapping questions across
admissions, advising access, courses and registration, degree requirements,
career, policies and deadlines, out-of-scope and sensitive-data requests, and
distress or academic failure. Each item has a `category` and a `review_focus`
describing what a good answer should do. The chat UI's topic chips stay a
separate, student-facing starter list. Distress and requests for student IDs,
grades, finances, or other sensitive records are not offered as chips. The
first run builds an isolated, quota-free hashing index and does not download
an embedding model. Passing `--build-index` always replaces a stale or
incompatible benchmark index before invoking either model:

```bash
python -m src.model_benchmark --build-index
```

Every model response is atomically checkpointed to
`evals/results/latest.partial.json`. Re-running the same command resumes the
matching run and skips successful responses, including when only one side of a
question finished. The console reports completed responses, errors, retry
delays, and an estimated time remaining. `evals/results/latest.json` and the
Markdown report are replaced only after both responses succeed for every
question, so an interrupted or throttled run cannot replace the live review
dataset.

Use `--fresh` to deliberately discard a matching partial run. A checkpoint
whose questions, model deployments, retrieval settings, or system prompt do not
match is rejected instead of being mixed into the new run. Retry behavior can
be tuned with `--max-attempts`, `--retry-delay`, and `--max-retry-delay`.

For a cheap smoke test before the full run:

```bash
python -m src.model_benchmark --build-index --limit 1 \
  --output evals/results/smoke.json \
  --report evals/results/smoke.md
```

Limited runs must use non-default output paths and therefore cannot replace the
curated dataset used by the hosted evaluation workspace. The published
`evals/results/latest.json` snapshot is the previous completed run. It stays in
place until a full benchmark succeeds, so the review queue does not switch to
the new questions until both models have answered them.

If GPT-5.6 Luna is available through an OpenAI API account rather than as a
deployment in the Azure resource, keep GPT-4o mini on Azure and route only the
right side to OpenAI:

```bash
python -m src.model_benchmark --build-index \
  --right-provider openai --right-model gpt-5.6-luna
```

That mixed-provider run requires `OPENAI_API_KEY`; the shared prompt, retrieved
evidence, alternating call order, and scoring workflow remain unchanged.

The latest JSON snapshot is tracked so the hosted evaluation workspace always
has a review set after deployment. The human-readable report and vector index
remain local build artifacts:

- `evals/results/latest.json` — deployed machine-readable review set
- `evals/results/latest.md` — side-by-side human review sheet
- `evals/vector_store/` — local evaluation-only retrieval index

Edit `evals/advising_questions.json` to add questions or reviewer expectations.
Every curated question needs a category and a review focus covering the
expected facts, handoff, or refusal. A test checks that the set stays inside
12–20 questions, covers each category, includes more than one distress or
academic-failure case, and does not copy those cases into the chat presets.
The chat presets and the benchmark are allowed to differ. GPT-5.6 Luna's
current API model ID and capabilities are documented on the [official OpenAI
model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

### Human evaluation workspace

After generating `evals/results/latest.json`, start the Flask app and open
`http://localhost:5000/evaluation`. The workspace gives reviewers a blind,
per-reviewer randomized Candidate A/B comparison with:

- a required 1–5 score on four dimensions (information accuracy, tone, appropriate length, and edge-case or distress handling) and a 1–3 sentence rationale for every response;
- per-reviewer progress, editable saved reviews, and a next-incomplete shortcut;
- model identity reveal only after both responses to a question are scored;
- aggregate model results and CSV audit export only after the run is complete.

Reviews are stored in SQLite. Locally the file is `evals/reviews.sqlite3`
beside the app (gitignored). On Azure App Service the file is
`/home/data/evaluation/reviews.sqlite3` on the persistent `/home` share. That
is the default when `EVAL_DB_PATH` is unset; setting `EVAL_DB_PATH` to the
same path is equivalent. Do not point it at `/tmp` or `/home/site/wwwroot`.
The first start copies a legacy database onto the durable path when the new
file is missing and an older file is still on disk (beside the app, or under
`/home/site/wwwroot/evals/`, a leftover from older runs). The copy never moves
or deletes the original. Opening `/evaluation` against a missing durable file
creates an empty database, and a later copy will not overwrite it. The backup
taken before that path became the live file is recorded in `docs/ops-log.md`.

Students can report a wrong answer from the chat. Each report is a row in
`answer_issue_reports` in that same SQLite file. The row stores the reason,
optional comment, and the flagged question and answer. The conversation id,
model, earlier turns, and links from the answer are stored only when the
student turns on "Include this conversation so the team can review it".
Signed-in reviewers see the list on `/evaluation` and at
`GET /api/evaluation/issue-reports`. The reviewer page shows counts by reason,
highlights the most reported issue type, and offers reason filters for the loaded reports.
Reports, including opted-in transcripts,
are automatically deleted after 90 days. Set `ISSUE_REPORT_RETENTION_DAYS`
to a positive integer to override this window; invalid values fall back to 90.
Cleanup runs when the report store initializes, then on writes or lists at most
once per hour per worker, without a separate scheduler. Only deletion counts
are logged, never report contents.

Free-text fields are scrubbed before they are written. `scrub_pii` replaces
SJSU 9-digit student IDs (including `012-345-678`), email addresses, US phone
numbers, and US street addresses with `[REDACTED_STUDENT_ID]`,
`[REDACTED_EMAIL]`, `[REDACTED_PHONE]`, and `[REDACTED_ADDRESS]`. That scrub
covers issue-report comments, questions, answers, and opted-in transcripts,
reviewer comments, and the question stored in the stripped-link log. Course
codes such as INFO 202 and ISDA 150, years, dates, and shorter numbers are
left unchanged. `src/durable_storage.py` copies the reviews database file and
does not write those free-text fields.

Each accepted student question is saved anonymized in
`anonymized_student_questions` in that same reviews database, after the same
scrub. The row stores the question text and a UTC timestamp only. It does not
store a user, session, conversation, or network identifier. The table is
created when the first question is saved, and creating it again is a no-op.
A failure to write the row is logged and does not change the chat response.

Students can use **Export chat** to keep the current conversation on their
own device with a one-click PDF download. The locally vendored jsPDF 4.2.1
build creates the export entirely client-side, sends no network request, and
stores nothing on the server. If PDF generation is unavailable, choose
**Save as PDF** in the browser's fallback print dialog. Older saved turns
without recorded timestamps show **Timestamp unavailable**.

Localhost can use the workspace without a shared secret. On a public deploy,
sign-in needs `EVAL_ACCESS_TOKEN`, `EVAL_ALLOWED_REVIEWERS`, or Easy Auth;
otherwise the app returns 503. `EVAL_ACCESS_TOKEN` is not set on the current
Azure app, and `EVAL_ALLOWED_REVIEWERS` is. See `docs/ops-log.md`. Leave
`EVAL_DB_PATH` unset unless you intentionally pin
`/home/data/evaluation/reviews.sqlite3`.
`EVAL_RESULTS_PATH` still defaults to the `evals/results/latest.json` shipped
with the release.

The access token is exchanged for a signed, HTTP-only session and is never
stored in browser storage. The app reads `X-MS-CLIENT-PRINCIPAL-NAME` only
when `WEBSITE_AUTH_ENABLED` is true. Turn Easy Auth on in the portal so the
platform sets that variable and strips a client-supplied header. SQLite is
appropriate for one App Service instance; use a managed database before
scaling the app horizontally.

---

## Deploy to Azure (App Service)

Production is the Linux App Service app `ischooladvisingchatbot-dx4ynl` on the
Basic B1 plan (a paid tier, billed against student credits; B1 is not free and
cannot use deployment slots). Every push to `main` that changes the app runs
`.github/workflows/deploy-azure.yml`: tests, zip deploy, wait for `/healthz`,
then rebuild the advising index and check its receipt. Oryx runs the release
from `/tmp`. `/home` is the persistent share.

Leave `VECTOR_STORE_PATH` unset so the index and `index_build.json` live at
`/home/data/chroma`. Reviews live at `/home/data/evaluation/reviews.sqlite3`.
`GET /healthz` is liveness and the platform health check. It does not open the
index. `GET /readyz` is the index receipt: 200 when `index_build.json` matches
this release and its embedding provider, model, and dimension, and 503 when
the receipt is missing or stale. The portal health check stays on `/healthz`.

The Procfile, the container image, and the Azure portal startup command are
`gunicorn --config gunicorn.conf.py app:app`. That file is the single source
of truth for the worker class (`gthread`), `GUNICORN_WORKERS=2`,
`GUNICORN_THREADS=4`, `GUNICORN_TIMEOUT=180`, `GUNICORN_GRACEFUL_TIMEOUT=210`,
and the bind to `$PORT` (default 8000). HTTPS Only is enabled, so HTTP
301-redirects to HTTPS and the session cookie can be marked Secure.

Chat calls use `LLM_TEMPERATURE=0.2`, `LLM_REQUEST_TIMEOUT=45`,
`LLM_MAX_RETRIES=0`, and `LLM_MAX_TOKENS=512`. Leave those unset to keep the
defaults. `AZURE_OPENAI_FALLBACK_DEPLOYMENT` is an optional second Azure
deployment for 429, timeout, and 5xx. `CHAT_DEBUG_TIMINGS=true` adds a
timings object to `/api/chat`.

The runbook in `AZURE_DEPLOYMENT.md` covers portal settings, the reviews
backup, rollback (`git revert` and push), a budget alert, and Azure OpenAI
quota. Changes made in the portal, outside git, are recorded in
`docs/ops-log.md`. Staging needs a second App Service app or a plan that
supports slots (Standard or higher).

---

## Repository structure

```
.
├── app.py                 # Flask web server
├── src/                   # Chatbot logic (RAG + providers + config)
├── templates/             # Web UI HTML (Flask template)
├── static/                # Web UI assets (CSS/JS/images)
├── tests/                 # Unit tests
├── requirements.txt       # Lean production/Azure dependencies
├── requirements-local.txt # Optional local Hugging Face embedding stack
├── .env.example
├── docs/ops-log.md        # Production changes made outside git
└── README.md
```

---
## Demo
The hosted app is linked in “External documents”. Screenshots of that site:

<img width="2234" height="1290" alt="image" src="https://github.com/user-attachments/assets/3505bb5e-6b1a-4736-838f-66edc799c035" />
Main page of the chatbot on Azure

<img width="2229" height="1288" alt="image" src="https://github.com/user-attachments/assets/91fd3991-dece-41ae-9fa4-537afdafddc4" />
Selecting "General Advising" and pre-written question

<img width="1532" height="568" alt="image" src="https://github.com/user-attachments/assets/19ea81d5-05c9-4a7a-920b-f21214bcf06f" />
Bottom of the site

---

## Technical Stack
The application uses Python 3.11+, Flask, LangChain, Chroma, HTML/CSS, and
JavaScript. LLM selection is configuration-driven (including OpenAI-compatible
Chinese providers), and Azure App Service remains a supported deployment target.

---
## What's Next
We want to fix up what we currently have at this moment since for next semester we will hopefully be deploying this chatbot onto the actual iSchool site. We need to make sure that parameters for questions are within the bounds of the School of Information, and that the chatbot responds with accurate information.

---
## External documents

- iSchool Advising Chatbot Rubric: `https://docs.google.com/spreadsheets/d/1kXhvObOL_13er0D4eyMBNfHel7tIFF4seUtL0yQtMk8/edit?usp=sharing`
- ISDA Advising Support Chatbot – Smoke Test Question Set: `https://docs.google.com/document/d/1KON-OufGsHjJpwY_hQp5m_doXAMw-rn98g4Oz9YvCQY/edit?usp=sharing`
- Figma Website Design `https://www.figma.com/design/J5cIoGXTAs7K07Q5X988Z6/iSchool-Chatbot-Site-RD?node-id=0-1&p=f&t=z4ZYAu1mDT8v6J8y-0`
- iSchool Advising Chatbot Azure Website: `https://ischooladvisingchatbot-dx4ynl.azurewebsites.net/`
---

## License

See `LICENSE`.

## BS ISDA and MLIS corpus rollout

The advising corpus now includes both BS ISDA and MLIS. After merge, the Azure
index must be rebuilt to include the MLIS chunks and program metadata. The
existing deploy workflow runs `/api/load-docs` through
`scripts/verify_deployed_index.py` and verifies the new receipt. No additional
manual step is needed for a successful normal deployment. The corpus contents and
release SHA change; `/readyz` checks the rebuilt receipt against the deployed
release SHA and configured embedder, rather than computing a corpus hash. If the workflow rebuild fails, rerun the deploy
workflow and verify `/readyz` before using chat. Do not reuse the old receipt.

The 15 ingested official MLIS/iSchool pages are:

- https://ischool.sjsu.edu/master-library-and-information-science
- https://ischool.sjsu.edu/mlis-required-courses
- https://ischool.sjsu.edu/mlis-electives
- https://ischool.sjsu.edu/mlis-academics
- https://ischool.sjsu.edu/mlis-course-load
- https://ischool.sjsu.edu/mlis-fees-chart
- https://ischool.sjsu.edu/mlis-program-learning-outcomes
- https://ischool.sjsu.edu/mlis-accreditation
- https://ischool.sjsu.edu/mlis-career-pathways
- https://ischool.sjsu.edu/admissions
- https://ischool.sjsu.edu/application-deadlines
- https://ischool.sjsu.edu/graduate-application
- https://ischool.sjsu.edu/advising-appointments
- https://ischool.sjsu.edu/info-289-e-portfolio-faqs
- https://www.sjsu.edu/online/programs/library-and-information-science-masters-degree.php
