"""Offline regressions for the live MARA source audit and reserved MSIS scope."""
from hashlib import sha256
import json
from pathlib import Path

import pytest
import yaml

from scripts.verify_mara_sources import mara_entries, unsupported_lines, verify_sources

DOCS = Path(__file__).resolve().parents[1] / 'data/advising_docs'


def test_audit_rejects_changed_units_courses_and_deadlines():
    html = '<main><h1>MARA</h1><p>42 Units</p><p>MARA 200</p></main>'
    assert unsupported_lines('MARA\n42 Units\nMARA 200\n', html) == []
    assert unsupported_lines('43 Units\nMARA 999\nApply by November 1', html) == [
        '43 Units', 'MARA 999', 'Apply by November 1']


def test_all_mara_snapshots_match_verified_content_hashes():
    mara = list(mara_entries(DOCS))
    assert len(mara) == 14
    for entry in mara:
        metadata = yaml.safe_load((DOCS / 'meta' / f"{entry['doc_id']}.yml").read_text())
        digest = sha256((DOCS / 'txt' / f"{entry['doc_id']}.txt").read_bytes()).hexdigest()
        assert digest == entry['content_sha256'] == metadata['verified_content_sha256']
        assert metadata['source_url'] == entry['source_url']
        assert metadata['verified_at']
        assert len(metadata['verified_source_sha256']) == 64


def test_msis_content_is_official_informatics_pages_only():
    entries = json.loads((DOCS / 'scrape/manifest.json').read_text())['documents']
    metadata = [yaml.safe_load(path.read_text()) for path in (DOCS / 'meta').glob('*.yml')]
    msis = [entry for entry in entries + metadata if 'MSIS' in entry.get('programs', [])]
    assert msis
    for entry in msis:
        url = entry.get('source_url', '').lower()
        assert url.startswith('https://ischool.sjsu.edu/')
        assert 'informatics' in url or '/infm-' in url


def test_live_audit_fails_closed_on_unavailable_source(tmp_path, monkeypatch):
    (tmp_path / 'scrape').mkdir()
    (tmp_path / 'scrape/manifest.json').write_text(json.dumps({'documents': [
        {'programs': ['MARA'], 'source_url': 'https://ischool.sjsu.edu/mara-test'}]}))
    class Unavailable:
        status_code = 503
    monkeypatch.setattr('scripts.verify_mara_sources.requests.get', lambda *a, **kw: Unavailable())
    with pytest.raises(ValueError, match='Source unavailable'):
        verify_sources(tmp_path)


@pytest.mark.parametrize('in_manifest', [True, False])
@pytest.mark.parametrize('mention', ['MARA', 'Master of Archives and Records Administration'])
def test_audit_checks_shared_and_metadata_only_documents(tmp_path, monkeypatch, in_manifest, mention):
    for directory in ('scrape', 'meta', 'txt'):
        (tmp_path / directory).mkdir()
    snapshot = f'{mention} students: apply by November 1\n'
    entry = {'doc_id': 'shared', 'programs': ['MLIS'],
             'source_url': 'https://ischool.sjsu.edu/shared-fixture',
             'content_sha256': sha256(snapshot.encode()).hexdigest()}
    (tmp_path / 'scrape/manifest.json').write_text(json.dumps({
        'documents': [entry] if in_manifest else []}))
    (tmp_path / 'meta/shared.yml').write_text(yaml.safe_dump(entry))
    (tmp_path / 'txt/shared.txt').write_text(snapshot)
    assert list(mara_entries(tmp_path)) == [entry]

    class Source:
        status_code = 200
        text = f'<main><p>{mention} students: apply by December 1</p></main>'
        content = text.encode()

    monkeypatch.setattr('scripts.verify_mara_sources.requests.get', lambda *a, **kw: Source())
    with pytest.raises(ValueError, match='Unsupported text'):
        verify_sources(tmp_path)
    Source.text = f'<main><p>{snapshot}</p></main>'
    assert verify_sources(tmp_path)[0]['source_url'] == entry['source_url']


def test_mara_audit_covers_shared_pages_without_changing_program_labels():
    entries = list(mara_entries(DOCS))
    sources = {entry['source_url'] for entry in entries}
    for path in ('application-deadlines', 'advising-appointments', 'mlis-electives',
                 'mlis-required-courses', 'mlis-program-learning-outcomes', 'informatics-faqs'):
        assert f'https://ischool.sjsu.edu/{path}' in sources
    assert len({entry['doc_id'] for entry in entries}) == len(entries)
