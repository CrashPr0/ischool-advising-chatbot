"""Bounded, offline-to-disk iSchool web crawler for advising ingestion.

The crawler is deliberately a separate command rather than a query-time
fallback. It only follows explicitly allowed hosts, applies request and byte
limits, writes deterministic text/metadata files, and never accepts arbitrary
URLs from the web API.
"""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
from hashlib import sha256
import logging
from pathlib import Path
import time
from typing import Iterable
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
import trafilatura
import yaml


LOGGER = logging.getLogger(__name__)
DEFAULT_SEEDS = "./data/advising_docs/scrape/seed_urls.txt"
DEFAULT_USER_AGENT = "iSchoolAdvisingBot/1.0 (+offline-ingestion)"


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Return a canonical HTTP(S) URL without a fragment."""
    if base:
        url = urljoin(base, url)
    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        )
    )


def host_allowed(url: str, allowed_hosts: set[str]) -> bool:
    """Check an exact hostname against the operator allow-list."""
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    return bool(hostname and hostname in allowed_hosts)


def read_seed_urls(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"Seed file not found: {path}")
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def sitemap_urls(xml_text: str) -> Iterable[str]:
    """Yield URLs from standard sitemap XML, including namespaced documents."""
    root = ET.fromstring(xml_text)
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "loc" and element.text:
            yield element.text.strip()


def _safe_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = trafilatura.extract(
        html,
        include_links=True,
        include_tables=True,
        favor_precision=True,
    )
    if not text:
        text = soup.get_text("\n", strip=True)
    return title, text.strip()


class SiteCrawler:
    def __init__(
        self,
        allowed_hosts: Iterable[str],
        max_urls: int = 200,
        delay_seconds: float = 1.0,
        timeout_seconds: float = 15.0,
        max_bytes: int = 2_000_000,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.allowed_hosts = {host.lower().strip().rstrip(".") for host in allowed_hosts if host.strip()}
        if not self.allowed_hosts:
            raise ValueError("At least one --allow-host value is required")
        self.max_urls = max(1, min(int(max_urls), 10_000))
        self.delay_seconds = max(0.0, min(float(delay_seconds), 60.0))
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self.max_bytes = max(10_000, min(int(max_bytes), 20_000_000))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent[:200]})

    def _fetch(self, url: str) -> tuple[str, str] | None:
        try:
            current_url = url
            for _ in range(3):
                # Do not let requests follow an untrusted redirect before the
                # destination has been checked against the allow-list.
                with self.session.get(
                    current_url,
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                    stream=True,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("Location", "")
                        redirected = normalize_url(location, current_url)
                        if not redirected or not host_allowed(redirected, self.allowed_hosts):
                            LOGGER.warning("Skipping redirect outside allow-list: %s", location)
                            return None
                        current_url = redirected
                        continue

                    response.raise_for_status()
                    final_url = normalize_url(current_url)
                    if not final_url or not host_allowed(final_url, self.allowed_hosts):
                        LOGGER.warning("Skipping redirect outside allow-list: %s", current_url)
                        return None
                    content_type = response.headers.get("Content-Type", "").lower()
                    if content_type and not any(
                        kind in content_type for kind in ("html", "xml", "text")
                    ):
                        return None
                    declared_length = response.headers.get("Content-Length")
                    try:
                        declared_size = int(declared_length) if declared_length else 0
                    except ValueError:
                        declared_size = 0
                    if declared_size > self.max_bytes:
                        LOGGER.warning("Skipping oversized response: %s", url)
                        return None
                    chunks = []
                    size = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        size += len(chunk)
                        if size > self.max_bytes:
                            LOGGER.warning("Skipping oversized response: %s", url)
                            return None
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    encoding = response.encoding or "utf-8"
                    return final_url, body.decode(encoding, errors="replace")

            LOGGER.warning("Too many redirects: %s", url)
            return None
        except (requests.RequestException, UnicodeError) as error:
            LOGGER.warning("Failed to fetch %s: %s", url, error)
            return None

    def crawl(self, seeds: Iterable[str], sitemap: str | None = None) -> list[dict[str, str]]:
        queue: deque[str] = deque()
        queued: set[str] = set()
        for raw_url in seeds:
            if len(queued) >= self.max_urls:
                break
            url = normalize_url(raw_url)
            if url and host_allowed(url, self.allowed_hosts):
                queue.append(url)
                queued.add(url)

        if sitemap:
            sitemap_url = normalize_url(sitemap)
            if sitemap_url and host_allowed(sitemap_url, self.allowed_hosts):
                result = self._fetch(sitemap_url)
                if result:
                    _, xml_text = result
                    try:
                        for raw_url in sitemap_urls(xml_text):
                            if len(queued) >= self.max_urls:
                                break
                            url = normalize_url(raw_url)
                            if url and host_allowed(url, self.allowed_hosts) and url not in queued:
                                queue.append(url)
                                queued.add(url)
                    except ET.ParseError as error:
                        LOGGER.warning("Invalid sitemap XML: %s", error)

        visited: set[str] = set()
        pages: list[dict[str, str]] = []
        while queue and len(visited) < self.max_urls:
            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            result = self._fetch(url)
            if not result:
                continue
            final_url, html = result
            title, text = _safe_text(html)
            if text:
                pages.append({"url": final_url, "title": title, "text": text})

            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                if len(queued) >= self.max_urls:
                    break
                linked = normalize_url(anchor["href"], final_url)
                if linked and host_allowed(linked, self.allowed_hosts) and linked not in queued:
                    queue.append(linked)
                    queued.add(linked)
        return pages


def write_pages(pages: Iterable[dict[str, str]], docs_root: Path) -> int:
    text_dir = docs_root / "txt"
    meta_dir = docs_root / "meta"
    text_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    count = 0
    for page in pages:
        doc_id = f"web-{sha256(page['url'].encode('utf-8')).hexdigest()[:16]}"
        text_path = text_dir / f"{doc_id}.txt"
        meta_path = meta_dir / f"{doc_id}.yml"
        text_path.write_text(page["text"] + "\n", encoding="utf-8")
        meta_path.write_text(
            yaml.safe_dump(
                {
                    "doc_id": doc_id,
                    "title": page["title"] or page["url"],
                    "topics": ["scraped-webpage"],
                    "source_url": page["url"],
                    "retrieved_at": timestamp,
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        count += 1
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-root", default="./data/advising_docs")
    parser.add_argument("--seeds", default=DEFAULT_SEEDS)
    parser.add_argument("--sitemap")
    parser.add_argument("--allow-host", action="append", required=True)
    parser.add_argument("--max-urls", type=int, default=200)
    parser.add_argument("--delay-s", type=float, default=1.0)
    parser.add_argument("--timeout-s", type=float, default=15.0)
    parser.add_argument("--max-bytes", type=int, default=2_000_000)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    crawler = SiteCrawler(
        args.allow_host,
        max_urls=args.max_urls,
        delay_seconds=args.delay_s,
        timeout_seconds=args.timeout_s,
        max_bytes=args.max_bytes,
        user_agent=args.user_agent,
    )
    pages = crawler.crawl(read_seed_urls(Path(args.seeds)), args.sitemap)
    written = write_pages(pages, Path(args.docs_root))
    print(f"Fetched {len(pages)} pages; wrote {written} text/metadata pairs.")
    return 0 if written else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
