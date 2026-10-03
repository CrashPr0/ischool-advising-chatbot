"""Persistent paths for the advising index and the reviews database.

Azure App Service Linux keeps ``/home`` across restarts and redeploys. Oryx
runs the app from an extracted copy under ``/tmp``, so a repo-relative
``./data/chroma`` or ``evals/reviews.sqlite3`` disappears with that copy.
Local development keeps those repo-relative defaults.

The reviews migration copies a legacy database onto the durable path when the
new file is absent. It never moves or deletes the original. Operators should
still take the manual backup in ``AZURE_DEPLOYMENT.md`` before deploying this
behavior: a restart can remove the ``/tmp`` copy before this process starts.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - Azure and CI are Linux
    fcntl = None

logger = logging.getLogger(__name__)

LOCAL_VECTOR_STORE_PATH = "./data/chroma"
AZURE_VECTOR_STORE_PATH = "/home/data/chroma"
# Previous default, used only when this process is not App Service and the
# directory is already present. App Service always prefers AZURE_VECTOR_STORE_PATH,
# including when that directory does not exist yet.
LEGACY_VECTOR_STORE_PATH = "/home/site/wwwroot/data/chroma"

AZURE_EVAL_DB_PATH = "/home/data/evaluation/reviews.sqlite3"
LEGACY_WWWROOT_EVAL_DB_PATH = "/home/site/wwwroot/evals/reviews.sqlite3"
# Invented-link records sit beside the reviews database on the same
# persistent share. They are not review scores, so they stay in their own file.
AZURE_STRIPPED_LINK_LOG_PATH = "/home/data/evaluation/stripped_links.sqlite3"
INDEX_RECEIPT_FILENAME = "index_build.json"
DEPLOYMENT_VERSION_FILENAME = "deployment_version.txt"

_AZURE_ENV_VARS = ("WEBSITE_INSTANCE_ID", "WEBSITE_SITE_NAME")
_warned_missing_reviews: set[str] = set()


def running_on_azure_app_service(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether this process is an Azure App Service instance."""
    env = os.environ if environ is None else environ
    return any(str(env.get(name) or "").strip() for name in _AZURE_ENV_VARS)


