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
