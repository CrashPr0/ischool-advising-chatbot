# Azure App Service runbook

Production is one Linux App Service app on the **Basic B1** plan. B1 is a paid
SKU, not a free tier. Student credits can pay the bill; they do not make B1
free. Check the current price in the Azure pricing calculator. Basic cannot
use deployment slots.

Pushes to `main` that change the app run
[`.github/workflows/deploy-azure.yml`](.github/workflows/deploy-azure.yml).
There is no `deploy.yml`. The workflow runs tests, builds a zip of that
commit, deploys it, waits until `/healthz` reports the new commit, then
rebuilds the advising index and checks the receipt.

| | |
| --- | --- |
| App name | `ischooladvisingchatbot-dx4ynl` |
| URL | `https://ischooladvisingchatbot-dx4ynl.azurewebsites.net` |
| Plan | Linux, Basic B1 |
| Build | Oryx (`SCM_DO_BUILD_DURING_DEPLOYMENT=true`) |
| Process | `Procfile`: `gunicorn --bind 0.0.0.0:${PORT:-8000} app:app` |

Oryx runs each release from an extracted copy under `/tmp`. That copy is
deleted on restart or redeploy. **`/home` is the persistent share.** Do not
store the Chroma index or the reviews database under `/tmp` or under the
release directory.

## Durable paths

With the variables unset, App Service uses:

| Data | Path |
| --- | --- |
| Chroma index and `index_build.json` | `/home/data/chroma` |
| Reviews SQLite database | `/home/data/evaluation/reviews.sqlite3` |

Local development, with the same variables unset, keeps using `./data/chroma`
and `evals/reviews.sqlite3` beside the app.

An explicit `VECTOR_STORE_PATH` or `EVAL_DB_PATH` overrides the default. If
App Service still has `VECTOR_STORE_PATH=/tmp/chroma` (or `./data/chroma`)
from an older guide, remove it or set `/home/data/chroma`. The next deploy
rebuilds the index in the path the app is actually using. The workflow's
rebuild and verify steps call the app; they do not hard-code a directory.

`EVAL_RESULTS_PATH` stays the `evals/results/latest.json` shipped in the
release unless you set it.

## Back up the reviews database before the path change

The live reviews file is still at the old default: `evals/reviews.sqlite3`
next to `app.py`. On the current host that is usually inside the Oryx
extract under `/tmp`. A restart deletes it. The new process copies a legacy
file onto `/home/data/evaluation/reviews.sqlite3` only when that new file
does not exist yet **and** the old file is still on disk (beside the app, or
at `/home/site/wwwroot/evals/reviews.sqlite3`). The copy never moves or
deletes the original.

Do this on the running app **before** merging the durable-path change. If the
new code starts first and someone opens `/evaluation`, it creates an empty
database at the new path, and a later copy will not overwrite that file.

SSH or the Kudu Bash console (`https://ischooladvisingchatbot-dx4ynl.scm.azurewebsites.net`):

```bash
find /tmp /home/site/wwwroot /home/data -name 'reviews.sqlite3' -print 2>/dev/null
mkdir -p /home/data/evaluation
```

Copy with SQLite's backup so a WAL sidecar is included. Do not `mv` or `rm`
the original. If the `sqlite3` CLI is not installed, use the app's Python:

```bash
python - <<'PY'
import sqlite3
from pathlib import Path

source = Path("/path/from/find/reviews.sqlite3")  # replace
destination = Path("/home/data/evaluation/reviews.sqlite3")
destination.parent.mkdir(parents=True, exist_ok=True)
offline = destination.with_name("reviews-backup-before-durable-path.sqlite3")
source_conn = sqlite3.connect(source)
try:
    for target in (destination, offline):
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
finally:
    source_conn.close()
print("copied", destination)
print("offline copy", offline)
PY
```

Confirm the copy has rows, then download the offline copy from Kudu:

```bash
sqlite3 /home/data/evaluation/reviews.sqlite3 \
  "SELECT COUNT(*) FROM evaluation_reviews;"
```

