"""Pure-Python 5-field cron expression parser, 24h minute expander, and deterministic jitter advisor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import re


CRON_MACROS: dict[str, str] = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

MONTH_NAMES: dict[str, int] = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

DOW_NAMES: dict[str, int] = {
    "SUN": 0,
    "MON": 1,
    "TUE": 2,
    "WED": 3,
    "THU": 4,
    "FRI": 5,
    "SAT": 6,
}


class CronParseError(ValueError):
    """Raised when a cron expression is syntactically invalid or out of bounds."""


@dataclass(frozen=True)
class ParsedCron:
    """Normalized representation of a 5-field cron schedule."""

    raw_expression: str
    normalized_expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    dom_restricted: bool
    dow_restricted: bool

    @property
    def is_dom_and_dow_restricted(self) -> bool:
        """True if both day-of-month and day-of-week fields are restricted (non-wildcard)."""
        return self.dom_restricted and self.dow_restricted


def normalize_macro(expr: str) -> str:
    """Expand @hourly/@daily/etc. macros into standard 5-field cron syntax."""
    cleaned = expr.strip()
    lower = cleaned.lower()
    if lower in CRON_MACROS:
        return CRON_MACROS[lower]
    return cleaned


def _replace_named_tokens(token: str, mapping: dict[str, int]) -> str:
    """Replace 3-letter month or day-of-week abbreviations case-insensitively."""
    upper = token.upper()
    for name, value in mapping.items():
        upper = re.sub(rf"\b{name}\b", str(value), upper)
    return upper


def _parse_field(
    field_str: str,
    min_val: int,
    max_val: int,
    field_name: str,
    names: dict[str, int] | None = None,
    is_dow: bool = False,
) -> tuple[frozenset[int], bool]:
    """Parse a single cron field into a set of matching integers and a restricted flag."""
    raw = field_str.strip()
    if not raw:
        raise CronParseError(f"Empty cron field for {field_name}")

    is_restricted = raw not in ("*", "*/1")
    processed = _replace_named_tokens(raw, names) if names else raw

    values: set[int] = set()
    for part in processed.split(","):
        part = part.strip()
        if not part:
            raise CronParseError(f"Invalid empty list segment in {field_name}: '{field_str}'")

        step = 1
        if "/" in part:
            step_split = part.split("/")
            if len(step_split) != 2:
                raise CronParseError(f"Invalid step syntax in {field_name}: '{part}'")
            range_part, step_str = step_split
            if not step_str.isdigit() or int(step_str) <= 0:
                raise CronParseError(f"Step must be a positive integer in {field_name}: '{part}'")
            step = int(step_str)
        else:
            range_part = part

        if range_part == "*":
            start, end = min_val, max_val
        elif "-" in range_part:
            bounds = range_part.split("-")
            if len(bounds) != 2:
                raise CronParseError(f"Invalid range syntax in {field_name}: '{part}'")
            try:
                start = int(bounds[0])
                end = int(bounds[1])
            except ValueError as exc:
                raise CronParseError(f"Non-integer range bounds in {field_name}: '{part}'") from exc
            if is_dow and end == 7:
                # Allow 1-7 (MON-SUN) ranges in day-of-week
                pass
            if start > end:
                raise CronParseError(
                    f"Range start {start} exceeds end {end} in {field_name}: '{part}'"
                )
        else:
            try:
                val = int(range_part)
            except ValueError as exc:
                raise CronParseError(f"Invalid integer token in {field_name}: '{part}'") from exc
            if "/" in part:
                start, end = val, max_val
            else:
                start, end = val, val

        upper_limit = 7 if is_dow else max_val
        if start < min_val or end > upper_limit:
            raise CronParseError(
                f"Value out of bounds ({min_val}..{upper_limit}) in {field_name}: '{part}'"
            )

        for current in range(start, end + 1, step):
            normalized_val = 0 if (is_dow and current == 7) else current
            values.add(normalized_val)

    return frozenset(values), is_restricted


def parse_cron(expr: str) -> ParsedCron:
    """Parse a 5-field cron expression or @macro into a ParsedCron instance."""
    normalized = normalize_macro(expr)
    parts = normalized.split()
    if len(parts) != 5:
        raise CronParseError(
            f"Expected 5 cron fields (minute hour dom month dow), got {len(parts)} in '{expr}'"
        )

    minutes, _ = _parse_field(parts[0], 0, 59, "minute")
    hours, _ = _parse_field(parts[1], 0, 23, "hour")
    doms, dom_restricted = _parse_field(parts[2], 1, 31, "day_of_month")
    months, _ = _parse_field(parts[3], 1, 12, "month", names=MONTH_NAMES)
    dows, dow_restricted = _parse_field(parts[4], 0, 6, "day_of_week", names=DOW_NAMES, is_dow=True)

    return ParsedCron(
        raw_expression=expr.strip(),
        normalized_expression=" ".join(parts),
        minutes=minutes,
        hours=hours,
        days_of_month=doms,
        months=months,
        days_of_week=dows,
        dom_restricted=dom_restricted,
        dow_restricted=dow_restricted,
    )


def matches_date(parsed: ParsedCron, target_date: date) -> bool:
    """Check whether a ParsedCron fires on the given calendar date using Vixie cron semantics."""
    if target_date.month not in parsed.months:
        return False

    cron_dow = (target_date.weekday() + 1) % 7
    dom_match = target_date.day in parsed.days_of_month
    dow_match = cron_dow in parsed.days_of_week

    if parsed.dom_restricted and parsed.dow_restricted:
        # Vixie cron ORs day-of-month and day-of-week when both are restricted
        return dom_match or dow_match
    return dom_match and dow_match


def expand_24h_minutes(expr: str, reference_date: date) -> list[int]:
    """Return sorted minute offsets (0..1439) when `expr` triggers on `reference_date`."""
    parsed = parse_cron(expr)
    if not matches_date(parsed, reference_date):
        return []

    firing_minutes: list[int] = []
    for hour in sorted(parsed.hours):
        base = hour * 60
        for minute in sorted(parsed.minutes):
            firing_minutes.append(base + minute)
    return firing_minutes


def suggest_jittered_cron(expr: str, job_name: str) -> str:
    """Suggest a deterministic jittered cron expression using SHA-256 of `job_name`.

    - Shifts midnight spikes (`0 0 * * *`, `@daily`, `@midnight`) to a deterministic
      minute (1..58) and off-peak hour (1..5) or preserves non-zero hours while jittering minutes.
    - Shifts top-of-hour spikes (`0 * * * *`, `@hourly`, `0 */2 * * *`) to a deterministic
      minute in `1..58`.
    - Shifts step-based sub-hourly schedules (`*/5 * * * *`, `*/15 * * * *`) to use a
      deterministic phase offset (`offset-59/step`).
    """
    normalized = normalize_macro(expr)
    parts = normalized.split()
    if len(parts) != 5:
        # Validate to raise CronParseError on invalid inputs
        parse_cron(expr)

    # Validate syntax first
    parse_cron(normalized)

    digest_bytes = hashlib.sha256(job_name.strip().encode("utf-8")).digest()
    hash_int = int.from_bytes(digest_bytes[:4], "big")
    jitter_minute = 1 + (hash_int % 58)  # 1..58 (never 0 or 59)

    min_field, hour_field, dom_field, month_field, dow_field = parts

    # Case 1: Midnight daily / weekly / monthly spike (`0 0 ...`)
    if min_field in ("0", "00") and hour_field in ("0", "00"):
        jitter_hour = 1 + ((hash_int >> 8) % 5)  # 01:00..05:58 off-peak window
        return f"{jitter_minute} {jitter_hour} {dom_field} {month_field} {dow_field}"

    # Case 2: Top-of-hour spike (`0 * * * *`, `0 */2 * * *`, `0 9 * * *`)
    if min_field in ("0", "00"):
        return f"{jitter_minute} {hour_field} {dom_field} {month_field} {dow_field}"

    # Case 3: Step-based minute schedule starting at :00 (`*/10`, `*/15`, `*/30`)
    if min_field.startswith("*/"):
        step_str = min_field.split("/", 1)[1]
        if step_str.isdigit():
            step = int(step_str)
            if step > 1:
                offset = 1 + (hash_int % (step - 1))
                return f"{offset}-59/{step} {hour_field} {dom_field} {month_field} {dow_field}"

    # Default fallback: replace minute with deterministic jitter minute
    return f"{jitter_minute} {hour_field} {dom_field} {month_field} {dow_field}"
