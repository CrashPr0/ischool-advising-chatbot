"""Topic clusters for summary chunks and top-k topic spread.

Every source page is assigned one advising topic from its URL path and title.
The YAML ``topics`` tags cannot do this: almost every page carries
``advising`` and ``degree-requirements``, so a deadline page and an advising
page look the same. URL paths are already organized by office (``/bursar/``,
``/registrar/graduation/``, ``/registrar/calendar/``) and iSchool slugs name
the page's subject, so a short ordered rule list separates them without an
API call. A YAML ``topic_cluster`` value overrides the rules for one page.

The assignment is computed from metadata at query time, so ``TOPIC_SPREAD``
works on an existing index without a rebuild. Summary chunks store their
topic in ``topic_cluster`` explicitly.

``TOPIC_SPREAD`` defaults to off. When on, and the question is broad (an
overview cue such as "what should I know", or three or more topics named),
no topic may hold more than ``TOPIC_SPREAD_MAX_PER_TOPIC`` of the top-k slots
while another topic has a candidate. Summary chunks share one bucket with
the same cap. Slots a capped topic cannot take are backfilled from the next
best candidates of other topics; if every other topic runs out, the capped
topic fills the rest, so spread never returns fewer chunks than before.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional, Sequence, TypeVar

T = TypeVar("T")

_TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_MAX_PER_TOPIC = 3
# While spreading, one source page takes at most this many top-k slots.
MAX_PER_PAGE = 2
DEFAULT_TOPIC = "overview"

# First match wins. Graduation comes before deadlines so the GAPE "Fall
# Graduation Deadlines" page stays with the other graduation pages, and
# deadlines come before admissions so "Application Deadlines" is a deadline
# page. Culminating-experience pages (289, e-portfolio, capstone) are degree
# requirements even though their slugs also say "faqs" or "eligibility".
TOPIC_RULES: tuple[tuple[str, re.Pattern], ...] = (
    (
        "graduation",
        re.compile(
            r"/graduation/|commencement|candidacy|award-graduate-degree"
            r"|gradapp|graduation",
        ),
    ),
    ("deadlines", re.compile(r"/calendar/|due-dates|deadline|dates and deadlines")),
    (
        "costs",
        re.compile(r"/bursar/|/faso/|fees|scholarship|tuition|payment|financial aid"),
    ),
    ("registration", re.compile(r"/registration/|add a class|drop a class")),
    ("careers", re.compile(r"career|professional-experience|internship")),
    (
        "requirements",
        re.compile(
            r"e-portfolio|\b289\b|capstone|required-courses|required courses"
            r"|electives|degree-units|degree units|degree-completion|course-load"
            r"|course-rotations|learning-outcomes|academics|preview_program"
        ),
    ),
    ("courses", re.compile(r"preview_course|course descriptions")),
    ("advising", re.compile(r"advising|faqs")),
    ("admissions", re.compile(r"admission|application|transfer-students|freshman")),
)

TOPICS: tuple[str, ...] = tuple(name for name, _rule in TOPIC_RULES) + (DEFAULT_TOPIC,)
# Summary chunks share one bucket, so overviews cannot fill a topic's slots
# and push out the page chunks that hold the exact wording.
SUMMARY_BUCKET = "summary"

# Topic words in a student question. A question naming three or more of
# these topics, or asking for an overview, is broad.
QUERY_TOPIC_TERMS: tuple[tuple[str, re.Pattern], ...] = (
    ("admissions", re.compile(r"\b(?:admissions?|admit\w*|apply|applying|applications?)\b")),
    ("deadlines", re.compile(r"\b(?:deadlines?|due|dates?|calendar)\b")),
    (
        "costs",
        re.compile(
            r"\b(?:fees?|tuition|costs?|pay|paying|payments?|scholarships?"
            r"|financial aid)\b"
        ),
    ),
    (
        "registration",
        re.compile(r"\b(?:register\w*|registration|enroll\w*|waitlist|add|drop)\b"),
    ),
    ("advising", re.compile(r"\b(?:advis\w*|appointments?)\b")),
    (
        "graduation",
        re.compile(r"\b(?:graduat\w*|commencement|diploma|candidacy)\b"),
    ),
    ("careers", re.compile(r"\b(?:careers?|jobs?|internships?|professional experience)\b")),
    (
        "requirements",
        re.compile(
            r"\b(?:requirements?|required|core|electives?|units?|e-portfolio"
            r"|culminating|capstone|courses?|classes)\b"
        ),
    ),
)
BROAD_QUERY_TOPICS = 3
_BROAD_CUE = re.compile(
    r"\b(?:what (?:should|do) i (?:need to )?know|need to know|overview|everything"
    r"|getting started|before (?:i )?(?:apply|applying|start|starting))\b"
)


def topic_spread_enabled() -> bool:
    """Read on each call. Unset or any value other than true/yes/on/1 is off."""
    return os.getenv("TOPIC_SPREAD", "false").strip().lower() in _TRUE_VALUES


def max_per_topic() -> int:
    """Per-topic cap on the top-k slots. Bad or missing values use 3."""
    raw = os.getenv("TOPIC_SPREAD_MAX_PER_TOPIC", "")
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = DEFAULT_MAX_PER_TOPIC
    return max(1, min(value, 20))


def page_topic(metadata: Optional[dict]) -> str:
    """Return the topic cluster for one chunk's metadata."""
    metadata = metadata or {}
    explicit = metadata.get("topic_cluster")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().casefold()
    url = metadata.get("source_url")
    title = metadata.get("title")
    haystack = " ".join(
        value.strip().casefold()
        for value in (url, title)
        if isinstance(value, str) and value.strip()
    )
    if not haystack:
        return DEFAULT_TOPIC
    for name, rule in TOPIC_RULES:
        if rule.search(haystack):
            return name
    return DEFAULT_TOPIC


