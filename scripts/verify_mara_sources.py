"""Check every MARA snapshot line against freshly fetched official SJSU HTML.

Run offline regression tests normally; run this command explicitly for a live audit.
No source text or audit files are written by this command.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests

from src.scrape_ischool import _safe_text


def unsupported_lines(snapshot, html):
    """Accept only text present in the source or its standard ingestion extraction."""
    def normalize(value):
        return re.sub(r"\s+", " ", value).strip()

    sources = [normalize(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)),
               normalize(_safe_text(html)[1])]
    return [line for line in snapshot.splitlines()
            if normalize(line) and not any(normalize(line) in source for source in sources)]


def verify_sources(docs_root):
    entries = json.loads((docs_root / 'scrape/manifest.json').read_text())['documents']
    results = []
    for entry in entries:
        if 'MARA' not in entry.get('programs', []):
            continue
        url = entry['source_url']
        if urlparse(url).scheme != 'https' or urlparse(url).hostname not in {
            'ischool.sjsu.edu', 'catalog.sjsu.edu'
        }:
            raise ValueError(f'Unofficial source: {url}')
        # Do not follow redirects to an unreviewed destination.
        response = requests.get(url, timeout=30, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError(f'Source unavailable: {url} ({response.status_code})')
        snapshot = (docs_root / 'txt' / f"{entry['doc_id']}.txt").read_bytes()
        if sha256(snapshot).hexdigest() != entry['content_sha256']:
            raise ValueError(f'Snapshot hash mismatch: {url}')
        missing = unsupported_lines(snapshot.decode('utf-8'), response.text)
        if missing:
            raise ValueError(f'Unsupported text at {url}: {missing!r}')
        results.append({'source_url': url, 'content_sha256': entry['content_sha256'],
                        'verified_source_sha256': sha256(response.content).hexdigest()})
    if not results:
        raise ValueError('No MARA documents found')
    return results


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1] / 'data/advising_docs'
    for result in verify_sources(root):
        print(json.dumps(result))
