from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Union


def parse_ts(value: Any) -> datetime:
    """
    Parse a timestamp-like value into a timezone-aware datetime (UTC).

    Accepts:
    - datetime (naive or aware)
    - ISO 8601 strings, including those ending with 'Z'
    - Unix epoch seconds (int/float)

    Returns:
        datetime: timezone-aware datetime in UTC

    Raises:
        ValueError: if the value cannot be parsed into a datetime
        TypeError: if the type is unsupported
    """
    # datetime input
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            # Assume UTC for naive datetimes
            return dt.replace(tzinfo=timezone.utc)
        # Normalize to UTC
        return dt.astimezone(timezone.utc)

    # Numeric epoch seconds
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError) as exc:
            raise ValueError(f"Invalid epoch timestamp: {value}") from exc

    # String-like input
    if isinstance(value, str):
        s = value.strip()

        # Common normalization: trailing Z to +00:00
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"

        # Allow space separator between date and time by replacing with 'T'
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)

        # Try ISO 8601 parsing
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                # Assume UTC if no timezone info
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except ValueError:
            # Fallthrough to alternative heuristics if needed in future
            pass

        # If we reach here, string couldn't be parsed
        raise ValueError(f"Unrecognized datetime string: {value!r}")

    raise TypeError(f"Unsupported timestamp type: {type(value).__name__}")
