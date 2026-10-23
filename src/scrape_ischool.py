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
import json
import logging
from pathlib import Path
import re
import time
from typing import Iterable
from urllib.parse import (
    parse_qsl,
    urlencode,
    urldefrag,
    urljoin,
    urlparse,
    urlunparse,
)
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
import trafilatura
import yaml

LOGGER = logging.getLogger(__name__)
DEFAULT_SEEDS = "./data/advising_docs/scrape/seed_urls.txt"
DEFAULT_USER_AGENT = "iSchoolAdvisingBot/1.0 (+offline-ingestion)"
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Return a canonical HTTP(S) URL without fragments or tracking fields."""
    if base:
        url = urljoin(base, url)
    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in TRACKING_QUERY_KEYS
        ),
        doseq=True,
    )
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            parsed.params,
            query,
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
        max_depth: int = 0,
        delay_seconds: float = 1.0,
        timeout_seconds: float = 15.0,
        max_bytes: int = 2_000_000,
        user_agent: str = DEFAULT_USER_AGENT,
        include_paths: Iterable[str] = (),
        exclude_paths: Iterable[str] = (),
        respect_robots: bool = True,
    ) -> None:
        self.allowed_hosts = {
            host.lower().strip().rstrip(".") for host in allowed_hosts if host.strip()
        }
        if not self.allowed_hosts:
            raise ValueError("At least one --allow-host value is required")
        self.max_urls = max(1, min(int(max_urls), 10_000))
        self.max_depth = max(0, min(int(max_depth), 10))
        self.delay_seconds = max(0.0, min(float(delay_seconds), 60.0))
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self.max_bytes = max(10_000, min(int(max_bytes), 20_000_000))
        self.user_agent = user_agent[:200]
        self.include_paths = [re.compile(pattern) for pattern in include_paths]
        self.exclude_paths = [re.compile(pattern) for pattern in exclude_paths]
        self.respect_robots = bool(respect_robots)
        self._robots: dict[str, RobotFileParser | None] = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def _url_allowed(self, url: str) -> bool:
        """Apply the host allow-list and optional path-level scope rules."""
        if not host_allowed(url, self.allowed_hosts):
            return False
        parsed = urlparse(url)
        target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if self.include_paths and not any(
            pattern.search(target) for pattern in self.include_paths
        ):
            return False
        if any(pattern.search(target) for pattern in self.exclude_paths):
            return False
        return True

    def _robots_allowed(self, url: str) -> bool:
        """Return whether the site's robots policy permits this crawler."""
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            robots_url = f"{origin}/robots.txt"
            parser = RobotFileParser()
            parser.set_url(robots_url)
            try:
                response = self.session.get(
                    robots_url,
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                )
                if response.status_code in {401, 403}:
                    LOGGER.warning("robots.txt denied crawler access: %s", origin)
                    self._robots[origin] = None
                    return False
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    # A missing robots file means no published restrictions.
                    parser.parse([])
                self._robots[origin] = parser
            except requests.RequestException as error:
                LOGGER.warning("Unable to read robots.txt for %s: %s", origin, error)
                parser.parse([])
                self._robots[origin] = parser

        parser = self._robots[origin]
        return bool(parser and parser.can_fetch(self.user_agent, url))

    def _fetch(
        self, url: str, *, enforce_path_scope: bool = True
    ) -> tuple[str, str] | None:
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
                        allowed_redirect = bool(
                            redirected
                            and (
                                self._url_allowed(redirected)
                                if enforce_path_scope
                                else host_allowed(redirected, self.allowed_hosts)
                            )
                        )
                        if not allowed_redirect:
                            LOGGER.warning(
                                "Skipping redirect outside allow-list: %s", location
                            )
                            return None
                        current_url = redirected
                        continue

                    response.raise_for_status()
                    final_url = normalize_url(current_url)
                    final_allowed = bool(
                        final_url
                        and (
                            self._url_allowed(final_url)
                            if enforce_path_scope
                            else host_allowed(final_url, self.allowed_hosts)
                        )
                    )
                    if not final_allowed:
                        LOGGER.warning(
                            "Skipping redirect outside allow-list: %s", current_url
                        )
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

    def crawl(
        self, seeds: Iterable[str], sitemap: str | None = None
    ) -> list[dict[str, str]]:
        queue: deque[tuple[str, int]] = deque()
        queued: set[str] = set()
        for raw_url in seeds:
            if len(queued) >= self.max_urls:
                break
            url = normalize_url(raw_url)
            if url and self._url_allowed(url):
                queue.append((url, 0))
                queued.add(url)

        if sitemap:
            sitemap_url = normalize_url(sitemap)
            if (
                sitemap_url
                and host_allowed(sitemap_url, self.allowed_hosts)
                and self._robots_allowed(sitemap_url)
            ):
                result = self._fetch(sitemap_url, enforce_path_scope=False)
                if result:
                    _, xml_text = result
                    try:
                        for raw_url in sitemap_urls(xml_text):
                            if len(queued) >= self.max_urls:
                                break
                            url = normalize_url(raw_url)
                            if url and self._url_allowed(url) and url not in queued:
                                queue.append((url, 0))
                                queued.add(url)
                    except ET.ParseError as error:
                        LOGGER.warning("Invalid sitemap XML: %s", error)

        visited: set[str] = set()
        pages: list[dict[str, str]] = []
        while queue and len(visited) < self.max_urls:
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            if not self._robots_allowed(url):
                LOGGER.warning("Skipping URL disallowed by robots.txt: %s", url)
                continue
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            result = self._fetch(url)
            if not result:
                continue
            final_url, html = result
            title, text = _safe_text(html)
            if text:
                pages.append({"url": final_url, "title": title, "text": text})

            if depth >= self.max_depth:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                if len(queued) >= self.max_urls:
                    break
                linked = normalize_url(anchor["href"], final_url)
                if linked and self._url_allowed(linked) and linked not in queued:
                    queue.append((linked, depth + 1))
                    queued.add(linked)
        return pages