def resolve_vector_store_path(
    configured_path: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Use an explicit path, then Azure's persistent index, then local data.

    An explicit ``VECTOR_STORE_PATH`` always wins, including a stale
    ``/tmp/chroma`` setting. On App Service the default is ``/home/data/chroma``
    even when that directory has not been created yet.
    """
    explicit = (configured_path or "").strip()
    if explicit:
        return explicit
    if running_on_azure_app_service(environ):
        return AZURE_VECTOR_STORE_PATH
    if os.path.isdir(LEGACY_VECTOR_STORE_PATH):
        return LEGACY_VECTOR_STORE_PATH
    return LOCAL_VECTOR_STORE_PATH


def default_local_eval_db_path(app_dir: str | None = None) -> str:
    """Repo-relative reviews database, beside the app when its directory is known."""
    if app_dir:
        return os.path.join(app_dir, "evals", "reviews.sqlite3")
    return os.path.join("evals", "reviews.sqlite3")


def default_local_stripped_link_log_path(app_dir: str | None = None) -> str:
    """Repo-relative invented-link log, beside the local reviews database."""
    if app_dir:
        return os.path.join(app_dir, "evals", "stripped_links.sqlite3")
    return os.path.join("evals", "stripped_links.sqlite3")


def resolve_stripped_link_log_path(
    configured_path: str | None = None,
    *,
    app_dir: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Use ``STRIPPED_LINK_LOG_PATH``, then Azure's persistent file, then local.

    On App Service the file is ``/home/data/evaluation/stripped_links.sqlite3``,
    the same directory that keeps the reviews database across restarts.
    """
    env = os.environ if environ is None else environ
    if configured_path is None:
        configured_path = env.get("STRIPPED_LINK_LOG_PATH", "")
    explicit = str(configured_path or "").strip()
    if explicit:
        return explicit
    if running_on_azure_app_service(env):
        return AZURE_STRIPPED_LINK_LOG_PATH
    return default_local_stripped_link_log_path(app_dir)


def resolve_eval_db_path(
    configured_path: str | None = None,
    *,
    app_dir: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Use an explicit ``EVAL_DB_PATH``, then Azure's persistent file, then local."""
    explicit = (configured_path or "").strip()
    if explicit:
        return explicit
    if running_on_azure_app_service(environ):
        return AZURE_EVAL_DB_PATH
    return default_local_eval_db_path(app_dir)


def legacy_reviews_database_candidates(app_dir: str | None = None) -> list[str]:
    """Paths where the pre-durable reviews database may still be sitting.

    The first entry is the historical code default (beside ``app.py``). On
    App Service that directory is often the ephemeral Oryx extract. The
    wwwroot path is the persistent site root from older runs.
    """
    candidates: list[str] = []
    if app_dir:
        candidates.append(default_local_eval_db_path(app_dir))
    candidates.append(LEGACY_WWWROOT_EVAL_DB_PATH)
    candidates.append(os.path.join(os.getcwd(), "evals", "reviews.sqlite3"))
    return candidates


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
        os.path.realpath(right)
    )


def _is_durable_eval_path(path: str) -> bool:
    return _same_path(path, AZURE_EVAL_DB_PATH)


def _is_sqlite_file(path: str) -> bool:
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
        with open(path, "rb") as handle:
            header = handle.read(16)
    except OSError:
        return False
    return header.startswith(b"SQLite format 3")


def _first_legacy_database(destination: str, candidates: list[str]) -> str | None:
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or not str(candidate).strip():
            continue
        source = os.path.abspath(candidate)
        key = os.path.normcase(os.path.realpath(source))
        if key in seen or _same_path(source, destination):
            continue
        seen.add(key)
        if _is_sqlite_file(source):
            return source
    return None


def _copy_sqlite_database(source: str, destination: str) -> None:
    """Write a consistent SQLite backup of ``source`` to ``destination``.

    The backup includes pages that still live in a WAL sidecar. ``source`` is
    only read. A crash removes the temporary file and leaves ``destination``
    absent so a later start can retry.
    """
    directory = os.path.dirname(destination)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = f"{destination}.migrating"
    try:
        if os.path.exists(temporary):
            os.remove(temporary)
        source_conn = sqlite3.connect(source)
        try:
            dest_conn = sqlite3.connect(temporary)
            try:
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()
        finally:
            source_conn.close()
        os.replace(temporary, destination)
    except Exception:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass
        raise
    if not os.path.exists(source):
        raise RuntimeError(f"Reviews database copy removed the source file at {source}")


def copy_reviews_database_if_needed(
    destination: str, candidates: list[str]
) -> str | None:
    """Copy the first legacy database onto ``destination`` when it is absent.

    Returns the source path when this call performed the copy. Returns None
    when ``destination`` already exists or no legacy SQLite file was found.
    The source file is never moved or deleted. An existing destination is
    never overwritten, so a later empty file cannot be repaired by this helper.
    """
    destination = os.path.abspath(destination)
    if os.path.exists(destination):
        return None
    if _first_legacy_database(destination, candidates) is None:
        return None

    directory = os.path.dirname(destination) or "."
    os.makedirs(directory, exist_ok=True)
    lock_path = f"{destination}.migrate.lock"
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            if os.path.exists(destination):
                return None
            source = _first_legacy_database(destination, candidates)
            if source is None:
                return None
            _copy_sqlite_database(source, destination)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    logger.info(
        "Copied reviews database from %s to %s; left the original file in place",
        source,
        destination,
    )
    return source


def _should_migrate_reviews(configured_path: str | None, destination: str) -> bool:
    """Migrate only onto the durable Azure default, not an arbitrary explicit path."""
    explicit = (configured_path or "").strip()
    if explicit:
        return _is_durable_eval_path(explicit)
    return _is_durable_eval_path(destination)


def prepare_eval_db_path(
    configured_path: str | None = None,
    *,
    app_dir: str | None = None,
    environ: Mapping[str, str] | None = None,
    candidates: list[str] | None = None,
) -> str:
    """Resolve the reviews database and copy a legacy file onto the durable path.

    A failed copy is raised instead of returning a path the caller would then
    create empty. Call this before opening the database.
    """
    destination = resolve_eval_db_path(
        configured_path, app_dir=app_dir, environ=environ
    )
    if not _should_migrate_reviews(configured_path, destination):
        return destination

    legacy = (
        list(candidates)
        if candidates is not None
        else legacy_reviews_database_candidates(app_dir)
    )
    try:
        copied_from = copy_reviews_database_if_needed(destination, legacy)
    except Exception:
        logger.exception(
            "Failed to copy a legacy reviews database to %s; "
            "not creating a new empty database",
            destination,
        )
        raise

    if copied_from is None and not os.path.exists(destination):
        if destination not in _warned_missing_reviews:
            _warned_missing_reviews.add(destination)
            logger.warning(
                "Reviews database %s does not exist and no legacy database was "
                "found among %s. The evaluation workspace will create an empty "
                "database on first use. Restore a backup first if this app "
                "already has reviews.",
                destination,
                legacy,
            )
    return destination


def read_deployment_version(version_file: str) -> str:
    """Return the commit baked into this release, or ``development`` locally."""
    try:
        with open(version_file, encoding="utf-8") as handle:
            version = handle.read().strip()
    except OSError:
        return "development"
    return version or "development"


def receipt_collection_id(vector_store_path: str) -> str | None:
    """Return the Chroma collection id recorded on a readable receipt."""
    receipt_path = os.path.join(vector_store_path or "", INDEX_RECEIPT_FILENAME)
    try:
        with open(receipt_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = str(payload.get("collection_id") or "").strip()
    return value or None


def _optional_positive_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def embedding_receipt_mismatch(
    payload: Mapping, expected: Mapping | None
) -> str | None:
    """Return why this receipt's embedder does not match the running process.

    A receipt written before embedding metadata existed is treated as the
    historical hashing index. Any other missing or different provider, model,
    or dimension is stale: querying those vectors with a new embedder returns
    empty or unrelated chunks.
    """
    if not expected:
        return None
    expected_provider = str(expected.get("embedding_provider") or "").strip()
    if not expected_provider:
        return None
    receipt_provider = str(payload.get("embedding_provider") or "").strip()
    if not receipt_provider:
        if expected_provider == "hashing":
            return None
        return (
            "index has no embedding provider recorded; "
            f"this process uses {expected_provider}"
        )
    if receipt_provider != expected_provider:
        return f"index embeddings are {receipt_provider}, not {expected_provider}"
    receipt_model = str(payload.get("embedding_model") or "").strip()
    expected_model = str(expected.get("embedding_model") or "").strip()
    if receipt_model and expected_model and receipt_model != expected_model:
        return f"index embedding model is {receipt_model}, not {expected_model}"
    receipt_dimension = _optional_positive_int(payload.get("embedding_dimension"))
    expected_dimension = _optional_positive_int(expected.get("embedding_dimension"))
    if (
        receipt_dimension
        and expected_dimension
        and receipt_dimension != expected_dimension
    ):
        return (
            f"index embedding dimension is {receipt_dimension}, "
            f"not {expected_dimension}"
        )
    return None


def _attach_embedding_report(
    report: dict, payload: Mapping, expected: Mapping | None
) -> None:
    for key in ("embedding_provider", "embedding_model", "embedding_dimension"):
        if key in payload:
            report[key] = payload.get(key)
    if expected and expected.get("embedding_provider"):
        report["expected_embedding_provider"] = expected.get("embedding_provider")


def assess_index_readiness(
    vector_store_path: str,
    *,
    version_file: str | None = None,
    expected_commit: str | None = None,
    expected_embedding: Mapping | None = None,
) -> dict:
    """Compare ``index_build.json`` with the deployed commit and embedder.

    The result is a readiness signal. It only reads the receipt file. It does
    not open Chroma or call an embedding API. ``/healthz`` must stay
    independent of this check because the deploy workflow waits for liveness
    before it rebuilds the index. A commit match with a different embedding
    provider, model, or dimension is ``stale``.
    """
    expected = (
        expected_commit
        if expected_commit is not None
        else read_deployment_version(version_file or DEPLOYMENT_VERSION_FILENAME)
    )
    store_path = vector_store_path or ""
    receipt_path = os.path.join(store_path, INDEX_RECEIPT_FILENAME)
    report = {
        "ready": False,
        "status": "missing",
        "expected_commit": expected,
        "receipt_commit": None,
        "detail": "index_build.json is missing",
        "vector_store_path": store_path,
        "receipt_path": receipt_path,
    }
    if not os.path.isfile(receipt_path):
        return report

    try:
        with open(receipt_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        report["status"] = "unreadable"
        report["detail"] = "index_build.json could not be read"
        return report
    if not isinstance(payload, dict):
        report["status"] = "unreadable"
        report["detail"] = "index_build.json could not be read"
        return report

    receipt_commit = str(payload.get("commit") or "").strip()
    if not receipt_commit or receipt_commit != expected:
        report["status"] = "mismatch"
        report["receipt_commit"] = receipt_commit or None
        described = receipt_commit or "an unknown commit"
        report["detail"] = f"index was built for {described}, not {expected}"
        return report

    embedding_mismatch = embedding_receipt_mismatch(payload, expected_embedding)
    if embedding_mismatch:
        report["status"] = "stale"
        report["receipt_commit"] = receipt_commit
        report["detail"] = embedding_mismatch
        _attach_embedding_report(report, payload, expected_embedding)
        return report

    report["ready"] = True
    report["status"] = "ready"
    report["receipt_commit"] = receipt_commit
    report["detail"] = "index receipt matches this release"
    _attach_embedding_report(report, payload, expected_embedding)
    return report
