# Operations log

Every production change made outside git gets an entry here. That includes
Azure portal edits, app settings, secret rotations, and startup-command
changes. Record when it happened, who did it, and what changed.

Secret values are never recorded. Setting names are fine. If a secret has to
be identified, the last four characters are the most this file should contain.

### 2026-09-26 (Sat), early morning PT, by Christopher Velez (sprint)

- Reviews database backup: before merging PR #7, verified `EVAL_DB_PATH=/home/data/evaluation/reviews.sqlite3` over Kudu SSH (16 rows) and made a consistent copy with the SQLite backup API at `/home/data/evaluation/reviews-backup-20260926.sqlite3` (16 rows).
- Startup command (Configuration > Stack settings): changed from `gunicorn --bind=0.0.0.0:8000 --timeout 600 --workers 1 app:app` to `gunicorn --config gunicorn.conf.py app:app`, so the repo's gunicorn.conf.py (workers, timeouts, bind to $PORT) is the single source of truth, matching Procfile and Dockerfile.
- HTTPS Only: turned On (http now 301-redirects to https); needed for the Secure session cookie.
- App setting `VECTOR_STORE_PATH` (was `/home/site/wwwroot/data/chroma`, which is replaced on every zip deploy) deleted, so the durable default `/home/data/chroma` from PR #7 applies. Verified after the #7 deploy: `/readyz` reports receipt at `/home/data/chroma/index_build.json` matching the release.
- Azure OpenAI key rotation (resource ISchoolAdvisingBot): the key in use had appeared in plaintext in local AI-assistant logs. App setting `AZURE_OPENAI_API_KEY` switched from KEY 1 to KEY 2, app restarted and chat verified, then KEY 1 regenerated. Developer .env updated.
- `DOCS_LOAD_TOKEN` rotation (same reason): new random 43-character token set in the App Service app setting, the GitHub Actions secret `DOCS_LOAD_TOKEN`, and the developer .env, all at the same time. The first deploy after this entry exercises the new token end to end.
- App settings present after these changes (names only): AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_EMBEDDING_DEPLOYMENT, AZURE_OPENAI_ENDPOINT, DEFAULT_LLM_PROVIDER, DOCS_LOAD_TOKEN, EVAL_ALLOWED_REVIEWERS, EVAL_DB_PATH, OPENAI_MODEL, PYTHONUNBUFFERED, SCM_DO_BUILD_DURING_DEPLOYMENT, SESSION_COOKIE_SECURE, SESSION_SECRET_KEY, WEBSITE_HTTPLOGGING_RETENTION_DAYS, WEBSITES_PORT. `EVAL_ACCESS_TOKEN` is not set.
- Related merges the same night: #7 (a7426e4, durable storage + /readyz), #11 (f7d82c3, chat history + stale-index 503).
- Cost budget: created `chatbot-monthly-25` on the Azure for Students subscription (subscription scope), $25/month USD starting 2026-09-01, alerts at Actual 50%, 80%, 100% and Forecasted 100%, emailing christopher.velez@sjsu.edu only. Month-to-date cost when created: $1.66.

### 2026-09-26 (Sat), afternoon PT, by Christopher Velez (sprint)

- Azure OpenAI rate limit, about 2:02 PM: on resource ISchoolAdvisingBot (resource group ISchool), the deployment `text-embedding-3-small` was raised from 1K to 50K tokens per minute (300 requests per minute). Billing is per token, so there is no added fixed cost.
- App settings, about 2:07 PM, on App Service `ischooladvisingchatbot-dx4ynl`: `EMBEDDING_PROVIDER=azure_openai` (new), `HYBRID_RETRIEVAL=true` (new), and `AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small` (already set). The app restarted, and at about 2:10 PM `/readyz` went stale (503), as the PR #15 receipt check intends, because the index was still hashing.
- Index rebuild, about 2:11 PM: `POST /api/load-docs?async=true` from the App Service SSH console. The job succeeded in about 35 seconds with 43 source files and 220 chunks. `/readyz` then reported ready with `embedding_provider` azure_openai, model text-embedding-3-small, dimension 1536, and commit 798a278.
- Live spot checks of the ISDA 150, 111, 120, and 140 prerequisite questions returned the catalog answers.
- Rollback: remove `EMBEDDING_PROVIDER` (or set it to hashing), then rebuild the index again through the deploy workflow's `workflow_dispatch` or load-docs.
