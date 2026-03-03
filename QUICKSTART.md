# Quick Start Guide

Get the iSchool Advising Chatbot running in 5 minutes!

## 1. Setup (2 minutes)

```bash
# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## 2. Configure API Keys (1 minute)

```bash
# Copy and edit .env
cp .env.example .env

# Add your OpenAI API key (or other provider):
# OPENAI_API_KEY=sk-...
```

Get an API key: https://platform.openai.com/api-keys

## 3. Run the Chatbot (2 minutes)

```bash
cd src
python main.py
```

## 4. Test Common Questions

Try these in the chatbot:

```
You: What do I need to graduate?
[Instant response from knowledge base]

You: hello
[Greeting response]

You: What courses do I need?
[LLM generated response]
```

## 5. Load Documents (Optional)

```
You: docs
[Loads sample documents]

You: Tell me about the capstone project
[RAG-enhanced response using documents]
```

## Features to Try

### Knowledge Base (Instant)
- "What do I need to graduate?"
- "How many units do I need?"
- "When can I graduate?"
- "What is the GPA requirement?"
- "Hello"

### Document Retrieval (RAG)
- Place `.txt` or `.pdf` files in `data/advising_docs/`
- Run `docs` command to load them
- Ask questions about your documents

### Multi-LLM Support
Change `DEFAULT_LLM_PROVIDER` in `.env` to test:
- `openai` - GPT-3.5/GPT-4
- `anthropic` - Claude
- `huggingface` - Open-source models

## Troubleshooting

**"No LLM providers configured"**
- Check `.env` file exists with API key
- Verify `OPENAI_API_KEY` (or your chosen provider)

**Vector store errors**
- Delete `data/chroma/` folder
- Run `docs` again to rebuild

**Import errors**
- Ensure you're in virtual environment
- Run `pip install -r requirements.txt` again

## Next Steps

1. Add your advising documents to `data/advising_docs/`
2. Customize knowledge base in `src/knowledge_base.py`
3. Test with different LLM providers
4. Build a web interface (Flask/FastAPI)

## Need Help?

- See `SETUP.md` for detailed documentation
- Check `src/main.py` for CLI commands
- Review `src/knowledge_base.py` to add custom Q&A
