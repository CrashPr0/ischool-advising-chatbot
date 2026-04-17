# iSchool Senior Advising Chatbot (RAG)

A LangChain-based advising chatbot for SJSU iSchool students. It supports:

- **Knowledge base answers** (instant responses for common questions)
- **RAG** (retrieves relevant documents and grounds the answer)
- **Multiple LLM providers** (OpenAI / Anthropic / Hugging Face)
- **CLI** (`src/main.py`) and **Web UI** (`app.py` Flask + `templates/` + `static/`)

---

## Quick start (CLI)

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Set at least one API key in `.env`, then run:

```bash
cd src
python main.py
```

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

## Advising documents (for RAG)

- **Folder**: `data/advising_docs/`
- **Formats**: `.txt` and `.pdf`
- **Recommended style**:
  - Use clear headings (`#`, `##`)
  - Keep docs scoped (ideally 500–2000 words per topic)
  - Include concrete details (course codes, unit counts, GPA thresholds, deadlines)

After adding/updating docs, rebuild the index:

- **CLI**: run `docs`
- **Web**: click “Load Documents” (or call `POST /api/load-docs`)

If the vector store gets into a bad state, deleting `data/chroma/` and re-running `docs` is the quickest reset.

---

## Tests / formatting

```bash
pytest -v
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

## External documents

- iSchool Advising Chatbot Rubric: `https://docs.google.com/spreadsheets/d/1kXhvObOL_13er0D4eyMBNfHel7tIFF4seUtL0yQtMk8/edit?usp=sharing`
- ISDA Advising Support Chatbot – Smoke Test Question Set: `https://docs.google.com/document/d/1KON-OufGsHjJpwY_hQp5m_doXAMw-rn98g4Oz9YvCQY/edit?usp=sharing`
- Figma Website Design `https://www.figma.com/design/J5cIoGXTAs7K07Q5X988Z6/iSchool-Chatbot-Site-RD?node-id=0-1&p=f&t=z4ZYAu1mDT8v6J8y-0`
- iSchool Advising Chatbot Azure Website: `https://ischooladvisingchatbot-dx4ynl.azurewebsites.net/`
---

## License

See `LICENSE`.
