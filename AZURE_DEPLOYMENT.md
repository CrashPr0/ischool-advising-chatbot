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
| Process | `Procfile`: `gunicorn --config gunicorn.conf.py app:app` (`gthread`, 2 workers, 4 threads, 180s timeout) |

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
extract under `/tmp`. A restart deletes it. Paste this into the App Service
SSH or Kudu Bash console **before** merging. It uses only the Python standard
library. It prints every `reviews.sqlite3` under `/tmp`, `/home/site/wwwroot`,
and `/home/data`, then copies the newest file that has at least one
`evaluation_reviews` row. It refuses to overwrite
`/home/data/evaluation/reviews.sqlite3` if that file already exists, and it
never moves or deletes the source.

If the new code starts first and someone opens `/evaluation`, the app creates
an empty database at the durable path, and a later copy will not overwrite it.
Run the snippet first. `OK` means both copies have the same row count as the
source. `FAIL` with "no reviews.sqlite3" means a restart may already have
wiped the old file; restore from an earlier download if you have one.

```bash
python3 - <<'EOF'
import os
import sqlite3
import sys
import time
from pathlib import Path

SEARCH_ROOTS = (
    Path("/tmp"),
    Path("/home/site/wwwroot"),
    Path("/home/data"),
)
DESTINATION = Path("/home/data/evaluation/reviews.sqlite3")
OFFLINE_COPY = Path("/home/data/evaluation/reviews-backup-before-durable-path.sqlite3")
_TABLE = "evaluation_reviews"


def _activity_mtime(path: Path) -> float:
    """Newest mtime of the database and its WAL sidecars."""
    latest = path.stat().st_mtime
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        try:
            if sidecar.is_file():
                latest = max(latest, sidecar.stat().st_mtime)
        except OSError:
            continue
    return latest


def _row_count(path: Path):
    """Return (count, error). count is None when the table cannot be read."""
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error as exc:
        return None, str(exc)
    try:
        row = connection.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()
    except sqlite3.Error as exc:
        return None, str(exc)
    finally:
        connection.close()
    return int(row[0]), None


def _find_databases(search_roots):
    found = []
    seen = set()
    for root in search_roots:
        root = Path(root)
        if not root.exists():
            print(f"skip missing root: {root}")
            continue

        def onerror(err, root=root):
            print(f"could not search {getattr(err, 'filename', root)}: {err}")

        for dirpath, _dirnames, filenames in os.walk(root, onerror=onerror):
            if "reviews.sqlite3" not in filenames:
                continue
            path = Path(dirpath) / "reviews.sqlite3"
            try:
                if not path.is_file():
                    continue
                key = path.resolve()
            except OSError as exc:
                print(f"could not read {path}: {exc}")
                continue
            if key in seen:
                continue
            seen.add(key)
            found.append(path)
    return found


def _describe(path: Path):
    try:
        size = path.stat().st_size
        mtime = _activity_mtime(path)
    except OSError as exc:
        print(f"  {path}  unreadable ({exc})")
        return None
    rows, error = _row_count(path)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
    if rows is None:
        print(f"  {path}  size={size}  mtime={stamp}  rows=unavailable ({error})")
    else:
        print(f"  {path}  size={size}  mtime={stamp}  rows={rows}")
    return {"path": path, "size": size, "mtime": mtime, "rows": rows}


def _backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    try:
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def _download_url() -> str:
    app_name = os.environ.get("WEBSITE_SITE_NAME", "").strip() or "<app>"
    return (
        f"https://{app_name}.scm.azurewebsites.net/api/vfs/data/evaluation/"
        "reviews-backup-before-durable-path.sqlite3"
    )


def backup_reviews(search_roots, destination, offline_copy) -> int:
    """Copy the newest non-empty reviews database. Never move or delete it."""
    destination = Path(destination)
    offline_copy = Path(offline_copy)
    print("reviews.sqlite3 candidates:")
    described = []
    for path in _find_databases(search_roots):
        item = _describe(path)
        if item is not None:
            described.append(item)
    if not described:
        print("(none)")

    if destination.exists():
        rows, error = _row_count(destination)
        detail = f"rows={rows}" if error is None else f"rows=unavailable ({error})"
        print(
            f"FAIL: {destination} already exists ({detail}). "
            "Refusing to overwrite it. Nothing was copied."
        )
        return 1

    selectable = [
        item
        for item in described
        if item["size"] > 0 and isinstance(item["rows"], int) and item["rows"] > 0
    ]
    if not selectable:
        if described:
            print(
                "FAIL: found reviews.sqlite3 files, but none contain "
                f"{_TABLE} rows. Nothing was copied."
            )
        else:
            roots = ", ".join(str(Path(root)) for root in search_roots)
            print(
                f"FAIL: no reviews.sqlite3 file was found under {roots}. "
                "The old default may already have been wiped by a restart. "
                "Nothing was copied."
            )
        return 1

    chosen = max(
        selectable,
        key=lambda item: (item["mtime"], item["rows"], str(item["path"])),
    )
    source = chosen["path"]
    print(f"Selected: {source} (rows={chosen['rows']})")

    created = []
    try:
        _backup(source, destination)
        created.append(destination)
        _backup(source, offline_copy)
        created.append(offline_copy)
        source_rows, source_error = _row_count(source)
        dest_rows, dest_error = _row_count(destination)
        offline_rows, offline_error = _row_count(offline_copy)
        if (
            not source.exists()
            or source_error
            or dest_error
            or offline_error
            or source_rows != dest_rows
            or source_rows != offline_rows
            or source_rows != chosen["rows"]
        ):
            raise RuntimeError(
                "row counts do not match "
                f"(source={source_rows}, destination={dest_rows}, "
                f"offline={offline_rows})"
            )
    except Exception as exc:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        print(f"FAIL: {exc}. Source was left in place. Incomplete copies were removed.")
        return 1

    print(
        f"OK: copied {chosen['rows']} rows from {source} to {destination} "
        f"and {offline_copy}. Source was left in place."
    )
    print(f"Download: {_download_url()}")
    return 0


if __name__ == "__main__":
    sys.exit(backup_reviews(SEARCH_ROOTS, DESTINATION, OFFLINE_COPY))
EOF
```

