"""Input and container contracts for recursive PII scrubbing."""

from copy import deepcopy

import pytest

from src.pii_scrubber import (
    REDACTED_EMAIL,
    REDACTED_STUDENT_ID,
    scrub_pii,
    scrub_pii_value,
)


@pytest.mark.parametrize("value", [None, 0, False, b"ada@sjsu.edu", [], {}])
def test_scrub_pii_rejects_non_strings(value):
    with pytest.raises(TypeError, match="^scrub_pii expects a string$"):
        scrub_pii(value)


def test_recursive_scrubbing_preserves_tuple_shape_keys_and_input():
    payload = {
        "ada@sjsu.edu": (
            "Contact ada@sjsu.edu",
            [{"student": "012345678", "course": "INFO 202"}],
            (None, False, 42),
        )
    }
    original = deepcopy(payload)

    scrubbed = scrub_pii_value(payload)

    assert scrubbed == {
        "ada@sjsu.edu": (
            f"Contact {REDACTED_EMAIL}",
            [{"student": REDACTED_STUDENT_ID, "course": "INFO 202"}],
            (None, False, 42),
        )
    }
    assert payload == original
    assert scrub_pii_value(scrubbed) == scrubbed

    # Mutating the result must not change a caller's nested transcript data.
    scrubbed["ada@sjsu.edu"][1][0]["course"] = "ISDA 150"
    assert payload == original


@pytest.mark.parametrize("value", [None, False, 42, 3.5, b"ada@sjsu.edu"])
def test_recursive_scrubbing_preserves_non_text_values(value):
    assert scrub_pii_value(value) is value


def test_student_id_redaction_does_not_consume_longer_digit_runs():
    text = "012345678 0012345678 0123456789 00123456789"

    assert scrub_pii(text) == (
        f"{REDACTED_STUDENT_ID} 0012345678 0123456789 00123456789"
    )
