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

Portal changes that are not in git are recorded in
[`docs/ops-log.md`](docs/ops-log.md). Secret values are never written there.

| | |
| --- | --- |
| App name | `ischooladvisingchatbot-dx4ynl` |
| URL | `https://ischooladvisingchatbot-dx4ynl.azurewebsites.net` |
| Plan | Linux, Basic B1 |
| Build | Oryx (`SCM_DO_BUILD_DURING_DEPLOYMENT=true`) |
| Process | Procfile, Dockerfile, and the portal startup command: `gunicorn --config gunicorn.conf.py app:app` (`gthread`, 2 workers, 4 threads, 180s timeout, bind `$PORT`) |
| Health check | `/healthz` (liveness). `/readyz` is the index receipt, not the platform probe. |
| HTTPS Only | Enabled. HTTP 301-redirects to HTTPS. |

Oryx runs each release from an extracted copy under `/tmp`. That copy is
deleted on restart or redeploy. **`/home` is the persistent share.** Do not
store the Chroma index or the reviews database under `/tmp` or under the
release directory.

## Durable paths

With the variables unset, App Service uses:

| Data | Path |
| --- | --- |
| Chroma index and `index_build.json` | `/home/data/chroma` |
| Reviews SQLite database (also student answer reports) | `/home/data/evaluation/reviews.sqlite3` |

Local development, with the same variables unset, keeps using `./data/chroma`
and `evals/reviews.sqlite3` beside the app.

An explicit `VECTOR_STORE_PATH` or `EVAL_DB_PATH` overrides those defaults.
Leave `VECTOR_STORE_PATH` unset on this App Service so the index stays at
`/home/data/chroma`. `EVAL_DB_PATH` may be unset or set to
`/home/data/evaluation/reviews.sqlite3`; both select that file. Do not set
either variable to `/tmp/chroma`, `./data/chroma`, or a path under
`/home/site/wwwroot` (zip deploy replaces wwwroot). The workflow's rebuild
and verify steps call the app; they do not hard-code a directory.

`EVAL_RESULTS_PATH` stays the `evals/results/latest.json` shipped in the
release unless you set it.

Student answer reports use `answer_issue_reports` in the reviews database.
`ISSUE_REPORT_RATE_LIMIT` (10) and `ISSUE_REPORT_RATE_WINDOW_SECONDS` (600)
control report submissions. Reports, including any shared conversation, are
kept long-term by the iSchool to improve the assistant. iSchool leadership
wants long-term retention for ALA accreditation. Leave
`ISSUE_REPORT_RETENTION_DAYS` unset to keep that default. Set it to a positive
integer in App Service application settings, then restart the app, to delete
reports older than that many days. Unset, empty, zero, and invalid values
delete nothing. Invalid values log a warning. When a positive window is set,
reports older than the window, including any opted-in conversation transcripts,
are deleted when each worker initializes its report store and opportunistically
on report writes or lists, at most once per hour per worker. No scheduler is
needed; idle workers clean up on the next report operation. Concurrent workers
use an atomic SQLite deletion. Logs record the number of deleted rows without
report contents. The chat dialog says reports are kept long-term.

## Reviews database backup

The live reviews file is `/home/data/evaluation/reviews.sqlite3`. The copy
taken before PR #7 merged is recorded in [`docs/ops-log.md`](docs/ops-log.md)
(`/home/data/evaluation/reviews-backup-20260926.sqlite3`, 16 rows). The
snippet below is the earlier migration helper. It uses only the Python
standard library. It prints every `reviews.sqlite3` under `/tmp`,
`/home/site/wwwroot`, and `/home/data` — those roots are places a leftover
file might still be sitting, not paths to configure — then copies the newest
file that has at least one `evaluation_reviews` row. It refuses to overwrite
`/home/data/evaluation/reviews.sqlite3` if that file already exists, and it
never moves or deletes the source.

Because the durable file already exists, pasting this into Kudu SSH on
production stops with FAIL and copies nothing. `OK` means both copies have
the same row count as the source. Opening `/evaluation` against a missing
durable file creates an empty database, and a later copy will not overwrite
it.

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

The app's own copy runs only when the durable file is still missing, the first
time the evaluation store opens. The log line `Copied reviews database from ...`
means it found a legacy file. `no legacy database was found` means nothing was
copied. On the current app that file is already at
`/home/data/evaluation/reviews.sqlite3`. The download URL above belongs to this
helper, which writes it only when the durable file does not exist yet. The
backup that was actually taken is the 2026-09-26 file in
[`docs/ops-log.md`](docs/ops-log.md).

## Required App Service settings

Portal: the app → **Settings → Configuration**, and **Settings → Authentication**.

