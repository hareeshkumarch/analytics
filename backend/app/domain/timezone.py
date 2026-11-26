from functools import lru_cache
from zoneinfo import available_timezones

from app.domain.errors import ValidationError

DEFAULT_BUSINESS_TIMEZONE = "UTC"


@lru_cache(maxsize=1)
def _known() -> frozenset[str]:
    return frozenset(available_timezones()) | {DEFAULT_BUSINESS_TIMEZONE}


def normalize_timezone(value: str | None) -> str:
    name = (value or "").strip() or DEFAULT_BUSINESS_TIMEZONE
    if name not in _known():
        raise ValidationError(
            f"Unknown time zone {name!r}.",
            remediation="Use an IANA name such as UTC, Asia/Kolkata or America/New_York.",
            failed_fields=("business_timezone",),
        )
    return name
