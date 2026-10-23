"""Catalog transport and workflow outputs, without network or repository writes."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from scripts import check_sjsu_catalog as catalog
from src.catalog_update import CatalogCheckResult, FetchResult, pull_request_body


@pytest.fixture
def client(monkeypatch):
    session = requests.Session()
    session.get = Mock(side_effect=AssertionError("Unexpected HTTP request"))
    monkeypatch.setattr(catalog.requests, "Session", lambda: session)
    return catalog.CatalogClient(delay_seconds=0, timeout_seconds=7)


def response(status, body="", headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = body.encode("utf-8")
    result.encoding = "utf-8"
    result.headers.update(headers or {})
    return result


@pytest.mark.parametrize(
    "url",
    [
        "http://catalog.sjsu.edu/",
        "https://example.org/",
        "https://catalog.sjsu.edu.example.org/",
        "https://catalog.sjsu.edu@example.org/",
        "/preview_course.php?catoid=24",
    ],
)
def test_client_rejects_outside_urls_before_requesting(client, url):
    assert client(url) == FetchResult(
        status=0, error="URL is outside catalog.sjsu.edu", blocked=True
    )
    client.session.get.assert_not_called()


@pytest.mark.parametrize("status, body", [(200, "Catalog café"), (404, "Missing"), (200, "")])
def test_client_preserves_response_and_disables_redirects(client, status, body):
    client.session.get.side_effect = None
    client.session.get.return_value = response(status, body)
    url = "https://catalog.sjsu.edu/index.php?catoid=24"

    assert client(url) == FetchResult(status=status, body=body)
    client.session.get.assert_called_once_with(url, timeout=7, allow_redirects=False)
    assert client.session.headers["User-Agent"] == catalog.USER_AGENT


@pytest.mark.parametrize(
    "status, headers",
    [(202, {}), (200, {"x-amzn-waf-action": "challenge"})],
)
def test_client_marks_challenges_unusable(client, caplog, status, headers):
    client.session.get.side_effect = None
    client.session.get.return_value = response(status, "Challenge page", headers)

    assert client("https://catalog.sjsu.edu/") == FetchResult(
        status=status, body="Challenge page", blocked=True
    )
    assert "challenged" in caplog.text


def test_client_blocks_redirect_without_using_its_body(client, caplog):
    client.session.get.side_effect = None
    client.session.get.return_value = response(
        302, "Misleading catalog", {"Location": "https://example.org/"}
    )

    assert client("https://catalog.sjsu.edu/") == FetchResult(
        status=302, error="redirect", blocked=True
    )
    assert "redirected" in caplog.text
    assert client.session.get.call_count == 1


@pytest.mark.parametrize("error_type", [requests.Timeout, requests.ConnectionError])
def test_client_returns_fetch_failure_on_transport_error(client, caplog, error_type):
    client.session.get.side_effect = error_type("Connection unavailable")

    assert client("https://catalog.sjsu.edu/") == FetchResult(
        status=0, error="Connection unavailable"
    )
    assert "Catalog fetch failed" in caplog.text


def test_client_waits_only_for_remaining_delay_between_requests(client, monkeypatch):
    client.delay_seconds = 1
    ticks = iter([100.0, 100.25, 101.0, 103.0, 103.0])
    monkeypatch.setattr(catalog.time, "monotonic", lambda: next(ticks))
    sleep = Mock()
    monkeypatch.setattr(catalog.time, "sleep", sleep)
    client.session.get.side_effect = None
    client.session.get.return_value = response(200, "Catalog")

    results = [client("https://catalog.sjsu.edu/") for _ in range(3)]

    assert results == [FetchResult(status=200, body="Catalog")] * 3
    sleep.assert_called_once_with(0.75)
    assert client.session.get.call_count == 3


def test_negative_delay_disables_throttling(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr(catalog.time, "sleep", sleep)
    session = Mock()
    session.get.return_value = response(200, "Catalog")
    monkeypatch.setattr(catalog.requests, "Session", lambda: session)
    client = catalog.CatalogClient(delay_seconds=-5)

    assert client("https://catalog.sjsu.edu/") == FetchResult(200, "Catalog")
    assert client("https://catalog.sjsu.edu/") == FetchResult(200, "Catalog")
    sleep.assert_not_called()


def test_updated_result_writes_json_pr_body_and_appends_workflow_outputs(tmp_path, monkeypatch):
    result = CatalogCheckResult(
        status="updated",
        message="Refreshed catalog snapshots",
        catoid="24",
        year="2027-2028",
        previous_catoid="23",
        previous_year="2026-2027",
        files=["data/catalog_identity.json"],
    )
    report = tmp_path / "result.json"
    pr_body = tmp_path / "pr.md"
    workflow = tmp_path / "github-output"
    workflow.write_text("previous=value\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", f"  {workflow}  ")

    catalog.write_result(result, str(report), str(pr_body))

    assert json.loads(report.read_text(encoding="utf-8")) == result.as_dict()
    assert pr_body.read_text(encoding="utf-8") == pull_request_body(result)
    assert workflow.read_text(encoding="utf-8") == (
        "previous=value\nchanged=true\nstatus=updated\nyear=2027-2028\ncatoid=24\n"
    )


@pytest.mark.parametrize("status", ["unchanged", "blocked", "error"])
def test_non_updates_preserve_pr_body_and_signal_no_change(tmp_path, monkeypatch, status):
    result = CatalogCheckResult(status=status, message="No refresh", catoid="23", year="2026-2027")
    pr_body = tmp_path / "pr.md"
    pr_body.write_text("Existing review text", encoding="utf-8")
    workflow = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(workflow))

    catalog.write_result(result, "", str(pr_body))

    assert pr_body.read_text(encoding="utf-8") == "Existing review text"
    assert workflow.read_text(encoding="utf-8") == (
        f"changed=false\nstatus={status}\nyear=2026-2027\ncatoid=23\n"
    )


@pytest.mark.parametrize("status", ["updated", "unchanged", "blocked"])
def test_main_passes_cli_options_and_reports_result(tmp_path, monkeypatch, capsys, status):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    repo = tmp_path / "repository"
    report = tmp_path / "result.json"
    pr_body = tmp_path / "pr.md"
    result = CatalogCheckResult(status=status, message="Catalog check complete")
    check = Mock(return_value=result)
    monkeypatch.setattr(catalog, "check_for_newer_catalog", check)

    exit_code = catalog.main([
        "--repo-root", str(repo), "--delay-s", "2.5",
        "--result", str(report), "--pr-body", str(pr_body),
    ])

    assert exit_code == 0
    check.assert_called_once()
    root, client = check.call_args.args
    assert root == repo
    assert isinstance(client, catalog.CatalogClient)
    assert client.delay_seconds == 2.5
    assert json.loads(report.read_text(encoding="utf-8")) == result.as_dict()
    assert pr_body.exists() == (status == "updated")
    assert capsys.readouterr().out == "Catalog check complete\n"


def test_main_defaults_skip_optional_output_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    check = Mock(return_value=CatalogCheckResult(status="unchanged", message="Already current"))
    monkeypatch.setattr(catalog, "check_for_newer_catalog", check)

    assert catalog.main([]) == 0

    root, client = check.call_args.args
    assert root == Path(catalog.ROOT)
    assert client.delay_seconds == catalog.DEFAULT_DELAY_SECONDS
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().out == "Already current\n"
