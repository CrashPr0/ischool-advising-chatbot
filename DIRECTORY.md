# Project Directory Structure Guide

This document explains the project structure and where to place files.

## Directory Layout

```
group-project-team-22/
├── src/                          # Source code
│   ├── __init__.py               # Package initialization
│   ├── config.py                 # Configuration management
│   ├── llm_providers.py          # LLM provider factory
│   ├── chatbot.py                # Basic chatbot (legacy)
│   ├── knowledge_base.py         # Hardcoded Q&A pairs
│   ├── document_processor.py     # Document loading & indexing
│   ├── rag_chatbot.py            # RAG-enhanced chatbot ***MAIN***
│   └── main.py                   # CLI entry point ***RUN THIS***
│
├── tests/                        # Unit tests
│   ├── __init__.py
│   ├── test_config.py            # Config tests
│   └── test_knowledge_base.py    # Knowledge base tests
│
├── data/                         # Data directory (not in git)
│   ├── advising_docs/            # Your advising documents (place here!)
│   │   ├── degree_requirements.txt    # Sample document
│   │   ├── course_descriptions.txt
│   │   ├── policies/
│   │   │   ├── academic_policies.txt
│   │   │   └── enrollment_rules.txt
│   │   └── guides/
│   │       └── graduation_guide.pdf
│   │
│   └── chroma/                   # Vector database (auto-generated)
│       ├── data.parquet
│       └── index.bin
│
├── docs/                         # Documentation (markdown files)
│   └── [Your docs here]
│
├── SCHEDULE-195.md               # Project schedule
├── README.md                     # Main README
├── SETUP.md                      # Detailed setup guide
├── QUICKSTART.md                 # Quick start guide (5 minutes)
├── DIRECTORY.md                  # This file
├── examples.py                   # Example usage script
├── requirements.txt              # Python dependencies
├── .env.example                  # Example environment file
├── .env                          # Your actual config (DON'T COMMIT)
├── .gitignore                    # Git ignore rules
└── LICENSE                       # License file
```

## Key Directories

### `/src` - Source Code

**Main entry point:**
- `main.py` - Run this to start the chatbot CLI
  
**Core modules:**
- `config.py` - Handles environment variables and settings
- `llm_providers.py` - Factory for creating LLM instances
- `knowledge_base.py` - Hardcoded question-answer pairs
- `document_processor.py` - Loads and indexes documents
- `rag_chatbot.py` - Main RAG chatbot implementation

**Legacy (for reference):**
- `chatbot.py` - Basic chatbot without RAG (v0.1)

### `/tests` - Unit Tests

Run tests with:
```bash
pytest tests/ -v
```

### `/data` - Data Storage

**Place your advising documents here:**
```
data/advising_docs/
├── program_requirements.txt
├── course_catalog.txt
├── graduation_checklist.txt
└── policies/
    └── academic_standards.txt
```

**Vector database (auto-created):**
- `chroma/` - Persistent vector store for document embeddings

### `/docs` - Documentation

Store project documentation here:
- Meeting notes
- Architecture diagrams
- API specifications
- User guides

## File Naming Conventions

**Python files:**
- Use snake_case: `document_processor.py`, `rag_chatbot.py`
- Test files: `test_*.py` (auto-discovered by pytest)

**Configuration files:**
- `.env` - Local environment variables (DON'T COMMIT)
- `.env.example` - Template for .env (DO COMMIT)

**Documentation:**
- `.md` - Markdown files in root or `/docs`

## What's Ignored (`.gitignore`)

These files/folders are NOT committed to Git:
- `.env` - Your API keys
- `__pycache__/` - Python cache
- `.pytest_cache/` - Test cache
- `venv/` or `ENV/` - Virtual environment
- `data/chroma/` - Generated vector store
- `*.log` - Log files
- `.vscode/` - IDE settings
- `.DS_Store` - macOS files

## Setting Up for Development

### 1. Clone repository
```bash
git clone <repo-url>
cd group-project-team-22
```

### 2. Create virtual environment
```bash
python -m venv venv
source venv/bin/activate  # macOS/Linux
# OR
venv\Scripts\activate     # Windows
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment
```bash
cp .env.example .env
# Edit .env with your API keys
```

### 5. Run the chatbot
```bash
cd src
python main.py
```

## Adding Your Advising Documents

### Step 1: Prepare documents
Supported formats:
- `.txt` - Plain text files
- `.pdf` - PDF documents

Recommended structure:
```
data/advising_docs/
├── requirements/
│   ├── MLIS_requirements.txt
│   └── MITM_requirements.txt
├── courses/
│   ├── course_descriptions.txt
│   └── prerequisites.txt
└── policies/
    └── graduation_policy.txt
```

### Step 2: Load documents
In the chatbot:
```
You: docs
[Loads and indexes all documents]
```

### Step 3: Ask questions
```
You: What are the program requirements?
[RAG will use your documents to answer]
```

## Development Workflow

### Making Changes

1. **Edit files in `/src`**
   - Write code following Python conventions
   - Use snake_case for functions/variables

2. **Test locally**
   ```bash
   cd src
   python main.py
   ```

3. **Run unit tests**
   ```bash
   pytest tests/ -v
   ```

4. **Commit to Git**
   ```bash
   git add src/
   git commit -m "Description of changes"
   git push
   ```

### File Change Checklist

Before committing, verify:
- ✓ Never commit `.env` file
- ✓ Your code passes `pytest` tests
- ✓ Your code follows PEP 8 (run `black` and `flake8`)
- ✓ Documentation is updated if needed

## Project Size

As of now:
- **Code files:** ~500 lines (src/)
- **Test files:** ~150 lines (tests/)
- **Documentation:** ~1500 lines total
- **Total Python modules:** 8

## Common Commands

```bash
# Run chatbot
cd src && python main.py

# Run tests
pytest tests/ -v

# Format code (PEP 8)
black src/ tests/

# Check code style
flake8 src/ tests/

# Run example
python examples.py

# List installed packages
pip list
```

## Troubleshooting

**"No module named 'langchain'"**
- Run: `pip install -r requirements.txt`
- Ensure you're in virtual environment

**"Vector store not found"**
- Run: `docs` command in chatbot
- Or manually: `python -c "from document_processor import setup_sample_docs; setup_sample_docs()"`

**Changes not showing up**
- Python may be caching imports
- Restart chatbot/terminal and try again

## Questions?

See:
- `SETUP.md` - Detailed setup guide
- `QUICKSTART.md` - 5-minute quick start
- `examples.py` - Code examples
- `src/main.py` - CLI implementation
