"""The pre-merge reviews backup snippet copies and never deletes."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backup_reviews_db import backup_reviews  # noqa: E402


def _snippet_from_runbook() -> str:
    text = (ROOT / "AZURE_DEPLOYMENT.md").read_text(encoding="utf-8")
    marker = "python3 - <<'EOF'\n"
    start = text.index(marker) + len(marker)
    end = text.index("\nEOF", start)
    return text[start:end] + "\n"


def _create_db(
    path: Path, comments: list[str], *, wal: bool = False
) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE evaluation_reviews (id INTEGER PRIMARY KEY, comment TEXT)"
    )
    connection.executemany(
        "INSERT INTO evaluation_reviews (comment) VALUES (?)",
        [(comment,) for comment in comments],
    )
    connection.commit()
    return connection


def _comments(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [
            row[0]
            for row in connection.execute(
                "SELECT comment FROM evaluation_reviews ORDER BY id"
            )
        ]
    finally:
        connection.close()


def _run(tmp_path: Path, roots: list[Path]):
    destination = tmp_path / "home" / "data" / "evaluation" / "reviews.sqlite3"
    offline = (
        tmp_path
        / "home"
        / "data"
        / "evaluation"
        / "reviews-backup-before-durable-path.sqlite3"
    )
    code = backup_reviews(roots, destination, offline)
    return code, destination, offline


def test_runbook_snippet_matches_the_script():
    assert _snippet_from_runbook() == (
        ROOT / "scripts" / "backup_reviews_db.py"
    ).read_text(encoding="utf-8")


def test_wal_database_is_copied_and_the_source_stays(tmp_path, capsys):
    source = tmp_path / "tmp" / "extract" / "evals" / "reviews.sqlite3"
    writer = _create_db(source, ["kept"], wal=True)
    inode = source.stat().st_ino

    code, destination, offline = _run(
        tmp_path, [tmp_path / "tmp", tmp_path / "wwwroot", tmp_path / "home" / "data"]
    )

    assert code == 0
    assert "OK:" in capsys.readouterr().out
    assert source.exists()
    assert source.stat().st_ino == inode
    assert _comments(source) == ["kept"]
    assert _comments(destination) == ["kept"]
    assert _comments(offline) == ["kept"]
    writer.close()


def test_no_database_is_a_clear_failure(tmp_path, capsys):
    roots = [tmp_path / "tmp", tmp_path / "wwwroot", tmp_path / "home" / "data"]
    for root in roots:
        root.mkdir(parents=True)

    code, destination, offline = _run(tmp_path, roots)
    output = capsys.readouterr().out

    assert code == 1
    assert "no reviews.sqlite3" in output
    assert "wiped" in output
    assert "FAIL:" in output
    assert not destination.exists()
    assert not offline.exists()


def test_existing_destination_is_not_overwritten(tmp_path, capsys):
    source = tmp_path / "tmp" / "evals" / "reviews.sqlite3"
    destination = tmp_path / "home" / "data" / "evaluation" / "reviews.sqlite3"
    _create_db(source, ["legacy"]).close()
    _create_db(destination, ["already durable"]).close()

    code, destination, offline = _run(
        tmp_path, [tmp_path / "tmp", tmp_path / "wwwroot", tmp_path / "home" / "data"]
    )

    assert code == 1
    assert "already exists" in capsys.readouterr().out
    assert _comments(source) == ["legacy"]
    assert _comments(destination) == ["already durable"]
    assert not offline.exists()


def test_newest_non_empty_database_wins(tmp_path, capsys):
    older = tmp_path / "wwwroot" / "evals" / "reviews.sqlite3"
    newer = tmp_path / "tmp" / "release" / "evals" / "reviews.sqlite3"
    empty = tmp_path / "tmp" / "empty" / "reviews.sqlite3"
    _create_db(older, ["old-a", "old-b"]).close()
    _create_db(newer, ["new"]).close()
    _create_db(empty, []).close()
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))
    os.utime(empty, (1_900_000_000, 1_900_000_000))

    code, destination, offline = _run(
        tmp_path,
        [tmp_path / "tmp", tmp_path / "wwwroot", tmp_path / "data-root"],
    )
    output = capsys.readouterr().out

    assert code == 0
    assert f"Selected: {newer}" in output
    assert "rows=2" in output
    assert "rows=0" in output
    assert _comments(destination) == ["new"]
    assert _comments(offline) == ["new"]
    assert _comments(older) == ["old-a", "old-b"]
    assert _comments(newer) == ["new"]


def test_heredoc_script_exits_nonzero_when_nothing_is_found(tmp_path):
    snippet = _snippet_from_runbook()
    snippet = snippet.replace('Path("/tmp")', f"Path({str(tmp_path / 'tmp')!r})")
    snippet = snippet.replace(
        'Path("/home/site/wwwroot")', f"Path({str(tmp_path / 'wwwroot')!r})"
    )
    snippet = snippet.replace(
        'Path("/home/data")', f"Path({str(tmp_path / 'homedata')!r})"
    )
    snippet = snippet.replace(
        'DESTINATION = Path("/home/data/evaluation/reviews.sqlite3")',
        f"""DESTINATION = Path({str(
            tmp_path / 'homedata' / 'evaluation' / 'reviews.sqlite3'
        )!r})""",
    )
    snippet = snippet.replace(
        (
            'OFFLINE_COPY = Path("/home/data/evaluation/reviews-backup-before-dur'
            'able-path.sqlite3")'
        ),
        f"""OFFLINE_COPY = Path({str(
            tmp_path / 'homedata' / 'evaluation' /
            'reviews-backup-before-durable-path.sqlite3'
        )!r})""",
    )
    (tmp_path / "tmp").mkdir()
    (tmp_path / "wwwroot").mkdir()
    (tmp_path / "homedata").mkdir()
    script = tmp_path / "backup.py"
    script.write_text(snippet, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "no reviews.sqlite3" in completed.stdout
    assert "FAIL:" in completed.stdout
