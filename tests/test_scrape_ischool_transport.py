"""Exercise crawler transport and ingestion without making network requests."""

import json

import pytest
import requests
import yaml

from src import scrape_ischool as scrape

ORIGIN = "https://ischool.sjsu.edu"


class Response:
    def __init__(self, body=b"", *, status=200, headers=None, chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = status in {301, 302, 303, 307, 308}
        self.encoding = None
        self.text = body.decode("utf-8")
        self.chunks = chunks if chunks is not None else [body]
        self.consumed = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            self.consumed.append(chunk)
            yield chunk


class Session:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def crawler_with_responses(monkeypatch, responses, **kwargs):
    session = Session(responses)
    monkeypatch.setattr(scrape.requests, "Session", lambda: session)
    crawler = scrape.SiteCrawler(["ischool.sjsu.edu"], delay_seconds=0, **kwargs)
    return crawler, session


def test_fetch_follows_only_checked_redirects_and_decodes_stream(monkeypatch):
    redirect = Response(status=302, headers={"Location": "/advising?utm_source=x#top"})
    page = Response(
        chunks=[b"Advising caf", b"\xc3\xa9"], headers={"Content-Type": "text/html"}
    )
    crawler, session = crawler_with_responses(
        monkeypatch, {ORIGIN + "/start": redirect, ORIGIN + "/advising": page}
    )

    assert crawler._fetch(ORIGIN + "/start") == (ORIGIN + "/advising", "Advising café")
    assert [url for url, _ in session.calls] == [
        ORIGIN + "/start",
        ORIGIN + "/advising",
    ]
    assert all(
        options == {"timeout": 15.0, "allow_redirects": False, "stream": True}
        for _, options in session.calls
    )
    assert redirect.closed and page.closed


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.ischool.sjsu.edu/advising",
        "/private/records",
        "mailto:advising@example.test",
    ],
)
def test_fetch_rejects_redirect_before_requesting_destination(monkeypatch, location):
    response = Response(status=302, headers={"Location": location})
    crawler, session = crawler_with_responses(
        monkeypatch, {ORIGIN + "/advising": response}, include_paths=[r"^/advising"]
    )

    assert crawler._fetch(ORIGIN + "/advising") is None
    assert [url for url, _ in session.calls] == [ORIGIN + "/advising"]
    assert response.closed


def test_fetch_stops_redirect_loop(monkeypatch, caplog):
    response = Response(status=302, headers={"Location": "/loop"})
    crawler, session = crawler_with_responses(monkeypatch, {ORIGIN + "/loop": response})

    assert crawler._fetch(ORIGIN + "/loop") is None
    assert len(session.calls) == 3
    assert "Too many redirects" in caplog.text


@pytest.mark.parametrize(
    "headers,chunks,consumed,expected",
    [
        ({"Content-Type": "application/pdf"}, [b"pdf"], [], None),
        ({"Content-Length": "10001"}, [b"large"], [], None),
        ({}, [b"a" * 6000, b"b" * 5000, b"unread"], [6000, 5000], None),
        ({"Content-Length": "invalid"}, [b"useful text"], [11], "useful text"),
        ({"Content-Length": "10000"}, [b"x" * 10000], [10000], "x" * 10000),
    ],
)
def test_fetch_enforces_type_and_byte_limits_before_consuming_more(
    monkeypatch, headers, chunks, consumed, expected
):
    response = Response(headers=headers, chunks=chunks)
    crawler, _ = crawler_with_responses(
        monkeypatch, {ORIGIN + "/page": response}, max_bytes=10000
    )

    result = crawler._fetch(ORIGIN + "/page")

    assert result == (None if expected is None else (ORIGIN + "/page", expected))
    assert [len(chunk) for chunk in response.consumed] == consumed
    assert response.closed


@pytest.mark.parametrize(
    "response", [requests.Timeout("timed out"), Response(status=503)]
)
def test_fetch_logs_request_failures_without_returning_content(
    monkeypatch, caplog, response
):
    crawler, _ = crawler_with_responses(monkeypatch, {ORIGIN + "/page": response})

    assert crawler._fetch(ORIGIN + "/page") is None
    assert "Failed to fetch" in caplog.text
    if isinstance(response, Response):
        assert response.closed


@pytest.mark.parametrize(
    "response,public,private",
    [
        (Response(b"User-agent: *\nDisallow: /private"), True, False),
        (Response(status=401), False, False),
        (Response(status=403), False, False),
        (Response(status=404), True, True),
        (requests.ConnectionError("offline"), True, True),
    ],
)
def test_robots_policy_is_loaded_once_and_reused(
    monkeypatch, response, public, private
):
    crawler, session = crawler_with_responses(
        monkeypatch, {ORIGIN + "/robots.txt": response}
    )

    assert crawler._robots_allowed(ORIGIN + "/advising") is public
    assert crawler._robots_allowed(ORIGIN + "/private/records") is private
    assert session.calls == [
        (ORIGIN + "/robots.txt", {"timeout": 15.0, "allow_redirects": False})
    ]


