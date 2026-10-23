"""Replace personal information in text before it is stored.

The patterns cover SJSU 9-digit student IDs, email addresses, US phone
numbers, and US street addresses. Course codes (INFO 202, ISDA 150), years,
dates, and shorter numbers are left unchanged.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED_STUDENT_ID = "[REDACTED_STUDENT_ID]"
REDACTED_EMAIL = "[REDACTED_EMAIL]"
REDACTED_PHONE = "[REDACTED_PHONE]"
REDACTED_ADDRESS = "[REDACTED_ADDRESS]"

# 9 contiguous digits, or the dashed 3-3-3 form (012-345-678). A following
# digit keeps phone numbers and longer ids intact.
_STUDENT_ID_RE = re.compile(r"(?<!\d)(?:\d{9}|\d{3}-\d{3}-\d{3})(?!\d)")

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# (408) 555-1234, 408-555-1234, 408.555.1234, +1 408 555 1234, and the same
# shapes with a leading 1. Parentheses delimit the area code even without a
# following space. Other groups require separators so a bare digit run is
# not treated as a phone.
_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:\+?1[\s.-]+)?"
    r"(?:\(\s*\d{3}\s*\)[\s.-]*|\d{3}[\s.-]+)"
    r"\d{3}[\s.-]+\d{4}"
    r"(?!\d)"
)

# Match whole suffixes so words like "Stages" and "Roadmaps" stay intact.
_STREET_SUFFIX = (
    r"(?:Street|Avenue|Boulevard|Drive|Court|Place|Lane|Road|Way|"
    r"St|Ave|Blvd|Rd|Dr|Ln|Ct|Pl)\b\.?"
)
_UNIT = r"(?:\s+(?:Apt|Unit)\.?\s*[A-Za-z0-9-]+|\s*#\s*[A-Za-z0-9-]+)"
_ADDRESS_RE = re.compile(
    rf"\b\d{{1,6}}\s+(?:[A-Za-z0-9.'-]+\s+){{1,5}}{_STREET_SUFFIX}(?:{_UNIT})?",
    re.IGNORECASE,
)


def scrub_pii(text: str) -> str:
    """Return ``text`` with personal information replaced by redaction tokens."""
    if not isinstance(text, str):
        raise TypeError("scrub_pii expects a string")
    if not text:
        return text
    scrubbed = _EMAIL_RE.sub(REDACTED_EMAIL, text)
    scrubbed = _PHONE_RE.sub(REDACTED_PHONE, scrubbed)
    scrubbed = _ADDRESS_RE.sub(REDACTED_ADDRESS, scrubbed)
    scrubbed = _STUDENT_ID_RE.sub(REDACTED_STUDENT_ID, scrubbed)
    return scrubbed


def scrub_pii_value(value: Any) -> Any:
    """Scrub strings inside nested dicts and lists. Other objects are unchanged.

    Dict keys are left as-is. Only string values are rewritten.
    """
    if isinstance(value, str):
        return scrub_pii(value)
    if isinstance(value, list):
        return [scrub_pii_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_pii_value(item) for item in value)
    if isinstance(value, dict):
        return {key: scrub_pii_value(item) for key, item in value.items()}
    return value
