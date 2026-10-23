"""New program selections stay isolated across every retrieval path."""

from hashlib import sha256
import json
from pathlib import Path

import pytest
from langchain_core.documents import Document

from src.config import Config
from src.document_processor import DocumentProcessor
from src.hybrid_retrieval import tokenize
from src.program_scope import (
    filter_program_documents,
    metadata_programs,
    normalize_program,
    query_programs,
    selected_programs,
)
from src.scrape_ischool import sync_pages

DOCS = Path(__file__).resolve().parents[1] / 'data' / 'advising_docs'


@pytest.mark.parametrize('program', ['mara', 'msis'])
def test_new_program_keys_and_ingestion(tmp_path, program):
    assert normalize_program(program) == program
    assert selected_programs('MLIS core courses', program) == {program.upper()}
    page = {'url': f'https://ischool.sjsu.edu/{program}-test',
            'title': 'Test fixture', 'text': 'Test fixture only'}
    if program == 'msis':
        with pytest.raises(ValueError, match='disabled'):
            sync_pages([page], tmp_path, programs=[program.upper()])
        return
    sync_pages([page], tmp_path, programs=[program.upper()])
    manifest = json.loads((tmp_path / 'scrape' / 'manifest.json').read_text())
    assert manifest['documents'][0]['programs'] == [program.upper()]


@pytest.mark.parametrize('query, expected', [
    ('MARA200 prerequisites', {'MARA'}),
    ('Master of Archives and Records Administration requirements', {'MARA'}),
    ('MS in Information Science requirements', {'MSIS'}),
    ('Master of Science in Information Science', {'MSIS'}),
    ('MSIS requirements', {'MSIS'}),
    ('MS in Informatics required courses', set()),
    ('INFM 211 capstone', set()),
    ('Compare MARA, MLIS, MSIS and ISDA', {'MARA', 'MLIS', 'MSIS', 'ISDA'}),
])
def test_named_program_hints(query, expected):
    assert query_programs(query) == expected


def test_program_tokens_and_serialized_filter():
    assert {'archive', 'record', 'administration'} <= set(tokenize('MARA'))
    assert 'informatic' not in set(tokenize('MSIS'))
    shared = Document(page_content='shared', metadata={'programs': '["MARA", "MSIS"]'})
    other = Document(page_content='other', metadata={'programs': ['MLIS']})
    untagged = Document(page_content='untagged')
    assert filter_program_documents([other, untagged, shared], 'mara') == [shared]
    assert filter_program_documents([other, untagged, shared], 'both') == [other, untagged, shared]