The copy runs the first time the evaluation store opens (a visit to
`/evaluation` or an evaluation API call), not during process import. The log
stream then contains `Copied reviews database from ...` if a legacy file was
still on disk. `no legacy database was found` means the durable file was
missing and nothing was copied. Restore the backup before that first
evaluation request; opening the workspace creates an empty database, and a
later copy will not overwrite it.

## Required App Service settings

Portal: the app → **Settings → Configuration**, and **Settings → Authentication**.

| Setting | What to confirm |
| --- | --- |
| Authentication (Easy Auth) | Enabled, so the platform supplies `X-MS-CLIENT-PRINCIPAL-NAME` and strips a client-supplied value. `WEBSITE_AUTH_ENABLED` should be true. Keep `EVAL_ACCESS_TOKEN` set until this is confirmed. |
| HTTPS Only | On (Configuration → General settings, or TLS/SSL settings). |
| `SESSION_SECRET_KEY` | A long random value, stable across restarts. Not the `.env.example` placeholder. An unset key is a new random value every process start, which logs reviewers out. |
| `SESSION_COOKIE_SECURE` | `true` |
| `DOCS_LOAD_TOKEN` | A long random value. The same value must be the GitHub Actions secret `DOCS_LOAD_TOKEN`. Placeholder values disable index rebuilds. |
| `EVAL_ACCESS_TOKEN` | A long random shared secret for `/evaluation`. |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true`, or Oryx never installs `requirements.txt` and Gunicorn fails with `No module named 'flask_cors'`. |
| Azure OpenAI (or the chosen provider) | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, and `DEFAULT_LLM_PROVIDER`. `EMBEDDING_PROVIDER` stays `hashing` unless you intentionally pay for hosted embeddings. |

Leave `VECTOR_STORE_PATH` and `EVAL_DB_PATH` unset so the `/home/data` defaults
apply. Do not set `VECTOR_STORE_PATH=/tmp/chroma`.

GitHub repository secrets used by the workflow:

- `AZURE_WEBAPP_PUBLISH_PROFILE` for `ischooladvisingchatbot-dx4ynl`
- `DOCS_LOAD_TOKEN` matching the app setting

## Health and readiness

`GET /healthz` is liveness only. It returns `{"status":"ok","version":"<commit>"}`
and does not open Chroma or call a model. The deploy workflow waits on this
route **before** it rebuilds the index, and the App Service health check path
must stay `/healthz`.

`GET /readyz` reads `index_build.json` under the vector-store path and
compares its `commit` to `deployment_version.txt`. It returns 200 when they
match and 503 when the receipt is missing, unreadable, or names another
commit. It does not load Chroma. A missing receipt is expected from the moment
the new zip is serving until the workflow's rebuild finishes. Do not point the
platform health check at `/readyz`, or the instance will be marked unhealthy
before the index exists and the rebuild may never run.

Chat does not yet refuse answers when `/readyz` is not ready. The signal is
the route above. A later change can call `assess_index_readiness` in
`src/durable_storage.py` from the chat path before retrieval.

## Rollback

Basic B1 has no deployment slots, so there is no swap back to the previous
package. Rollback is a new commit on `main`:

```bash
git revert <bad-commit>
git push origin main
```

The workflow deploys that commit, waits for `/healthz`, and rebuilds the
index for the reverted SHA. **Run workflow** (`workflow_dispatch`) redeploys
whatever `main` is now; it does not restore an older package by itself.

A failed index rebuild leaves the previous `index_build.json` in place on
purpose. `/readyz` then reports a commit mismatch until a rebuild for the
running commit succeeds. The site can still be serving the new code against
the previous corpus. Watch the Actions log; a red job can mean the new code
is already live.

## Staging

Slots require Standard or higher. On Basic, staging is a **second App Service
app** (its own URL, its own `/home`, its own `DOCS_LOAD_TOKEN` and reviews
file). Do not point a staging app at the production `/home` data.

## Budget alert and Azure OpenAI quota

The app cannot see the Azure bill. Set both of these in the portal:

1. **Cost Management → Budgets → Add.** Scope the resource group or
   subscription. Set the amount under the remaining student credits (confirm
   the current Azure for Students grant; B1 and Azure OpenAI both spend it).
   Alert at 50% and 80% to the people who watch the app.
2. **Azure OpenAI → the deployment → quota.** Keep a token or
   tokens-per-minute cap on the student deployment. The advising benchmark
   already waits 60 seconds between questions because that quota is small.
   Raising the app's rate limit does not raise the Azure quota.

## Application Insights and a failure alert

This repository does not install the Application Insights SDK. Use platform
metrics, which App Service records without that package:

1. Open the app → **Application Insights** → turn it on and create (or pick)
   a workspace-based resource in the same region.
2. **Monitoring → Alerts → Create → Alert rule.**
3. Signal: App Service **Http Server Errors** (HTTP 5xx), or **Failed
   requests** if the Application Insights metric is already flowing.
4. Condition: greater than 5 in a 5-minute window. Action: email the team.
5. Leave the health-check path on `/healthz`. A separate alert on
   "health check failing" is useful only if that path stays `/healthz`.

The GitHub Actions log is still the place to see a failed index rebuild. The
alert above is for 5xx responses after deploy.

## Settings landing in other changes

Do not guess these names. Confirm them in the pull requests that change the
process model and the chat client, then set the values those PRs document:

- Gunicorn worker class, thread count, request timeout, and graceful timeout.
  Today the `Procfile` is `gunicorn --bind 0.0.0.0:${PORT:-8000} app:app`
  (one sync worker, Gunicorn's 30-second default timeout).
- Chat LLM request timeout, retry count, and `max_tokens`.

`/healthz` stays the liveness path after those changes. `/api/status` loads
the RAG stack and is not a health check.

## What a deploy does

1. `git archive` of the commit, plus a `deployment_version.txt` containing
   `GITHUB_SHA`.
2. Zip deploy to `ischooladvisingchatbot-dx4ynl`.
3. Poll `GET /healthz` until `version` equals that SHA (up to about 10 minutes).
4. `python scripts/verify_deployed_index.py` posts `POST /api/load-docs?async=true`
   with `DOCS_LOAD_TOKEN`, then polls until `index_build.json` names that SHA
   and the collection is non-empty.
5. The same script runs again with `--verify-only`.

Documentation-only pushes are ignored by the workflow (`**/*.md`). Use
**Run workflow** when a docs-only commit must be published anyway. Deploys
are serialized; a second push waits.

## Logs

```bash
az webapp log tail \
  --name ischooladvisingchatbot-dx4ynl \
  --resource-group <resource-group>
```

Or the portal log stream. Look for the index receipt line
(`Document index rebuilt for <sha>`) and, on the first start after the path
change, the reviews copy line.

## Troubleshooting

- **`No module named 'flask_cors'`** — `SCM_DO_BUILD_DURING_DEPLOYMENT` is not
  `true`, so Oryx did not install `requirements.txt`.
- **`/healthz` never shows the new SHA** — the zip did not become the running
  release. Check the deployment log. Do not switch the probe to `/readyz`.
- **Actions is red after `/healthz` succeeded** — the index rebuild failed or
  the receipt still names an older commit. `/readyz` reports the reason
  (`missing` or `mismatch`) without loading Chroma.
- **Answers fall back to "not enough information" while `/healthz` is green**
  — the index path is empty or ephemeral. Confirm `VECTOR_STORE_PATH` is unset
  or `/home/data/chroma`, then rerun the workflow.
- **Evaluation workspace has no previous reviews** — the durable file was
  created empty. Restore `/home/data/evaluation/reviews-backup-before-durable-path.sqlite3`
  only after stopping traffic to `/evaluation`, and only if you are replacing
  an empty file you just confirmed is empty. Do not delete the backup.