def sync_pages(
    pages: Iterable[dict[str, str]],
    docs_root: Path,
    *,
    programs: Iterable[str] = ("ISDA",),
    topics: Iterable[str] = ("advising",),
) -> dict[str, int]:
    """Write snapshots; preserve manifest entries outside this run’s programs."""
    text_dir = docs_root / "txt"
    meta_dir = docs_root / "meta"
    manifest_path = docs_root / "scrape" / "manifest.json"
    text_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    program_list = sorted(
        {value.strip().upper() for value in programs if value.strip()}
    )
    if not program_list or not set(program_list) <= {"ISDA", "MLIS", "MARA", "MSIS"}:
        raise ValueError("The advising corpus supports only ISDA, MLIS, MARA, and MSIS")
    pages = list(pages)
    topic_list = sorted({value.strip() for value in topics if value.strip()})
    stats = {"processed": 0, "added": 0, "updated": 0, "unchanged": 0}
    # A program-specific refresh must not erase the other program's inventory.
    previous_entries = []
    if manifest_path.is_file():
        previous_entries = json.loads(manifest_path.read_text(encoding="utf-8")).get(
            "documents", []
        )
    manifest_entries = [
        entry for entry in previous_entries
        if not set(entry.get("programs", [])).intersection(program_list)
    ]
    for page in pages:
        doc_id = f"web-{sha256(page['url'].encode('utf-8')).hexdigest()[:16]}"
        text_path = text_dir / f"{doc_id}.txt"
        meta_path = meta_dir / f"{doc_id}.yml"
        # A shared URL has one snapshot, with both program labels.
        shared = next(
            (entry for entry in previous_entries if entry["source_url"] == page["url"]),
            None,
        )
        page_programs = sorted(set(program_list) | set((shared or {}).get("programs", [])))
        page_topics = sorted(set(topic_list) | set((shared or {}).get("topics", [])))
        content = page["text"].strip() + "\n"
        content_hash = sha256(content.encode("utf-8")).hexdigest()
        previous = (
            text_path.read_text(encoding="utf-8") if text_path.is_file() else None
        )
        if previous == content:
            stats["unchanged"] += 1
        else:
            stats["updated" if previous is not None else "added"] += 1
            text_path.write_text(content, encoding="utf-8")
            meta_path.write_text(
                yaml.safe_dump(
                    {
                        "doc_id": doc_id,
                        "title": page["title"] or page["url"],
                        "owner": "SJSU",
                        "source_type": "webpage",
                        "source_url": page["url"],
                        "retrieved_at": timestamp,
                        "content_sha256": content_hash,
                        "programs": page_programs,
                        "topics": page_topics,
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        if previous == content and meta_path.is_file():
            metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            if metadata.get("programs") != page_programs or metadata.get("topics") != page_topics:
                metadata.update(programs=page_programs, topics=page_topics)
                meta_path.write_text(
                    yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
        stats["processed"] += 1
        manifest_entries = [
            entry for entry in manifest_entries if entry["source_url"] != page["url"]
        ]
        manifest_entries.append(
            {
                "doc_id": doc_id,
                "source_url": page["url"],
                "title": page["title"] or page["url"],
                "content_sha256": content_hash,
                "programs": page_programs,
                "topics": page_topics,
            }
        )

    manifest = {
        "schema_version": 1,
        "documents": sorted(manifest_entries, key=lambda item: item["source_url"]),
    }
    serialized_manifest = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if (
        not manifest_path.is_file()
        or manifest_path.read_text(encoding="utf-8") != serialized_manifest
    ):
        manifest_path.write_text(serialized_manifest, encoding="utf-8")
    return stats


def write_pages(pages: Iterable[dict[str, str]], docs_root: Path) -> int:
    """Backward-compatible wrapper returning the number of processed pages."""
    return sync_pages(pages, docs_root)["processed"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-root", default="./data/advising_docs")
    parser.add_argument("--seeds", default=DEFAULT_SEEDS)
    parser.add_argument("--sitemap")
    parser.add_argument("--allow-host", action="append", required=True)
    parser.add_argument("--max-urls", type=int, default=200)
    parser.add_argument("--max-depth", type=int, default=0)
    parser.add_argument("--delay-s", type=float, default=1.0)
    parser.add_argument("--timeout-s", type=float, default=15.0)
    parser.add_argument("--max-bytes", type=int, default=2_000_000)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--include-path", action="append", default=[])
    parser.add_argument("--exclude-path", action="append", default=[])
    parser.add_argument("--program", action="append", default=[])
    parser.add_argument("--topic", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    crawler = SiteCrawler(
        args.allow_host,
        max_urls=args.max_urls,
        max_depth=args.max_depth,
        delay_seconds=args.delay_s,
        timeout_seconds=args.timeout_s,
        max_bytes=args.max_bytes,
        user_agent=args.user_agent,
        include_paths=args.include_path,
        exclude_paths=args.exclude_path,
    )
    pages = crawler.crawl(read_seed_urls(Path(args.seeds)), args.sitemap)
    stats = sync_pages(
        pages,
        Path(args.docs_root),
        programs=args.program or ["ISDA"],
        topics=args.topic or ["advising"],
    )
    print(
        f"Fetched {len(pages)} pages; "
        f"added {stats['added']}, updated {stats['updated']}, "
        f"unchanged {stats['unchanged']}."
    )
    return 0 if stats["processed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