@pytest.fixture(scope='module')
def corpus_processor(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(Config, 'EMBEDDING_PROVIDER', 'hashing')
    patch.setattr(Config, 'VECTOR_STORE_PATH', str(tmp_path_factory.mktemp('mara-index')))
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    # Exercise MSIS filtering without claiming any MSIS curriculum facts.
    processor.vector_store.add_documents([
        Document(page_content='MSIS retrieval test fixture only', metadata={'programs': '["MSIS"]'}),
    ])
    yield processor
    patch.undo()


@pytest.mark.parametrize('mode', ['hybrid', 'vector', 'lexical', 'unreadable-index'])
@pytest.mark.parametrize('program', ['mara', 'msis'])
def test_selected_program_returns_only_matching_chunks(corpus_processor, monkeypatch, mode, program):
    monkeypatch.setenv('HYBRID_RETRIEVAL', 'false' if mode == 'vector' else 'true')
    if mode == 'lexical':
        def unavailable(*args, **kwargs):
            raise RuntimeError('Embedding unavailable')
        monkeypatch.setattr(corpus_processor.vector_store, 'similarity_search', unavailable)
    if mode == 'unreadable-index':
        monkeypatch.setattr('src.hybrid_retrieval._collection_records', lambda *args: [])
    hits = corpus_processor.retrieve_relevant_docs(
        f'{program.upper()} core courses and MLIS requirements', k=20, program=program)
    assert hits
    from src.program_scope import metadata_programs
    assert all(program.upper() in metadata_programs(hit.metadata) for hit in hits)


def test_real_mara_sources_are_citable_and_hashed(corpus_processor):
    documents = [doc for doc in corpus_processor.load_documents()
                 if doc.metadata.get('programs') == ['MARA']]
    assert len(documents) >= 3
    assert any('MARA 200' in doc.page_content for doc in documents)
    assert any('MARA 289' in doc.page_content for doc in documents)
    manifest = json.loads((DOCS / 'scrape' / 'manifest.json').read_text())
    assert {entry['source_url'] for entry in manifest['documents']
            if entry['programs'] == ['MARA']} == {doc.metadata['source_url'] for doc in documents}
    for doc in documents:
        assert doc.metadata['source_url'].startswith('https://ischool.sjsu.edu/')
        assert sha256(Path(doc.metadata['source']).read_bytes()).hexdigest() == doc.metadata['content_sha256']
    hits = corpus_processor.retrieve_relevant_docs('MARA required courses', k=8, program='mara')
    assert any(hit.metadata['source_url'].endswith('/mara-required-courses') for hit in hits)


@pytest.mark.parametrize('prefix', ['MARA', 'MSIS'])
def test_catalog_parsers_accept_new_prefixes(prefix):
    from src.catalog_update import course_listing_entries, extract_course_snapshot

    # Synthetic parser fixtures are never written into the advising corpus.
    title = f'{prefix} 200 - Parser fixture only'
    html = f'<p><h1 id="course_preview_title">{title}</h1><em>3</em> <em>unit(s)</em><br>Fixture description.</p>'
    source = 'https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=1'
    assert extract_course_snapshot(html, source, '2026-10-22').startswith(title + '\n')
    assert course_listing_entries(f'<a>{title}</a>', prefix=prefix) == [(f'{prefix} 200', 'Parser fixture only')]


@pytest.mark.parametrize('mode', ['hybrid', 'lexical'])
def test_mara_eval_questions_retrieve_official_evidence(corpus_processor, monkeypatch, mode):
    from scripts.eval_report import load_cases

    root = DOCS.parents[1]
    cases = load_cases(root / 'evals/advising_questions.json',
                       root / 'evals/retrieval_expectations.json')
    mara_cases = [case for case in cases if case['id'].startswith('mara-')]
    assert {case['id'] for case in mara_cases} == {
        'mara-core', 'mara-electives', 'mara-eportfolio',
        'mara-admission', 'mara-units', 'mara-apply',
    }
    monkeypatch.setenv('HYBRID_RETRIEVAL', 'false' if mode == 'vector' else 'true')
    if mode == 'lexical':
        def unavailable(*args, **kwargs):
            raise RuntimeError('Embedding unavailable')
        monkeypatch.setattr(corpus_processor.vector_store, 'similarity_search', unavailable)
    for case in mara_cases:
        hits = corpus_processor.retrieve_relevant_docs(case['question'], k=4, program='mara')
        sources = {hit.metadata.get('source_url') for hit in hits}
        assert sources.intersection(case['expected_sources']), (mode, case['id'], sources)
        assert all('MARA' in metadata_programs(hit.metadata) for hit in hits)


def test_mara_admissions_shared_sources_keep_existing_program_labels(corpus_processor):
    documents = {doc.metadata.get('source_url'): doc for doc in corpus_processor.load_documents()}
    for path in ('admissions', 'graduate-application'):
        doc = documents[f'https://ischool.sjsu.edu/{path}']
        assert {'MARA', 'MLIS'} <= set(doc.metadata['programs'])
        assert 'MSIS' not in doc.metadata['programs']
        assert sha256(Path(doc.metadata['source']).read_bytes()).hexdigest() == doc.metadata['content_sha256']
    admissions = documents['https://ischool.sjsu.edu/admissions'].page_content
    assert '3.0' in admissions and 'GMAT or GRE' in admissions


@pytest.mark.parametrize('program', ['mara', 'msis'])
def test_empty_selected_program_never_returns_other_programs(program):
    from src.hybrid_retrieval import hybrid_retrieve

    class OtherProgramStore:
        def get(self, **kwargs):
            return {'ids': ['other'], 'documents': ['MLIS core courses'],
                    'metadatas': [{'programs': '["MLIS"]'}]}

        def similarity_search(self, *args, **kwargs):
            return [Document(page_content='MLIS core courses', metadata={'programs': '["MLIS"]'})]

    assert hybrid_retrieve(OtherProgramStore(), 'core courses', k=4, program=program) == []
