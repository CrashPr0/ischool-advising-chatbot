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


@pytest.mark.parametrize('program', ['mara', 'msis'])
@pytest.mark.parametrize('question,title', [
    ('What GPA do I need for admission?', 'Admissions'),
    ('How do I apply to the program?', 'Graduate Application'),
])
def test_shared_graduate_titles_match_intent_within_selected_program(program, question, title):
    shared = Document(page_content='Official shared graduate page',
                      metadata={'programs': ['MARA', 'MSIS', 'MLIS'], 'title': title})
    curriculum = Document(page_content='Program curriculum',
                          metadata={'programs': [program.upper()], 'title': 'Required Courses'})
    other = Document(page_content='Other program',
                     metadata={'programs': ['ISDA'], 'title': title})
    assert prefer_program_documents([other, curriculum, shared], question, program) == [shared, curriculum, other]


def test_admission_title_does_not_override_graduation_or_ambiguous_programs():
    curriculum = Document(page_content='MARA courses', metadata={'programs': ['MARA']})
    admission = Document(page_content='Application', metadata={'programs': ['MARA'], 'title': 'Graduate Application'})
    original = [curriculum, admission]
    assert prefer_program_documents(original, 'How do I apply to graduate?', 'mara') == original
    assert prefer_program_documents(original, 'Compare MARA and MSIS admissions') == original


@pytest.mark.parametrize('program', ['mara', 'msis'])
def test_required_units_prefers_curriculum_without_overriding_specific_courses(program):
    required = Document(page_content='Required coursework', metadata={
        'programs': [program.upper()], 'title': 'Required Courses'})
    elective = Document(page_content='Elective coursework', metadata={
        'programs': [program.upper()], 'title': 'Electives'})
    course = Document(page_content='Course details', metadata={
        'programs': [program.upper()], 'title': f'{program.upper()} 289 Handbook'})
    original = [elective, course, required]
    assert prefer_program_documents(original, 'How many required course units?', program) == [
        required, elective, course]
    assert prefer_program_documents(original, f'Is {program.upper()} 289 a required course?', program) == [
        course, elective, required]
