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
## Proof of Concept Scope
Currently on the main branch, there is an Azure site where it takes in inputs in a chatbox and answers based on sentence structures and its own thought process. On a different branch there is no Azure site, but a filter to make sure that the students are not asking inappropriate questions. As a whole, the website needs a bit of tweaking to make sure the links are correct, the sentence structures is not the main focus, and the chat filter actually works. We also need a bit more information for the chatbot to use so that it can answer questions correctly. If it does not have the required information, the chatbot will state so.

---
## Prerequisites/Running the PoC
Use Python 3.11 or newer and configure at least one supported provider in
`.env`. The CLI and web app fail clearly when no provider is configured; the
knowledge base remains useful for tests and direct library callers without
making a network request. The deployed Azure URL in “External documents” can
be used to test the current hosted PoC.

---
# Installation
## Quick start (CLI)

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
```

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

The scraper is **not** meant to run at query-time. It writes files your existing `DocumentProcessor` already ingests.

1. Put seed URLs in `data/advising_docs/scrape/seed_urls.txt` (one URL per line)
2. Run:

```bash
python -m src.scrape_ischool --docs-root ./data/advising_docs --seeds ./data/advising_docs/scrape/seed_urls.txt --allow-host ischool.sjsu.edu --allow-host ischoolapps.sjsu.edu --allow-host www.sjsu.edu --max-urls 200 --delay-s 1.0
```

Optional: also pass a sitemap (bounded) to add more pages:

```bash
python -m src.scrape_ischool --docs-root ./data/advising_docs --seeds ./data/advising_docs/scrape/seed_urls.txt --sitemap https://ischool.sjsu.edu/sitemap.xml --allow-host ischool.sjsu.edu --allow-host ischoolapps.sjsu.edu --allow-host www.sjsu.edu --max-urls 200 --delay-s 1.0
```

3. Rebuild the vector index (see below).

The crawler is bounded by host, URL, response-size, timeout, and delay limits.
It never runs during a chat request.

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

The index persists to `Config.VECTOR_STORE_PATH` (default: `./data/chroma`).
The web rebuild endpoint is admin-only; use the CLI for scheduled or local
indexing jobs.

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
- `GET /healthz` (liveness probe; does not require provider credentials)
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
retrieved once with a local embedding model, then the Azure deployments named
`gpt-4o-mini` and `gpt-5.6-luna` receive the exact same system prompt and
document excerpts. This keeps Azure embedding rate limits and retrieval
variance out of the model comparison. Invocation order alternates to reduce
connection-warming bias. The generated Markdown report includes both answers,
retrieved sources, latency, reported token usage, and a 1–7 human-review rubric.

Both models use the existing Azure endpoint and key. The defaults assume the
two deployment names above; override them with `--left-model` and
`--right-model` if the names differ in your Azure project. Transient Azure
429/time-out responses use bounded exponential backoff, and the benchmark
paces question pairs with `--request-delay` (3 seconds by default).

Then run the eight-question benchmark. The first run downloads the local
`all-MiniLM-L6-v2` embedding model and builds an isolated evaluation index:

```bash
python -m src.model_benchmark --build-index
```

For a cheap smoke test before the full run:

```bash
python -m src.model_benchmark --build-index --limit 1
```

Outputs are local and ignored by Git:

- `evals/results/latest.json` — machine-readable results
- `evals/results/latest.md` — side-by-side human review sheet
- `evals/vector_store/` — local evaluation-only retrieval index

Edit `evals/advising_questions.json` to add questions or reviewer expectations.
The supplied set includes ordinary registration questions, evidence-boundary
checks that catch graduate/undergraduate policy leakage, and a Simplified
Chinese question. GPT-5.6 Luna's current API model ID and capabilities are
documented on the [official OpenAI model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

### Human evaluation workspace

After generating `evals/results/latest.json`, start the Flask app and open
`http://localhost:5000/evaluation`. The workspace gives reviewers a blind,
per-reviewer randomized Candidate A/B comparison with:

- a required 1–7 score and 1–3 sentence rationale for every response;
- per-reviewer progress, editable saved reviews, and a next-incomplete shortcut;
- model identity reveal only after both responses to a question are scored;
- aggregate model results and CSV audit export only after the run is complete.

Reviews are stored in the ignored SQLite file `evals/reviews.sqlite3`. Localhost
can use the workspace without a shared secret. For a deployed handoff, configure:

```env
EVAL_ACCESS_TOKEN=<long-random-shared-secret>
EVAL_RESULTS_PATH=/home/data/evaluation/latest.json
EVAL_DB_PATH=/home/data/evaluation/reviews.sqlite3
```

The access token is exchanged for a signed, HTTP-only session and is never
stored in browser storage. The workspace also recognizes Azure App Service Easy
Auth/Entra user headers, so the shared token can be retired when organizational
sign-in is enabled. SQLite is appropriate for one App Service instance; use a
managed database before scaling the app horizontally.

---

## Deploy to Azure (App Service)

This repo includes `Procfile` (`gunicorn app:app`) and an `app.py` that reads `PORT`.

High-level steps:

1. `az login`
2. Create a resource group + App Service plan + web app
3. Set app settings (environment variables) in Azure Portal:
   - `OPENAI_API_KEY` (and/or other provider keys)
   - `DEFAULT_LLM_PROVIDER`
   - `WEBSITES_PORT=5000`
4. Deploy from GitHub (or ZIP deploy)

---

## Repository structure

```
.
├── app.py                 # Flask web server
├── src/                   # Chatbot logic (RAG + providers + config)
├── templates/             # Web UI HTML (Flask template)
├── static/                # Web UI assets (CSS/JS/images)
├── tests/                 # Unit tests
├── requirements.txt
├── .env.example
└── README.md
```

---
## Running the PoC and Demo
Below in "External documents", there is a link marked for use (the Azure link). In this section, there are screenshots of the website to show it working along with descriptions:

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
- iSchool Advising Chatbot Azure Website (use for PoC): `https://ischooladvisingchatbot-dx4ynl.azurewebsites.net/`
---

## License

See `LICENSE`.
