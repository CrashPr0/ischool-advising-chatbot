# Deploy to Azure - Complete Guide

Deploy your iSchool Advising Chatbot on Azure using your student free credits ($100/month)!

## Prerequisites

- ✅ Azure account (free tier with student credits)
- ✅ Docker installed locally (optional for testing)
- ✅ GitHub account (for source control)
- ✅ Local copy of the chatbot code

## Step 1: Set Up Azure Account

### 1.1 Claim Your Student Free Credits

1. Go to [Azure for Students](https://azure.microsoft.com/en-us/free/students/)
2. Sign in with your SJSU email
3. Verify your student status
4. You'll get **$100 free credits** (refreshed annually)

### 1.2 Create Resource Group

1. Go to [Azure Portal](https://portal.azure.com)
2. Search for "Resource Groups"
3. Click "Create"
4. **Name:** `ischool-chatbot-rg`
5. **Region:** `West US 2` (closest to California)
6. Click "Review + Create" → "Create"

## Step 2: Option A - Deploy with Azure App Service (Easiest)

### 2.1 Set Up Azure CLI

```bash
# Install Azure CLI
# Windows: https://aka.ms/installazurecliwindows
# macOS: brew install azure-cli
# Linux: curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# Login to Azure
az login
```

### 2.2 Prepare Application

1. **Update** `app.py` port to use environment variable:

The app already handles this - it reads `PORT` env var!

2. **Add** `Procfile` for Azure App Service:

```
web: gunicorn app:app
```

3. **Update** `.gitignore` (already done):

Ensures `.env` with API keys isn't committed

### 2.3 Deploy to App Service

```bash
# Create App Service Plan (B1 = free tier eligible)
az appservice plan create \
  --name ischool-chatbot-plan \
  --resource-group ischool-chatbot-rg \
  --sku B1 \
  --is-linux

# Create Web App
az webapp create \
  --resource-group ischool-chatbot-rg \
  --plan ischool-chatbot-plan \
  --name ischool-chatbot-prod \
  --runtime "PYTHON:3.10"

# Configure environment variables
az webapp config appsettings set \
  --resource-group ischool-chatbot-rg \
  --name ischool-chatbot-prod \
  --settings WEBSITES_PORT=5000

# Deploy from GitHub
az webapp deployment github-actions add \
  --resource-group ischool-chatbot-rg \
  --name ischool-chatbot-prod \
  --repo <YOUR-GITHUB-REPO-URL> \
  --branch main
```

### 2.4 Set API Keys in Azure

1. Go to [Azure Portal](https://portal.azure.com)
2. Find your App Service: `ischool-chatbot-prod`
3. Go to **Settings → Configuration**
4. Add Environment Variables (click "+ New application setting"):

```
OPENAI_API_KEY = sk-...
DEFAULT_LLM_PROVIDER = openai
VECTOR_STORE_PATH = /tmp/chroma
```

5. Click "Save"

**Your app will restart automatically!**

### 2.5 Access Your Chatbot

```
https://ischool-chatbot-prod.azurewebsites.net
```

---

## Step 2: Option B - Deploy with Azure Container Instances

### 2.1 Build Docker Image

```bash
# Create container registry
az acr create \
  --resource-group ischool-chatbot-rg \
  --name ischoolchatbot \
  --sku Basic

# Login to container registry
az acr login --name ischoolchatbot

# Get login server
az acr show \
  --name ischoolchatbot \
  --query loginServer \
  --output table
```

### 2.2 Build and Push Image

```bash
# Replace <login-server> with output from above
cd your-repo-directory

docker build -t ischool-chatbot:latest .

docker tag ischool-chatbot:latest <login-server>/ischool-chatbot:latest

docker push <login-server>/ischool-chatbot:latest
```

### 2.3 Deploy Container

```bash
# Create container instance
az container create \
  --resource-group ischool-chatbot-rg \
  --name ischool-chatbot \
  --image <login-server>/ischool-chatbot:latest \
  --cpu 1 \
  --memory 2 \
  --registry-login-server <login-server> \
  --registry-username <username> \
  --registry-password <password> \
  --ports 5000 \
  --dns-name-label ischool-chatbot \
  --environment-variables \
    OPENAI_API_KEY=sk-... \
    DEFAULT_LLM_PROVIDER=openai
```

### 2.4 Access Your Chatbot

```
http://ischool-chatbot.westus2.azurecontainers.io:5000
```

---

## Step 3: Configure API Keys

### 3.1 Add OpenAI API Key

1. Get your key: https://platform.openai.com/api-keys
2. In Azure Portal, set environment variable:
   - **OPENAI_API_KEY** = `sk-...`

### 3.2 Optional: Add Other Providers

```
ANTHROPIC_API_KEY = claude-key...
HUGGINGFACE_API_KEY = hf_...
```

---

## Step 4: Add Custom Documents

### 4.1 Upload via Container Storage

**For App Service:**

1. Connect via SFTP:
   ```bash
   az webapp deployment list-publishing-profiles \
     --name ischool-chatbot-prod \
     --resource-group ischool-chatbot-rg \
     --query [0].publishUrl
   ```

2. Upload files to `/data/advising_docs/` using SFTP client

3. Restart app → run "docs" command in chatbot

### 4.2 Alternative: Use App Service Editor

1. Go to [Azure Portal](https://portal.azure.com)
2. App Service → **Development Tools → App Service Editor**
3. Navigate to `data/advising_docs/`
4. Upload `.txt` or `.pdf` files

---

## Step 5: Monitor Your Deployment

### 5.1 View Logs

```bash
# App Service logs
az webapp log tail \
  --name ischool-chatbot-prod \
  --resource-group ischool-chatbot-rg

# Or in Azure Portal:
# App Service → "App Service logs" → "Logs stream"
```

### 5.2 Monitor Performance

1. Go to [Azure Portal](https://portal.azure.com)
2. App Service → **Monitoring → Metrics**
3. View CPU, Memory, HTTP requests

### 5.3 Set Up Alerts

1. **Monitoring → Alerts**
2. **Create alert rule**
3. Condition: HTTP 5xx errors > 5
4. Set email notification

---

## Step 6: Cost Optimization

### 6.1 Keep Within Free Tier

**Free resources:**
- ✅ 1 App Service Plan (B1 tier) = **FREE**
- ✅ 1 Web App = **FREE**
- ✅ 1 Application Insights = **free tier**

**Paid resources to avoid:**
- ❌ Scaling to multiple instances
- ❌ Premium tier (P1, P2, P3)
- ❌ Continuous deployment pipelines beyond quota

### 6.2 Monitor Credits

1. Go to [Azure Portal](https://portal.azure.com)
2. **Cost Management + Billing**
3. **View budgets and expenditure**

### 6.3 Set Budget Alert

1. **Cost Management → Budgets**
2. **Create new budget**
3. Set limit: `$80` (leave $20 buffer)
4. Alert at 80%

---

## Step 7: GitHub Actions CI/CD (Optional)

Automatically deploy on code push!

### 7.1 Create GitHub Workflow

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Azure

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v2

    - name: Deploy to Azure Web App
      uses: azure/webapps-deploy@v2
      with:
        app-name: 'ischool-chatbot-prod'
        publish-profile: ${{ secrets.AZURE_WEBAPP_PUBLISH_PROFILE }}
        package: .
```

### 7.2 Add Publish Profile

1. App Service → **Get publish profile** (download XML)
2. GitHub → **Settings → Secrets → New repository secret**
3. **Name:** `AZURE_WEBAPP_PUBLISH_PROFILE`
4. **Value:** Contents of downloaded XML
5. Save

Now pushes to `main` branch auto-deploy!

---

## Troubleshooting

### App won't start

**Check logs:**
```bash
az webapp log tail --name ischool-chatbot-prod --resource-group ischool-chatbot-rg
```

**Common issues:**
- Missing `OPENAI_API_KEY` → Set in Configuration
- Port not 5000 → Verify `WEBSITES_PORT=5000`
- Module not found → Ensure all packages in `requirements.txt`

### High memory usage

- Reduce `sentence-transformers` model size
- Or switch to smaller embeddings: `"all-MiniLM-L6-v2"` (current, lightweight)
- Disable document processing if not used

### API rate limits

- OpenAI: Check quota at https://platform.openai.com/account/rate-limits
- Anthropic: Similar dashboard
- Upgrade plan if needed

---

## Upgrading Beyond Free Tier

When you run out of credits or need more:

### Option 1: Pay-As-You-Go
- B1 App Service: **~$11/month**
- Minimal for production

### Option 2: Use Spot Instances
- 70% discount on compute
- Can be interrupted
- Good for non-critical apps

### Option 3: Alternative Platforms
- **Heroku**: $7/month dyno
- **Railway**: $5 credit/month + usage
- **Vercel**: Free with serverless backend elsewhere

---

## Next Steps

1. ✅ Deploy to Azure
2. 🔄 Customize knowledge base
3. 📄 Add your advising documents
4. 🧪 Test all features
5. 📊 Monitor performance
6. 🚀 Share URL with team

---

## Useful Resources

- [Azure Free Account](https://azure.microsoft.com/en-us/free/)
- [Azure for Students](https://azure.microsoft.com/en-us/free/students/)
- [Azure App Service Docs](https://learn.microsoft.com/en-us/azure/app-service/)
- [Azure Container Instances](https://learn.microsoft.com/en-us/azure/container-instances/)
- [Azure Pricing Calculator](https://azure.microsoft.com/en-us/pricing/calculator/)

---

## Questions?

Check the main [SETUP.md](SETUP.md) or ask your team!
