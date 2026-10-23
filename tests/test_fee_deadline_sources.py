"""'What is the fee payment deadline?' retrieves the official Bursar source."""

from hashlib import sha256
import json
from pathlib import Path

import pytest

from src.config import Config
from src.document_processor import DocumentProcessor
from src.program_scope import metadata_programs

DOCS = Path(__file__).resolve().parents[1] / 'data' / 'advising_docs'
BURSAR = 'https://www.sjsu.edu/bursar/fees-due-dates/payment-due-dates/'
FEE_SOURCES = {
    BURSAR + 'index.php',
    BURSAR + 'fall.php',
    BURSAR + 'spring.php',
    'https://www.sjsu.edu/bursar/payment-refunds/pay-now.php',
    'https://www.sjsu.edu/bursar/payment-refunds/index.php',
    'https://www.sjsu.edu/registrar/calendar/',
    'https://www.sjsu.edu/registrar/calendar/spring-2027.php',
}
PROGRAMS = ['ISDA', 'MLIS']


@pytest.fixture(scope='module')
def corpus_processor(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(Config, 'EMBEDDING_PROVIDER', 'hashing')
    patch.setattr(Config, 'VECTOR_STORE_PATH', str(tmp_path_factory.mktemp('fee-index')))
    processor = DocumentProcessor(str(DOCS))
    processor.process_and_index()
    yield processor
    patch.undo()


def test_fee_sources_are_shared_hashed_and_in_manifest(corpus_processor):
    documents = {doc.metadata.get('source_url'): doc
                 for doc in corpus_processor.load_documents()}
    manifest = {entry['source_url']: entry for entry in json.loads(
        (DOCS / 'scrape' / 'manifest.json').read_text())['documents']}
    for url in FEE_SOURCES:
        doc = documents[url]
        assert doc.metadata['programs'] == PROGRAMS
        assert manifest[url]['programs'] == PROGRAMS
        assert doc.metadata['content_sha256'] == manifest[url]['content_sha256']
        assert sha256(Path(doc.metadata['source']).read_bytes()).hexdigest() == \
            doc.metadata['content_sha256']


@pytest.mark.parametrize('mode', ['hybrid', 'lexical'])
@pytest.mark.parametrize('program', ['isda', 'mlis'])
def test_fee_payment_deadline_retrieves_bursar_source(
        corpus_processor, monkeypatch, mode, program):
    monkeypatch.setenv('HYBRID_RETRIEVAL', 'true')
    if mode == 'lexical':
        def unavailable(*args, **kwargs):
            raise RuntimeError('Embedding unavailable')
        monkeypatch.setattr(corpus_processor.vector_store, 'similarity_search', unavailable)
    question = 'What is the fee payment deadline?'
    hits = corpus_processor.retrieve_relevant_docs(question, k=4, program=program)
    assert any(hit.metadata.get('source_url', '').startswith('https://www.sjsu.edu/bursar/')
               for hit in hits), [hit.metadata.get('source_url') for hit in hits]
    # The dated Payment Due Dates pages must be reachable with a wider window.
    hits = corpus_processor.retrieve_relevant_docs(question, k=8, program=program)
    bursar = [hit for hit in hits
              if hit.metadata.get('source_url', '').startswith(BURSAR)]
    assert bursar, [hit.metadata.get('source_url') for hit in hits]
    assert all(program.upper() in metadata_programs(hit.metadata) for hit in hits)
    evidence = ' '.join(' '.join(hit.page_content.split()) for hit in bursar)
    assert 'Payment Due Date' in evidence