def test_crawl_deduplicates_links_and_stops_at_depth_boundary(monkeypatch):
    root = Response(
        b"<title>Advising</title><main>Official guidance</main>"
        b'<a href="/child#one">one</a><a href="/child?utm_source=x">again</a>'
        b'<a href="https://outside.example/page">outside</a>'
        b'<a href="/broken">broken</a>'
    )
    child = Response(
        b'<title>Child</title><main>Child guidance</main><a href="/grandchild">next</a>'
    )
    crawler, session = crawler_with_responses(
        monkeypatch,
        {
            ORIGIN + "/": root,
            ORIGIN + "/child": child,
            ORIGIN + "/broken": requests.Timeout("unavailable"),
        },
        respect_robots=False,
        max_depth=1,
    )
    monkeypatch.setattr(scrape.trafilatura, "extract", lambda *args, **kwargs: None)

    pages = crawler.crawl([ORIGIN, ORIGIN + "/#duplicate", "mailto:no@example.test"])

    assert [page["url"] for page in pages] == [ORIGIN + "/", ORIGIN + "/child"]
    assert pages[0]["title"] == "Advising"
    assert "Official guidance" in pages[0]["text"]
    assert [url for url, _ in session.calls] == [
        ORIGIN + "/",
        ORIGIN + "/child",
        ORIGIN + "/broken",
    ]


def test_crawl_budget_includes_robots_denied_pages(monkeypatch, caplog):
    crawler, session = crawler_with_responses(
        monkeypatch,
        {
            ORIGIN + "/robots.txt": Response(b"User-agent: *\nDisallow: /private"),
        },
        max_urls=1,
    )

    assert crawler.crawl([ORIGIN + "/private", ORIGIN + "/advising"]) == []
    assert [url for url, _ in session.calls] == [ORIGIN + "/robots.txt"]
    assert "disallowed by robots.txt" in caplog.text


def test_bad_sitemap_does_not_discard_seed_pages(monkeypatch, caplog):
    crawler, session = crawler_with_responses(
        monkeypatch,
        {
            ORIGIN + "/sitemap.xml": Response(b"<not-closed>"),
            ORIGIN + "/advising": Response(b"<main>Seed guidance</main>"),
        },
        respect_robots=False,
    )

    pages = crawler.crawl([ORIGIN + "/advising"], ORIGIN + "/sitemap.xml")

    assert [page["url"] for page in pages] == [ORIGIN + "/advising"]
    assert "Invalid sitemap XML" in caplog.text
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    "body,exit_code",
    [(b"<title>MLIS</title><main>Degree guidance</main>", 0), (b"", 1)],
)
def test_cli_writes_fetched_snapshots_and_reports_empty_runs(
    monkeypatch, tmp_path, capsys, body, exit_code
):
    crawler_with_responses(
        monkeypatch,
        {
            ORIGIN + "/robots.txt": Response(status=404),
            ORIGIN + "/advising": Response(body),
        },
    )
    seeds = tmp_path / "seeds.txt"
    seeds.write_text(f"# approved pages\n\n{ORIGIN}/advising\n", encoding="utf-8")
    docs = tmp_path / "docs"

    assert (
        scrape.main(
            [
                "--seeds",
                str(seeds),
                "--docs-root",
                str(docs),
                "--allow-host",
                "ischool.sjsu.edu",
                "--delay-s",
                "0",
                "--program",
                "MLIS",
                "--topic",
                "degree requirements",
            ]
        )
        == exit_code
    )

    manifest = json.loads((docs / "scrape" / "manifest.json").read_text())
    output = capsys.readouterr().out
    if exit_code == 0:
        assert "Fetched 1 pages; added 1, updated 0, unchanged 0." in output
        (entry,) = manifest["documents"]
        assert entry["source_url"] == ORIGIN + "/advising"
        assert entry["programs"] == ["MLIS"]
        assert entry["topics"] == ["degree requirements"]
        metadata = yaml.safe_load(next((docs / "meta").glob("*.yml")).read_text())
        assert metadata["content_sha256"] == entry["content_sha256"]
        assert "Degree guidance" in next((docs / "txt").glob("*.txt")).read_text()
    else:
        assert "Fetched 0 pages; added 0, updated 0, unchanged 0." in output
        assert manifest["documents"] == []
        assert list((docs / "txt").iterdir()) == []