def chunk_bucket(metadata: Optional[dict]) -> str:
    """Spread bucket: the page topic, or one shared bucket for summaries."""
    try:
        from .summary_chunks import is_summary_metadata
    except ImportError:  # pragma: no cover - src/ on sys.path
        from summary_chunks import is_summary_metadata
    if is_summary_metadata(metadata):
        return SUMMARY_BUCKET
    return page_topic(metadata)


def query_topics(query: str) -> set[str]:
    """Advising topics a question names."""
    text = query.casefold() if isinstance(query, str) else ""
    return {name for name, rule in QUERY_TOPIC_TERMS if rule.search(text)}


def is_broad_query(query: str) -> bool:
    """An overview request, or a question spanning three or more topics.

    "What is the fee payment deadline?" names two topics and is answered by
    one cluster of pages, so it is not spread.
    """
    if not isinstance(query, str) or not query.strip():
        return False
    if _BROAD_CUE.search(query.casefold()):
        return True
    return len(query_topics(query)) >= BROAD_QUERY_TOPICS


def chunk_page(metadata: Optional[dict]) -> str:
    """Source page for the per-page cap. Summaries have no page cap."""
    try:
        from .summary_chunks import is_summary_metadata
    except ImportError:  # pragma: no cover - src/ on sys.path
        from summary_chunks import is_summary_metadata
    if is_summary_metadata(metadata):
        return ""
    raw = (metadata or {}).get("source_url") or (metadata or {}).get("source")
    return raw.strip() if isinstance(raw, str) else ""


def spread_by_topic(
    items: Sequence[T],
    k: int,
    topic_of: Callable[[T], str],
    cap: Optional[int] = None,
    page_of: Optional[Callable[[T], str]] = None,
    page_cap: int = MAX_PER_PAGE,
) -> list[T]:
    """Reorder ``items`` so the first ``k`` respect the per-topic cap.

    With ``page_of``, one source page also takes at most ``page_cap`` slots,
    so a topic's slots go to different pages (one long FAQ page cannot fill
    the advising topic). An empty page key is not capped.

    Relative order is preserved inside the selection and inside the
    deferred tail. Items past ``k`` follow in their original order, so a
    caller that slices ``[:k]`` sees the spread selection and a caller that
    reads further (authoritative course-page pulls) still has candidates.
    """
    ordered = list(items)
    if k <= 0 or len(ordered) <= 1:
        return ordered
    cap = max_per_topic() if cap is None else max(1, int(cap))
    chosen: list[int] = []
    deferred: list[int] = []
    counts: dict[str, int] = {}
    page_counts: dict[str, int] = {}
    for index, item in enumerate(ordered):
        if len(chosen) >= k:
            deferred.append(index)
            continue
        topic = topic_of(item) or DEFAULT_TOPIC
        page = page_of(item) if page_of is not None else ""
        if counts.get(topic, 0) >= cap or (
            page and page_counts.get(page, 0) >= page_cap
        ):
            deferred.append(index)
            continue
        counts[topic] = counts.get(topic, 0) + 1
        if page:
            page_counts[page] = page_counts.get(page, 0) + 1
        chosen.append(index)
    # Backfill: fewer than k candidates fit under the caps, so the capped
    # items take the remaining slots in their original order.
    while len(chosen) < k and deferred:
        chosen.append(deferred.pop(0))
    chosen_set = set(chosen)
    head = [ordered[index] for index in sorted(chosen)]
    tail = [ordered[index] for index in range(len(ordered)) if index not in chosen_set]
    return head + tail
