# Web Interface & Local Testing

Run the web interface locally before deploying to Azure!

## Local Development

### 1. Install Dependencies

```bash
# Make sure you're in virtual environment first
python -m venv venv
venv\Scripts\activate  # Windows: venv\Scripts\activate

# Install all packages including Flask
pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
# Copy example
cp .env.example .env

# Edit .env with your OpenAI API key
OPENAI_API_KEY=sk-your-key-here
DEFAULT_LLM_PROVIDER=openai
```

### 3. Run Locally

```bash
# From project root (NOT from src/)
python app.py
```

You should see:
```
 * Running on http://127.0.0.1:5000
 * WARNING: This is a development server. Do not use it in production.
```

### 4. Open in Browser

Go to: **http://localhost:5000**

## Testing the Web Interface

### Test Hardcoded Answers

1. Type: "What do I need to graduate?"
2. Should get instant response from Knowledge Base
3. Source badge shows: `KNOWLEDGE_BASE (100%)`

### Test LLM Responses

1. Type: "How can I plan my course schedule?"  
2. Should get LLM-generated response
3. Source badge shows: `LLM (50%)`

### Load Sample Documents

1. Click **"Load Documents"** button in sidebar
2. Should say "Documents loaded successfully!"
3. Sample file created at: `data/advising_docs/degree_requirements.txt`

### Test Document Retrieval (RAG)

After loading documents:
1. Type: "Tell me about the capstone project"
2. Should retrieve documents
3. Source badge shows: `RAG (70%)` with document count

### Test Common Questions

1. Click **"Common Questions"** in sidebar
2. Modal shows all available hardcoded questions
3. Click a question to populate input field
4. Send message

### Clear History

1. Click **"Clear History"** button
2. Confirm dialog appears
3. Chat area resets to welcome message
4. Message count resets to 0

## Docker Testing (Optional)

### Build Docker Image

```bash
docker build -t ischool-chatbot:latest .
```

### Run Container Locally

```bash
docker run -p 5000:5000 \
  -e OPENAI_API_KEY=sk-... \
  -e DEFAULT_LLM_PROVIDER=openai \
  ischool-chatbot:latest
```

Then visit: http://localhost:5000

### Test Container Health

```bash
curl http://localhost:5000/api/status
```

Should return:
```json
{
  "status": "ready",
  "available_providers": ["openai"],
  "hardcoded_questions": 9
}
```

## API Endpoints Reference

Test these endpoints with curl or Postman:

### Status Check
```bash
curl http://localhost:5000/api/status
```

### Send Message
```bash
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What do I need to graduate?"}'
```

### Get History
```bash
curl http://localhost:5000/api/history
```

### Load Documents
```bash
curl -X POST http://localhost:5000/api/load-docs
```

### Get Hardcoded Questions
```bash
curl http://localhost:5000/api/questions
```

### Clear History
```bash
curl -X POST http://localhost:5000/api/clear-history
```

## Troubleshooting

### 404 on /
- Ensure `templates/index.html` exists
- Check file is in `templates/` (not `src/templates/`)
- Restart Flask

### Templates not updating
- Flask caches templates
- Restart the app: Ctrl+C, then `python app.py`
- Or set `FLASK_ENV=development` for auto-reload

### CORS errors
- Flask-CORS already enabled
- No CORS headers needed for local testing
- Will work in cloud (different domains)

### API not responding
```bash
# Check network
curl -v http://localhost:5000/api/status

# Check Flask is running
# Should see: "Running on http://127.0.0.1:5000"

# Verify port 5000 is free
netstat -ano | findstr :5000  # Windows
lsof -i :5000                 # macOS/Linux
```

### Memory usage high
- Normal for first load (loads embeddings model)
- Will stabilize after ~30 seconds
- Check: `sentence-transformers` is lightweight model

### Slow responses
- First query is slow (model initialization)
- Subsequent queries should be fast
- OpenAI API adds slight latency (~1-2s)

## Performance Tips

### Development
- Use `python app.py` for development
- Auto-reload: Install `flask-reload` or use debugger

### Production (Azure)
- Uses `gunicorn` (production WSGI server)
- Multiple workers: `gunicorn --workers 4 app:app`
- Configured in `Procfile`

### Optimization
- Cache embeddings after first load
- Reuse vector store connection
- Already implemented in `RAGAdvisingChatbot`

## File Structure for Web

```
project-root/
├── app.py                    # Flask application
├── requirements.txt          # Dependencies (includes Flask)
├── Procfile                  # Azure deployment config
├── Dockerfile               # Container config
├── templates/
│   └── index.html           # Main web page
├── static/
│   ├── style.css            # Styling
│   └── script.js            # Frontend logic
├── src/
│   ├── rag_chatbot.py       # Core chatbot
│   ├── knowledge_base.py    # Hardcoded Q&A
│   └── ... (other modules)
└── data/
    └── advising_docs/       # Documents for RAG
```

## Next Steps

1. ✅ Run locally: `python app.py`
2. ✅ Test all features
3. ✅ Add custom documents to `data/advising_docs/`
4. ✅ Configure Azure deployment
5. ✅ Deploy with `AZURE_DEPLOYMENT.md`

## Resources

- [Flask Documentation](https://flask.palletsprojects.com/)
- [Testing Flask Apps](https://flask.palletsprojects.com/en/2.3.x/testing/)
- [Gunicorn Documentation](https://gunicorn.org/)
