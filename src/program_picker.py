"""Conservative UI intent detection; never interpret model text as commands."""

import re

# Match direct requests about the chat's program scope, not advising questions
# such as "How do I change my major?" or quoted/negated requests.
_PREFIX = r"(?:(?:actually|sorry|please|hey)[,!.]?\s+)*"
_REQUEST = (
    r"(?:(?:can|could|would) you\s+|i (?:want|need|would like) to\s+)?"
    r"(?:change|switch|update|select|choose)\s+(?:my|the)\s+(?:program|major)"
    r"(?:\s+(?:to\s+)?(?:isda|bs isda|mlis|both|a different (?:program|major)))?"
)
_DIFFERENT = (
    r"i(?:'m| am)\s+(?:actually\s+)?(?:in\s+)?(?:a\s+)?different\s+(?:program|major)"
)
_PICKER = r"(?:show|open|bring up)\s+(?:the\s+)?program\s+(?:picker|options|selection)"
_INTENT = re.compile(
    rf"{_PREFIX}(?:{_REQUEST}|{_DIFFERENT}|{_PICKER})(?:\s+please)?[.!?]*",
    re.IGNORECASE,
)


def wants_program_picker(message: str) -> bool:
    """Only explicit, standalone switching intent opens the local picker."""
    return bool(_INTENT.fullmatch(" ".join(message.replace("’", "'").split())))