| Setting | What to confirm |
| --- | --- |
| Authentication (Easy Auth) | The app reads `X-MS-CLIENT-PRINCIPAL-NAME` only when the platform sets `WEBSITE_AUTH_ENABLED`. Otherwise that header is ignored. |
| HTTPS Only | Enabled. HTTP 301-redirects to HTTPS. Required for the Secure session cookie (`SESSION_COOKIE_SECURE=true`). |
| `SESSION_SECRET_KEY` | A long random value, stable across restarts. Not the `.env.example` placeholder. An unset key is a new random value every process start, which logs reviewers out. |
| `SESSION_COOKIE_SECURE` | `true` |
| `DOCS_LOAD_TOKEN` | A long random value. The same value must be the GitHub Actions secret `DOCS_LOAD_TOKEN`. Placeholder values disable index rebuilds. |
| `EVAL_ACCESS_TOKEN` | Read by the app for `/evaluation`. Not set in production today. With it unset, sign-in uses `EVAL_ALLOWED_REVIEWERS` or Easy Auth. A public deploy with none of those three returns 503. |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true`, or Oryx never installs `requirements.txt` and Gunicorn fails with `No module named 'flask_cors'`. |
| Azure OpenAI (or the chosen provider) | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, and `DEFAULT_LLM_PROVIDER`. `EMBEDDING_PROVIDER` stays `hashing` unless you intentionally pay for hosted embeddings. |

Leave `VECTOR_STORE_PATH` unset so `/home/data/chroma` applies. Do not set
`VECTOR_STORE_PATH=/tmp/chroma` or `/home/site/wwwroot/data/chroma`.
`EVAL_DB_PATH` may be unset or set to `/home/data/evaluation/reviews.sqlite3`.

[`docs/ops-log.md`](docs/ops-log.md) lists the app setting names present after
the latest out-of-band change. The app reads `EVAL_ACCESS_TOKEN` when it is
set. It is not set in production today. A non-local sign-in then requires
`EVAL_ALLOWED_REVIEWERS` (that name is set) or Easy Auth
(`WEBSITE_AUTH_ENABLED`, which the platform sets only when Authentication is
on). Localhost can open the workspace without either. These app settings are
platform switches the Python code does not read, so they are not in
`.env.example`: `PYTHONUNBUFFERED`, `SCM_DO_BUILD_DURING_DEPLOYMENT`,
`WEBSITE_HTTPLOGGING_RETENTION_DAYS`, and `WEBSITES_PORT`. Gunicorn binds to
`PORT` (default 8000 in `gunicorn.conf.py`). `WEBSITES_PORT` has to be that
same port. `SCM_DO_BUILD_DURING_DEPLOYMENT` must stay `true`.

GitHub repository secrets used by the workflow:

- `AZURE_WEBAPP_PUBLISH_PROFILE` for `ischooladvisingchatbot-dx4ynl`
- `DOCS_LOAD_TOKEN` matching the app setting

## Health and readiness

`GET /healthz` is liveness only. It returns `{"status":"ok","version":"<commit>"}`
and does not open Chroma or call a model. The deploy workflow waits on this
route **before** it rebuilds the index, and the App Service health check path
must stay `/healthz`.

`GET /readyz` reads `index_build.json` under the vector-store path and
compares its `commit` to `deployment_version.txt`. It also compares
`embedding_provider`, `embedding_model`, and `embedding_dimension` with the
embedder this process will use. It returns 200 when the commit matches and
the embedder matches (a receipt from before those fields existed still counts
as the hashing index). It returns 503 when the receipt is missing, unreadable,
names another commit (`mismatch`), or was built with a different embedder
(`stale`). It does not load Chroma or call Azure. A missing receipt is
expected from the moment the new zip is serving until the workflow's rebuild
finishes. Do not point the platform health check at `/readyz`, or the instance
will be marked unhealthy before the index exists and the rebuild may never run.

`POST /api/chat` calls the same `assess_index_readiness` helper before it
loads the chatbot. A missing, unreadable, mismatched, or embedding-stale
receipt returns HTTP 503 and a short student-facing message, and does not
call the model. Switching `EMBEDDING_PROVIDER` or
`AZURE_OPENAI_EMBEDDING_DEPLOYMENT` without a rebuild is stale on purpose:
the old vectors are a different shape, and search would otherwise return
empty or unrelated chunks.

`/healthz` does not perform this check. The deploy workflow waits for
`/healthz`, then rebuilds the index through `/api/load-docs`, not through
`/api/chat`. Chat answers again once that rebuild writes a receipt for the
running commit and the embedder that built it.

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

A failed index rebuild leaves the previous `index_build.json` and the previous
Chroma collection in place. Embeddings are written to a staging collection and
renamed over the live collection only after every batch succeeds. `/readyz`
then reports a commit mismatch (or stays stale if the embedder changed) until
a rebuild for the running commit succeeds. Watch the Actions log; a red job
can mean the new code is already live while chat is still refusing the old
receipt.

Hosted embedding calls during an index build are batched to about 800 tokens
and retried on HTTP 429 (8 attempts, backing off from 2 seconds up to 60).
A student question does not use that backoff; see Process and chat limits.
The advising corpus is about 19,500 tokens after chunking, so a 1000
token-per-minute deployment takes about 20 minutes and can outlast the
deploy script's 20-minute wait. Use at least 10000 tokens per minute on
`text-embedding-3-small` before setting `EMBEDDING_PROVIDER=azure_openai`.
100000 tokens per minute finishes the quota portion in well under a minute.

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
After each worker can serve `/healthz`, it builds the chatbot in a background
thread so the first student does not pay that cost and the startup probe is
not blocked.

Chat completions use the limits in `src/llm_providers.py`:

| Setting | Default | Range |
| --- | --- | --- |
| `LLM_TEMPERATURE` | 0.2 | 0–2 |
| `LLM_REQUEST_TIMEOUT` | 45 seconds | 1–120 |
| `LLM_MAX_RETRIES` | 0 | 0–5 |
| `LLM_MAX_TOKENS` | 512 | 64–4096 |

`LLM_MAX_RETRIES` defaults to 0. The OpenAI SDK otherwise sleeps for the
provider's Retry-After header, which has held student replies for about 30
seconds on HTTP 429. On 429, timeout, or 5xx the app immediately tries
`AZURE_OPENAI_FALLBACK_DEPLOYMENT` when that setting names another deployment
on the same endpoint and key. If the fallback rejects `temperature` (some
reasoning models), the app retries it once without temperature and sends the
token cap as `max_completion_tokens`. If no fallback is set or it also fails,
`/api/chat` returns HTTP 200 with source `busy` and asks the student to try
again in about a minute. An Azure content-filter block returns HTTP 200 with
source `filtered` and points the student at the iSchool advising page.

`CHAT_DEBUG_TIMINGS=true` adds a `timings` object to that JSON
(`retrieval_ms`, `embedding_ms`, `llm_ms`, `total_ms`, `llm_attempts`,
`rate_limit_count`, `deployment`). Leave it unset in production. Every
request still logs one `chat_request` line with those fields. On
`source=error` the line also includes the exception class and a short
message.

Query embeddings (the student question, not the index build) use at most two
attempts, an 8-second timeout, and about 2 seconds of backoff. If that call
fails and hybrid retrieval is on, the answer uses BM25 only. The index lock
is held for collection alignment, not during the embedding call. Chunk text
is cached by the live collection id, so a staging swap that publishes a new
id does not reuse the previous chunks.

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
- **Chat returns 503 while `/healthz` is green** — the index receipt is
  missing or still names the previous commit. That window is normal until
  "Rebuild production advising index" finishes. Do not point the health
  check at `/api/chat` or `/readyz`.
- **Answers fall back to "not enough information" while `/healthz` is green**
  — the index path is empty or ephemeral. Confirm `VECTOR_STORE_PATH` is unset
  (the app then uses `/home/data/chroma`) and is not `/tmp/chroma` or a path
  under `/home/site/wwwroot`, then rerun the workflow.
- **Evaluation workspace has no previous reviews** — the durable file was
  created empty. The backup recorded in `docs/ops-log.md` is
  `/home/data/evaluation/reviews-backup-20260926.sqlite3`. Restore a backup
  only after stopping traffic to `/evaluation`, and only if you are replacing
  an empty file you just confirmed is empty. Do not delete the backup.

## BS ISDA and MLIS corpus rollout

The advising corpus now includes both BS ISDA and MLIS. After merge, the Azure
index must be rebuilt to include the MLIS chunks and program metadata. The
existing deploy workflow runs `/api/load-docs` through
`scripts/verify_deployed_index.py` and verifies the new receipt. No additional
manual step is needed for a successful normal deployment. The corpus contents and
release SHA change; `/readyz` checks the rebuilt receipt against the deployed
release SHA and configured embedder, rather than computing a corpus hash. If the workflow rebuild fails, rerun the deploy
workflow and verify `/readyz` before using chat. Do not reuse the old receipt.

The 15 ingested official MLIS/iSchool pages are:

- https://ischool.sjsu.edu/master-library-and-information-science
- https://ischool.sjsu.edu/mlis-required-courses
- https://ischool.sjsu.edu/mlis-electives
- https://ischool.sjsu.edu/mlis-academics
- https://ischool.sjsu.edu/mlis-course-load
- https://ischool.sjsu.edu/mlis-fees-chart
- https://ischool.sjsu.edu/mlis-program-learning-outcomes
- https://ischool.sjsu.edu/mlis-accreditation
- https://ischool.sjsu.edu/mlis-career-pathways
- https://ischool.sjsu.edu/admissions
- https://ischool.sjsu.edu/application-deadlines
- https://ischool.sjsu.edu/graduate-application
- https://ischool.sjsu.edu/advising-appointments
- https://ischool.sjsu.edu/info-289-e-portfolio-faqs
- https://www.sjsu.edu/online/programs/library-and-information-science-masters-degree.php
