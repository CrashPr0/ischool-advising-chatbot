# iSchool Senior Advising Chatbot (RAG)

A LangChain-based advising chatbot for SJSU iSchool students. It supports:

- **Knowledge base answers** (instant responses for common questions)
- **RAG** (retrieves relevant documents and grounds the answer)
- **Multiple LLM providers** (OpenAI / Azure OpenAI / Anthropic / Hugging Face)
- **OpenAI-compatible endpoints** (DeepSeek, Qwen/DashScope, Zhipu/GLM, or a self-hosted gateway)
- **CLI** (`python -m src.main`) and **Web UI** (`app.py` Flask + `templates/` + `static/`)

The chatbot takes in student questions and makes an answer based on what it knows. This is to make sure that students get answers faster than emailing students, which should also decrease the workloads of advisors.

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

The scraper is **not** meant to run at query-time. It writes files your existing `DocumentProcessor` already ingests. The checked-in seed list is deliberately limited to reviewed ISDA and SJSU-wide sources; do not add program-specific MLIS, MARA, Informatics, or MSIDT pages.

1. Put seed URLs in `data/advising_docs/scrape/seed_urls.txt` (one URL per line)
2. Run:

```bash
python -m src.scrape_ischool \
  --docs-root ./data/advising_docs \
  --seeds ./data/advising_docs/scrape/seed_urls.txt \
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
  --seeds ./data/advising_docs/scrape/seed_urls.txt \
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

The index persists to `Config.VECTOR_STORE_PATH`. With that variable unset,
local development uses `./data/chroma`. On Azure App Service the same default
is `/home/data/chroma`, which lives on the persistent `/home` share. Do not
point production at `/tmp/chroma` or another path under the Oryx extract:
those directories disappear on restart. An explicit `VECTOR_STORE_PATH` still
overrides the default. The web rebuild endpoint is admin-only; use the CLI
for scheduled or local indexing jobs.

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
- `GET /readyz` (index receipt; 503 until `index_build.json` matches `deployment_version.txt`. Deploy probes and the Azure health check must keep using `/healthz`)
- `POST /api/chat` `{ "message": "..." }`
- `GET /api/history`
- `POST /api/clear-history`
- `POST /api/load-docs` (admin `X-Admin-Token` or `Authorization: Bearer ...` required)
- `GET /api/docs-stats` (same admin token; source-file and index counts)
- `GET /api/questions`
- `GET /api/voice-config`

Chat history is isolated per browser session, bounded, and held in worker
memory. It is cleared on restart; multi-worker deployments should use a shared
session store (such as Redis) if durable history is required. Document index
rebuilds are disabled unless `DOCS_LOAD_TOKEN` is configured.

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
`RAG_RESPONSE_MAX_CHARS`; history retention is controlled by
`MAX_HISTORY_MESSAGES` and `CHAT_SESSION_TTL_SECONDS`.

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

Important: **Never commit `.env`** (use `.env.example` as the template).

---

## Tests / formatting

```bash
python -m pytest -q
black .
flake8 .
```

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
beside the app (gitignored). On Azure App Service, when `EVAL_DB_PATH` is
unset, the file is `/home/data/evaluation/reviews.sqlite3` on the persistent
`/home` share. The first start copies a legacy database onto that path when
the new file is missing and an older file is still on disk (beside the app,
or under `/home/site/wwwroot/evals/`). The copy never moves or deletes the
original. Because Oryx runs from `/tmp`, that legacy file is often already
gone after a restart, so take the manual backup in `AZURE_DEPLOYMENT.md`
before deploying the path change. Opening `/evaluation` against a missing
durable file creates an empty database, and a later copy will not overwrite it.

Localhost can use the workspace without a shared secret. For a deployed
handoff, set `EVAL_ACCESS_TOKEN` to a long random value. Leave `EVAL_DB_PATH`
unset unless you intentionally pin `/home/data/evaluation/reviews.sqlite3`.
`EVAL_RESULTS_PATH` still defaults to the `evals/results/latest.json` shipped
with the release.

The access token is exchanged for a signed, HTTP-only session and is never
stored in browser storage. The workspace recognizes Azure App Service Easy
Auth/Entra user headers. Turn Easy Auth on in the portal so the platform, not
the browser, supplies `X-MS-CLIENT-PRINCIPAL-NAME`. Keep `EVAL_ACCESS_TOKEN`
set until that is confirmed. SQLite is appropriate for one App Service
instance; use a managed database before scaling the app horizontally.

---

## Deploy to Azure (App Service)

Production is the Linux App Service app `ischooladvisingchatbot-dx4ynl` on the
Basic B1 plan (a paid tier, billed against student credits; B1 is not free and
cannot use deployment slots). Every push to `main` that changes the app runs
`.github/workflows/deploy-azure.yml`: tests, zip deploy, wait for `/healthz`,
then rebuild the advising index. Oryx runs the release from `/tmp`. `/home` is
the persistent share, so the index and reviews database default to
`/home/data` there.

The runbook in `AZURE_DEPLOYMENT.md` covers the required portal settings
(Easy Auth, HTTPS-only, `SESSION_SECRET_KEY`, `DOCS_LOAD_TOKEN`,
`EVAL_ACCESS_TOKEN`), how to back up the reviews database, rollback (`git revert`
and push), a budget alert and Azure OpenAI quota, and Application Insights.
Staging needs a second App Service app or a plan that supports slots (Standard
or higher).

Gunicorn worker, timeout, and chat-model `max_tokens` / timeout settings are
being chosen separately. Confirm those names before adding them in the portal,
and keep the platform health check on `/healthz`.

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
