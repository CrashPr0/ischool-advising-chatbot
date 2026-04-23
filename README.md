# iSchool Senior Advising Chatbot (RAG)

A LangChain-based advising chatbot for SJSU iSchool students. It supports:

- **Knowledge base answers** (instant responses for common questions)
- **RAG** (retrieves relevant documents and grounds the answer)
- **Multiple LLM providers** (OpenAI / Azure OpenAI / Anthropic / Hugging Face / Gemini)
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
For testing it own your own, you would need to make sure that Python is installed to run main.py and input an API key for chatbot uses. If an API key is not provided, then it will only answer based off of sentence structures. For testing the Azure site (using the current main branch), you can run the link in the “External Documents” section (School Advising Chatbot Azure Website) to test the current main branch.

---
# Installation
## Quick start (CLI)

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
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
# Build (reuses existing chroma dir unless --clean)
python -m src.build_index --docs ./data/advising_docs

# Clean rebuild
python -m src.build_index --docs ./data/advising_docs --clean
```

The index persists to `Config.VECTOR_STORE_PATH` (default: `./data/chroma`).

### CLI commands

- `help` - show commands
- `docs` - create/load sample docs + build vector index
- `questions` - list hardcoded questions
- `clear` - clear conversation history
- `exit` - quit

---

## Quick start (Web UI)

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open `http://localhost:5000`.

### Web API endpoints

- `GET /api/status`
- `POST /api/chat` `{ "message": "..." }`
- `GET /api/history`
- `POST /api/clear-history`
- `POST /api/load-docs`
- `GET /api/questions`

---

## Configuration (`.env`)

Minimum configuration is one provider key plus a default:

```env
OPENAI_API_KEY=...
DEFAULT_LLM_PROVIDER=openai
```

Optional:

```env
ANTHROPIC_API_KEY=...
HUGGINGFACE_API_KEY=...
```

Important: **Never commit `.env`** (use `.env.example` as the template).

---

## Tests / formatting

```bash
python -m pytest -q
black .
flake8 .
```

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
Currently we are mostly using Python as the bulk of the code, HTML for organizing the website, and CSS and Javascript as supplementaries. We will use GPT 5.2 as our API and are currently using Azure to host the site for the time being, but also manage our project.

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
