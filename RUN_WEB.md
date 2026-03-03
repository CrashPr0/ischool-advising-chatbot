# Running the Web Interface

## 🚀 Quick Start (2 minutes)

```bash
# 1. Install dependencies (if not already done)
pip install -r requirements.txt

# 2. Set your OpenAI key
# Edit .env file:
# OPENAI_API_KEY=sk-your-key-here

# 3. Run the web server
python app.py

# 4. Open in browser
# http://localhost:5000
```

## 💻 What You'll See

A beautiful chat interface with:
- **Left side (70%)**: Main chat area with message history
- **Right side (30%)**: Sidebar with quick actions and stats

## 🎯 How to Use

1. **Type a question** in the input box
2. **Press Enter** or click the Send button
3. **Wait for response** (usually 1-2 seconds)
4. **See the source** badge showing where answer came from:
   - 🟦 **Knowledge Base** = Instant hardcoded answer
   - 🟩 **RAG** = Answer from documents
   - 🟧 **LLM** = AI-generated answer

## 🎮 Sidebar Buttons

### Load Documents 📄
- Loads sample advising documents
- Then questions can use document context
- Try asking: "Tell me about the capstone project"

### Common Questions ❓
- Shows all available hardcoded questions
- Click one to auto-fill your message input
- Great reference for what questions work

### Clear History 🗑️
- Deletes entire conversation
- Useful for testing or starting fresh

## 💡 Try These Questions

### Knowledge Base (Instant)
- "What do I need to graduate?"
- "How many units do I need?"
- "Hello"

### LLM (1-2 seconds)
- "How should I plan my course schedule?"
- "What career opportunities are there?"

### RAG (After loading docs)
- "Tell me about the MLIS program"
- "What are the capstone requirements?"

## 📊 Statistics Panel

Shows:
- **Messages**: Count of messages sent
- **KB Questions**: Number of hardcoded Q&A pairs
- **Last Response**: Source and confidence of last answer

## 🔧 Troubleshooting

### Page won't load
```bash
# Make sure Flask is running
# You should see: Running on http://127.0.0.1:5000

# Check port 5000 is available
# Windows: netstat -ano | findstr :5000
# Mac/Linux: lsof -i :5000
```

### API error
- Check your `.env` has `OPENAI_API_KEY=sk-...`
- Make sure key is valid (check OpenAI dashboard)
- Newer key starts with `sk-`

### Slow responses
- First response takes ~10 seconds (model loads)
- Subsequent responses are faster
- Check your internet connection

### Templates not updating
- Flask caches templates
- Stop the server (Ctrl+C)
- Start again: `python app.py`

## 📱 Mobile Friendly

Works great on phones and tablets!
- Interface adapts to screen size
- Touch-friendly buttons
- Try it on your phone

## 🌐 Deploy to Azure

Once you're happy locally:
1. Follow [AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md)
2. Get a public URL
3. Share with your team!

## ⚙️ Configuration

Edit `.env` to change:
```env
# Which LLM to use: openai, anthropic, huggingface
DEFAULT_LLM_PROVIDER=openai

# API keys for each provider
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=claude-...
HUGGINGFACE_API_KEY=hf_...

# Vector store location
VECTOR_STORE_PATH=./data/chroma
```

## 🆘 Getting Help

- **Quick setup?** → [QUICKSTART.md](QUICKSTART.md)
- **Web development?** → [WEB_DEVELOPMENT.md](WEB_DEVELOPMENT.md)
- **Deploying?** → [AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md)
- **General info?** → [SETUP.md](SETUP.md)

---

**Enjoy your chatbot! 🎉**
