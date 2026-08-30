import re

from django.core.exceptions import ValidationError

PROHIBITED_TELEMETRY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"authorization\s*:",
        r"\bbearer\b",
        r"\bapi[_ -]?key\b",
        r"\bpassword\b",
        r"\bsecret\b",
        r"\bsession[_ -]?cookie\b",
        r"\bprovider[_ -]?response[_ -]?body\b",
        r"\braw[_ -]?response\b",
    )
)


def validate_safe_summary(value: str | None) -> None:
    if value and any(pattern.search(value) for pattern in PROHIBITED_TELEMETRY_PATTERNS):
        raise ValidationError("Summary contains prohibited telemetry content.")
