"""Check catalog.sjsu.edu and refresh ISDA snapshots when a newer year is current.

The GitHub Actions workflow runs this weekly and from the Actions tab
(workflow_dispatch). Exit status is 0 when the catalog is unchanged and when
the site cannot be read. Files change only when a newer academic year was
published and the ISDA pages were refreshed. This script does not push and
does not open a pull request; the workflow does that after pytest passes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.catalog_update import (  # noqa: E402
    USER_AGENT,
    CatalogCheckResult,
    FetchResult,
    check_for_newer_catalog,
    pull_request_body,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 20.0


class CatalogClient:
    """GET catalog.sjsu.edu pages with a short pause between requests."""

    def __init__(
        self,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_fetch = 0.0

    def __call__(self, url: str) -> FetchResult:
        if self.delay_seconds and self._last_fetch:
            wait = self.delay_seconds - (time.monotonic() - self._last_fetch)
            if wait > 0:
                time.sleep(wait)
        self._last_fetch = time.monotonic()
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or hostname != "catalog.sjsu.edu":
            return FetchResult(
                status=0, error="URL is outside catalog.sjsu.edu", blocked=True
            )
        try:
            response = self.session.get(
                url, timeout=self.timeout_seconds, allow_redirects=False
            )
        except requests.RequestException as error:
            LOGGER.warning("Catalog fetch failed for %s: %s", url, error)
            return FetchResult(status=0, error=str(error))
        body = response.text or ""
        header = str(response.headers.get("x-amzn-waf-action") or "")
        blocked = response.status_code == 202 or bool(header)
        if response.is_redirect:
            LOGGER.warning("Catalog fetch redirected for %s", url)
            return FetchResult(
                status=response.status_code, error="redirect", blocked=True
            )
        if blocked:
            LOGGER.warning(
                "Catalog fetch was challenged for %s (HTTP %s)",
                url,
                response.status_code,
            )
        return FetchResult(status=response.status_code, body=body, blocked=blocked)


def write_result(result: CatalogCheckResult, path: str, pr_body: str = "") -> None:
    payload = json.dumps(result.as_dict(), indent=2) + "\n"
    if path:
        Path(path).write_text(payload, encoding="utf-8")
    github_output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if pr_body and result.status == "updated":
        Path(pr_body).write_text(pull_request_body(result), encoding="utf-8")
    if github_output:
        changed = "true" if result.status == "updated" else "false"
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"changed={changed}\n")
            handle.write(f"status={result.status}\n")
            handle.write(f"year={result.year}\n")
            handle.write(f"catoid={result.catoid}\n")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", default="", help="Write the JSON result here.")
    parser.add_argument(
        "--pr-body", default="", help="Write review pull request text when updated."
    )
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--delay-s", type=float, default=DEFAULT_DELAY_SECONDS)
    args = parser.parse_args(argv)
    result = check_for_newer_catalog(
        Path(args.repo_root),
        CatalogClient(delay_seconds=args.delay_s),
    )
    write_result(result, args.result, args.pr_body)
    print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
