"""Offline tests for the bounded advising-document crawler."""

from src.scrape_ischool import (
    SiteCrawler,
    host_allowed,
    normalize_url,
    sitemap_urls,
    write_pages,
)


def test_normalize_url_removes_fragments_and_rejects_non_http():
    assert normalize_url(" HTTPS://ISCHOOL.SJSU.EDU/path#section ") == (
        "https://ischool.sjsu.edu/path"
    )
    assert normalize_url("mailto:advising@sjsu.edu") is None


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
    assert text_files[0].read_text(encoding="utf-8").startswith(
        "Official advising information."
    )