Download the offline copy while signed in to Kudu (this URL is not public):

https://ischooladvisingchatbot-dx4ynl.scm.azurewebsites.net/api/vfs/data/evaluation/reviews-backup-before-durable-path.sqlite3

The pattern is `https://<app>.scm.azurewebsites.net/api/vfs/data/evaluation/reviews-backup-before-durable-path.sqlite3`.
On this Linux app the Kudu VFS root is `/home`, so that path is
`/home/data/evaluation/reviews-backup-before-durable-path.sqlite3`. The snippet
also prints the URL when `WEBSITE_SITE_NAME` is set.

The app's own copy, if the durable file is still missing, runs the first time
the evaluation store opens. The log line `Copied reviews database from ...`
means it found a legacy file. `no legacy database was found` means nothing was
copied. Restore the Kudu download before that first `/evaluation` request.

## Required App Service settings

Portal: the app → **Settings → Configuration**, and **Settings → Authentication**.

| Setting | What to confirm |
| --- | --- |
| Authentication (Easy Auth) | Enabled. The app reads `X-MS-CLIENT-PRINCIPAL-NAME` only when `WEBSITE_AUTH_ENABLED` is true; otherwise that header is ignored. Keep `EVAL_ACCESS_TOKEN` set until Easy Auth is confirmed. |
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

## Process and chat limits

`gunicorn.conf.py` is what the Procfile and the container image load. Leave
the variables unset to keep these defaults. A value outside the range falls
back inside it.

| Setting | Default | Range |
| --- | --- | --- |
| `GUNICORN_WORKERS` | 2 | 1–4 |
| `GUNICORN_THREADS` | 4 | 1–8 |
| `GUNICORN_TIMEOUT` | 180 seconds | 30–230 |
| `GUNICORN_GRACEFUL_TIMEOUT` | 210 seconds | at least the worker timeout, and at most 300 |

The worker class is `gthread`. It is not an environment variable. Two workers
keep a cold Chroma load from freezing `/healthz`. The 180-second timeout sits
above the chat client's retry budget and under App Service's front-end cutoff.

Chat completions use the limits in `src/llm_providers.py`:

| Setting | Default | Range |
| --- | --- | --- |
| `LLM_TEMPERATURE` | 0.2 | 0–2 |
| `LLM_REQUEST_TIMEOUT` | 45 seconds | 1–120 |
| `LLM_MAX_RETRIES` | 2 | 0–5 |
| `LLM_MAX_TOKENS` | 1024 | 64–4096 |

`/healthz` stays the platform health check and the deploy probe. `/api/status`
loads the RAG stack and is not a health check. `/readyz` is the index receipt.

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
