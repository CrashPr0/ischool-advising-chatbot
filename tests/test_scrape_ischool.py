"""Offline tests for the bounded advising-document crawler."""

import json
from urllib.robotparser import RobotFileParser

from src.scrape_ischool import (
    SiteCrawler,
    host_allowed,
    normalize_url,
    sitemap_urls,
    sync_pages,
    write_pages,
)


def test_normalize_url_removes_fragments_and_rejects_non_http():
    assert normalize_url(" HTTPS://ISCHOOL.SJSU.EDU/path#section ") == (
        "https://ischool.sjsu.edu/path"
    )
    assert normalize_url("mailto:advising@sjsu.edu") is None


def test_normalize_url_removes_tracking_and_sorts_query_parameters():
    assert (
        normalize_url("https://ischool.sjsu.edu/page?utm_source=email&b=2&a=1#top")
        == "https://ischool.sjsu.edu/page?a=1&b=2"
    )


def test_host_allow_list_requires_exact_host():
    allowed = {"ischool.sjsu.edu"}
    assert host_allowed("https://ischool.sjsu.edu/page", allowed)
    assert not host_allowed("https://evil.ischool.sjsu.edu/page", allowed)


def test_sitemap_parser_handles_namespaces():
    xml = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://ischool.sjsu.edu/one</loc></url>
      <url><loc>https://ischool.sjsu.edu/two</loc></url>
    </urlset>
    """
    assert list(sitemap_urls(xml)) == [
        "https://ischool.sjsu.edu/one",
        "https://ischool.sjsu.edu/two",
    ]


def test_crawler_limits_are_bounded():
    crawler = SiteCrawler({"ischool.sjsu.edu"}, max_urls=100_000, max_bytes=1)
    assert crawler.max_urls == 10_000
    assert crawler.max_bytes == 10_000


def test_crawler_applies_path_scope_and_depth_limits():
    crawler = SiteCrawler(
        {"ischool.sjsu.edu"},
        max_depth=100,
        include_paths=[r"^/bsisda"],
        exclude_paths=[r"faculty$"],
    )
    assert crawler.max_depth == 10
    assert crawler._url_allowed("https://ischool.sjsu.edu/bsisda-advising")
    assert not crawler._url_allowed("https://ischool.sjsu.edu/mlis-advising")
    assert not crawler._url_allowed("https://ischool.sjsu.edu/bsisda-faculty")


def test_crawler_honors_cached_robots_policy():
    crawler = SiteCrawler({"ischool.sjsu.edu"})
    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /private"])
    crawler._robots["https://ischool.sjsu.edu"] = parser
    assert crawler._robots_allowed("https://ischool.sjsu.edu/bsisda")
    assert not crawler._robots_allowed("https://ischool.sjsu.edu/private/records")


def test_sitemap_itself_bypasses_page_path_scope(monkeypatch):
    crawler = SiteCrawler(
        {"ischool.sjsu.edu"}, include_paths=[r"^/bsisda"], respect_robots=False
    )

    def fake_fetch(url, *, enforce_path_scope=True):
        if url.endswith("sitemap.xml"):
            assert not enforce_path_scope
            return url, (
                "<urlset><url><loc>https://ischool.sjsu.edu/bsisda-advising"
                "</loc></url><url><loc>https://ischool.sjsu.edu/mlis-advising"
                "</loc></url></urlset>"
            )
        return url, "<html><title>ISDA</title><main>Approved content</main></html>"

    monkeypatch.setattr(crawler, "_fetch", fake_fetch)
    pages = crawler.crawl([], "https://ischool.sjsu.edu/sitemap.xml")

    assert [page["url"] for page in pages] == [
        "https://ischool.sjsu.edu/bsisda-advising"
    ]


def test_write_pages_creates_text_and_metadata(tmp_path):
    count = write_pages(
        [
            {
                "url": "https://ischool.sjsu.edu/advising",
                "title": "Advising",
                "text": "Official advising information.",
            }
        ],
        tmp_path,
    )

    assert count == 1
    text_files = list((tmp_path / "txt").glob("*.txt"))
    metadata_files = list((tmp_path / "meta").glob("*.yml"))
    assert len(text_files) == len(metadata_files) == 1
    assert (
        text_files[0]
        .read_text(encoding="utf-8")
        .startswith("Official advising information.")
    )


def test_sync_pages_tracks_changes_without_rewriting_unchanged_snapshots(tmp_path):
    page = {
        "url": "https://ischool.sjsu.edu/bsisda-advising",
        "title": "BSISDA Advising",
        "text": "Official ISDA advising information.",
    }
    first = sync_pages([page], tmp_path, programs=["ISDA"], topics=["advising"])
    text_path = next((tmp_path / "txt").glob("*.txt"))
    first_mtime = text_path.stat().st_mtime_ns
    second = sync_pages([page], tmp_path, programs=["ISDA"], topics=["advising"])

    assert first == {"processed": 1, "added": 1, "updated": 0, "unchanged": 0}
    assert second == {"processed": 1, "added": 0, "updated": 0, "unchanged": 1}
    assert text_path.stat().st_mtime_ns == first_mtime
    manifest = json.loads((tmp_path / "scrape" / "manifest.json").read_text())
    assert manifest["documents"][0]["programs"] == ["ISDA"]


def test_program_refresh_preserves_other_program_manifest_entries(tmp_path):
    isda = {
        "url": "https://ischool.sjsu.edu/bsisda-advising",
        "title": "ISDA",
        "text": "ISDA evidence",
    }
    mlis = {
        "url": "https://ischool.sjsu.edu/mlis-required-courses",
        "title": "MLIS",
        "text": "MLIS evidence",
    }
    sync_pages([isda], tmp_path, programs=["ISDA"])
    manifest_path = tmp_path / "scrape" / "manifest.json"
    old_isda = json.loads(manifest_path.read_text())["documents"][0]
    sync_pages([mlis], tmp_path, programs=["MLIS"])
    entries = json.loads(manifest_path.read_text())["documents"]
    assert old_isda in entries
    assert len(entries) == 2
    old_mlis = next(entry for entry in entries if entry["programs"] == ["MLIS"])
    replacement = dict(isda, url="https://ischool.sjsu.edu/bsisda-required-courses")
    sync_pages([replacement], tmp_path, programs=["ISDA"])
    entries = json.loads(manifest_path.read_text())["documents"]
    assert old_mlis in entries
    assert old_isda not in entries
    assert len(entries) == 2


def test_shared_page_keeps_both_programs_in_manifest_and_metadata(tmp_path):
    import yaml

    page = {
        "url": "https://ischool.sjsu.edu/advising-appointments",
        "title": "Advising",
        "text": "Shared advising evidence",
    }
    sync_pages([page], tmp_path, programs=["ISDA"])
    text_path = next((tmp_path / "txt").glob("*.txt"))
    original_mtime = text_path.stat().st_mtime_ns
    sync_pages([page], tmp_path, programs=["MLIS"])
    entries = json.loads((tmp_path / "scrape" / "manifest.json").read_text())[
        "documents"
    ]
    assert len(entries) == 1
    assert entries[0]["programs"] == ["ISDA", "MLIS"]
    metadata = yaml.safe_load(next((tmp_path / "meta").glob("*.yml")).read_text())
    assert metadata["programs"] == ["ISDA", "MLIS"]
    assert text_path.stat().st_mtime_ns == original_mtime


def test_sync_rejects_excluded_programs(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="only ISDA and MLIS"):
        sync_pages([], tmp_path, programs=["MARA"])
