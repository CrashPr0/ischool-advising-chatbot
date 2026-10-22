"""Program preference preserves the retrieval order within priority groups."""

import pytest
from langchain_core.documents import Document

from src.program_scope import prefer_program_documents


@pytest.mark.parametrize("program,other", [("mlis", "ISDA"), ("isda", "MLIS")])
def test_program_preference_is_stable_and_keeps_all_candidates(program, other):
    wanted = program.upper()
    other_first = Document(page_content="other first", metadata={"programs": [other]})
    untagged_first = Document(page_content="untagged first")
    wanted_first = Document(
        page_content="wanted first", metadata={"programs": [wanted]}
    )
    shared = Document(
        page_content="shared", metadata={"programs": ["ISDA", "MLIS"]}
    )
    other_second = Document(
        page_content="other second", metadata={"programs": [other]}
    )
    wanted_second = Document(
        page_content="wanted second", metadata={"programs": [wanted]}
    )
    untagged_second = Document(page_content="untagged second")
    original = [
        other_first,
        untagged_first,
        wanted_first,
        shared,
        other_second,
        wanted_second,
        untagged_second,
    ]
    before = list(original)

    ranked = prefer_program_documents(original, "Core courses?", program)

    assert ranked == [
        wanted_first,
        shared,
        wanted_second,
        untagged_first,
        untagged_second,
        other_first,
        other_second,
    ]
    assert original == before
    assert {id(doc) for doc in ranked} == {id(doc) for doc in original}


@pytest.mark.parametrize("query", ["Core courses?", "Compare ISDA and MLIS"])
def test_ambiguous_query_preserves_iterator_order(query):
    original = [
        Document(page_content="ISDA", metadata={"programs": ["ISDA"]}),
        Document(page_content="untagged"),
        Document(page_content="MLIS", metadata={"programs": ["MLIS"]}),
    ]

    ranked = prefer_program_documents(iter(original), query)

    assert isinstance(ranked, list)
    assert ranked == original
