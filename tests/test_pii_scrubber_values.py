"""Recursive scrubbing preserves payload structure and caller-owned values."""

from copy import deepcopy

import pytest

from src.pii_scrubber import (
    REDACTED_EMAIL,
    REDACTED_STUDENT_ID,
    scrub_pii,
    scrub_pii_value,
)


def test_scrub_pii_value_preserves_nested_tuples_keys_and_input():
    payload = {
        "ada@sjsu.edu": (
            "Contact ada@sjsu.edu",
            [{"student": ("012345678", "INFO 202")}],
        ),
        "empty": (),
    }
    original = deepcopy(payload)

    scrubbed = scrub_pii_value(payload)

    assert scrubbed == {
        "ada@sjsu.edu": (
            f"Contact {REDACTED_EMAIL}",
            [{"student": (REDACTED_STUDENT_ID, "INFO 202")}],
        ),
        "empty": (),
    }
    assert payload == original
    assert scrubbed is not payload
    assert scrubbed["ada@sjsu.edu"][1] is not payload["ada@sjsu.edu"][1]
    assert scrub_pii_value(scrubbed) == scrubbed


@pytest.mark.parametrize("value", [None, False, 0, 3.5, b"ada@sjsu.edu", object()])
def test_scrub_pii_value_leaves_non_text_objects_unchanged(value):
    assert scrub_pii_value(value) is value


@pytest.mark.parametrize("value", [None, False, 0, b"ada@sjsu.edu", [], {}])
def test_scrub_pii_rejects_non_string_input(value):
    with pytest.raises(TypeError, match="scrub_pii expects a string"):
        scrub_pii(value)


def test_scrub_pii_accepts_empty_text():
    assert scrub_pii("") == ""
