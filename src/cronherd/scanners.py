"""Scanners for Unix crontab files, GitHub Actions workflows, and systemd .timer units."""

from __future__ import annotations

from pathlib import Path
import re

from cronherd.cron_parser import CRON_MACROS, CronParseError, parse_cron
from cronherd.models import ScheduledJob


IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)

DURATION_ANNOTATION_RE = re.compile(
    r"#\s*(?:cronherd:\s*)?duration\s*[:=]\s*(\d+)\s*(m|min|mins|minute|minutes|h|hr|hours)?\b",
    re.IGNORECASE,
)
NAME_ANNOTATION_RE = re.compile(
    r"#\s*(?:cronherd:\s*)?(?:name|job)\s*[:=]\s*([A-Za-z0-9_.:-]+)",
    re.IGNORECASE,
)
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
GHA_WORKFLOW_NAME_RE = re.compile(r"^\s*name\s*:\s*(.+?)\s*$")
GHA_CRON_LINE_RE = re.compile(
    r"""^\s*-\s*cron\s*:\s*(?:"([^"]+)"|'([^']+)'|([^\s#]+))(?:\s*(#.*))?$"""
)


def _extract_duration_minutes(text: str, default_minutes: int) -> int:
    """Parse `# duration: 10m` or `# duration: 2h` from comment text."""
    match = DURATION_ANNOTATION_RE.search(text)
    if not match:
        return default_minutes
    value = int(match.group(1))
    unit = (match.group(2) or "m").lower()
    if unit.startswith("h"):
        return max(1, value * 60)
    return max(1, value)


def _extract_name_annotation(text: str) -> str | None:
    """Parse `# name: db-backup` from comment text if present."""
    match = NAME_ANNOTATION_RE.search(text)
    return match.group(1).strip() if match else None


def _derive_job_name_from_command(command: str, fallback: str) -> str:
    """Derive a concise human-readable job identifier from a shell command string."""
    cleaned = command.strip()
    if not cleaned:
        return fallback
    # Strip leading env vars or common interpreters
    tokens = cleaned.split()
    skip_prefixes = {
        "sudo",
        "env",
        "/bin/sh",
        "/bin/bash",
        "sh",
        "bash",
        "-c",
        "python",
        "python3",
        "flock",
        "-n",
    }
    filtered: list[str] = []
    for tok in tokens:
        if "=" in tok and not tok.startswith("-"):
            continue
        if tok in skip_prefixes:
            continue
        if tok.startswith("-") and len(filtered) == 0:
            continue
        filtered.append(tok.strip("\"'"))
        if len(filtered) >= 2:
            break
    if not filtered:
        return fallback
    primary = Path(filtered[0]).name or filtered[0]
    if len(filtered) > 1 and not filtered[1].startswith(">"):
        secondary = Path(filtered[1]).name
        return f"{primary}:{secondary}"
    return primary


def parse_crontab_file(
    file_path: Path,
    default_duration_minutes: int = 1,
) -> list[ScheduledJob]:
    """Parse a Unix crontab file into ScheduledJob instances."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError:
        return []

    jobs: list[ScheduledJob] = []
    pending_duration: int | None = None
    pending_name: str | None = None

    for idx, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            pending_duration = None
            pending_name = None
            continue

        if stripped.startswith("#"):
            if DURATION_ANNOTATION_RE.search(stripped):
                pending_duration = _extract_duration_minutes(stripped, default_duration_minutes)
            name_hint = _extract_name_annotation(stripped)
            if name_hint:
                pending_name = name_hint
            continue

        if ENV_ASSIGNMENT_RE.match(stripped):
            continue

        # Separate inline trailing comment if present
        inline_comment = ""
        line_body = stripped
        if " #" in stripped:
            line_body, inline_comment = stripped.split(" #", 1)
            inline_comment = "#" + inline_comment
            line_body = line_body.strip()

        duration = pending_duration or default_duration_minutes
        if inline_comment:
            duration = _extract_duration_minutes(inline_comment, duration)
        explicit_name = _extract_name_annotation(inline_comment) or pending_name

        pending_duration = None
        pending_name = None

        tokens = line_body.split()
        if not tokens:
            continue

        expr: str | None = None
        command_tokens: list[str] = []

        if tokens[0].startswith("@"):
            macro = tokens[0].lower()
            if macro in CRON_MACROS and len(tokens) >= 2:
                expr = tokens[0]
                # Check if tokens[1] is a system crontab username followed by command
                if len(tokens) >= 3 and tokens[1] in ("root", "ubuntu", "cron", "www-data", "postgres"):
                    command_tokens = tokens[2:]
                else:
                    command_tokens = tokens[1:]
        elif len(tokens) >= 6:
            candidate_expr = " ".join(tokens[:5])
            try:
                parse_cron(candidate_expr)
                expr = candidate_expr
                # Support /etc/crontab format with user column in field 6
                if len(tokens) >= 7 and re.fullmatch(r"[a-z_][a-z0-9_-]*", tokens[5]):
                    if tokens[5] in ("root", "ubuntu", "cron", "www-data", "postgres", "nobody", "daemon"):
                        command_tokens = tokens[6:]
                    else:
                        command_tokens = tokens[5:]
                else:
                    command_tokens = tokens[5:]
            except CronParseError:
                continue

        if not expr or not command_tokens:
            continue

        command_str = " ".join(command_tokens).strip()
        fallback_name = f"{file_path.name}:L{idx}"
        job_name = explicit_name or _derive_job_name_from_command(command_str, fallback_name)
        job_id = f"crontab:{file_path.as_posix()}:{idx}"

        jobs.append(
            ScheduledJob(
                job_id=job_id,
                name=job_name,
                expression=expr,
                source_file=str(file_path),
                line_number=idx,
                source_type="crontab",
                command=command_str,
                estimated_duration_minutes=duration,
            )
        )

    return jobs


def parse_github_workflow_file(
    file_path: Path,
    default_duration_minutes: int = 1,
) -> list[ScheduledJob]:
    """Parse GitHub Actions `.yml`/`.yaml` workflow file for `schedule:` `- cron:` entries."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError:
        return []

    lines = content.splitlines()
    workflow_name = file_path.stem
    for raw_line in lines[:30]:
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            continue
        if not raw_line.startswith(" ") and not raw_line.startswith("\t"):
            m = GHA_WORKFLOW_NAME_RE.match(raw_line)
            if m:
                raw_name = m.group(1).strip().strip("\"'")
                if raw_name:
                    workflow_name = raw_name
                break

    jobs: list[ScheduledJob] = []
    pending_duration: int | None = None
    pending_name: str | None = None
    occurrence = 0

    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            if DURATION_ANNOTATION_RE.search(stripped):
                pending_duration = _extract_duration_minutes(stripped, default_duration_minutes)
            name_hint = _extract_name_annotation(stripped)
            if name_hint:
                pending_name = name_hint
            continue

        match = GHA_CRON_LINE_RE.match(raw_line)
        if not match:
            continue

        cron_expr = (match.group(1) or match.group(2) or match.group(3) or "").strip()
        inline_comment = match.group(4) or ""
        try:
            parse_cron(cron_expr)
        except CronParseError:
            continue

        occurrence += 1
        duration = pending_duration or default_duration_minutes
        if inline_comment:
            duration = _extract_duration_minutes(inline_comment, duration)
        explicit_name = _extract_name_annotation(inline_comment) or pending_name

        pending_duration = None
        pending_name = None

        job_name = explicit_name or (
            workflow_name if occurrence == 1 else f"{workflow_name}#{occurrence}"
        )
        job_id = f"gha:{file_path.as_posix()}:{idx}"

        jobs.append(
            ScheduledJob(
                job_id=job_id,
                name=job_name,
                expression=cron_expr,
                source_file=str(file_path),
                line_number=idx,
                source_type="github-actions",
                command=f"workflow:{workflow_name}",
                estimated_duration_minutes=duration,
            )
        )

    return jobs


def systemd_calendar_to_cron(calendar_spec: str) -> str:
    """Convert common systemd `OnCalendar=` specifications into 5-field cron syntax."""
    spec = calendar_spec.strip()
    lower = spec.lower()

    shorthands = {
        "minutely": "* * * * *",
        "hourly": "0 * * * *",
        "daily": "0 0 * * *",
        "midnight": "0 0 * * *",
        "weekly": "0 0 * * 1",
        "monthly": "0 0 1 * *",
        "yearly": "0 0 1 1 *",
        "annually": "0 0 1 1 *",
        "quarterly": "0 0 1 1,4,7,10 *",
        "semiannually": "0 0 1 1,7 *",
    }
    if lower in shorthands:
        return shorthands[lower]

    # Parse optional leading day-of-week token (e.g. "Mon *-*-* 00:00:00" or "Mon..Fri *-*-* 09:00:00")
    tokens = spec.split()
    dow_cron = "*"
    if len(tokens) == 3:
        dow_raw, date_part, time_part = tokens
        dow_upper = dow_raw.upper().replace("..", "-")
        dow_cron = dow_upper
    elif len(tokens) == 2:
        first, second = tokens
        if ":" in second:
            date_part, time_part = first, second
        else:
            raise CronParseError(f"Unsupported OnCalendar specification: '{calendar_spec}'")
    elif len(tokens) == 1 and ":" in tokens[0]:
        date_part = "*-*-*"
        time_part = tokens[0]
    else:
        raise CronParseError(f"Unsupported OnCalendar specification: '{calendar_spec}'")

    # Parse date_part: YYYY-MM-DD or *-MM-DD or *-*-*
    date_segments = date_part.split("-")
    if len(date_segments) == 3:
        _, month_seg, dom_seg = date_segments
    elif len(date_segments) == 2:
        month_seg, dom_seg = date_segments
    else:
        raise CronParseError(f"Invalid date segment in OnCalendar: '{calendar_spec}'")

    def _norm_cal_field(seg: str) -> str:
        s = seg.strip().replace("..", "-")
        if s == "*":
            return "*"
        # Convert 00/5 or 0/15 to */5 or */15
        if "/" in s:
            base, step = s.split("/", 1)
            if base in ("*", "0", "00"):
                return f"*/{int(step)}"
            return f"{int(base)}/{int(step)}"
        if "," in s:
            return ",".join(str(int(x)) if x.isdigit() else x for x in s.split(","))
        if s.isdigit():
            return str(int(s))
        return s

    month_cron = _norm_cal_field(month_seg)
    dom_cron = _norm_cal_field(dom_seg)

    # Parse time_part: HH:MM[:SS]
    time_segments = time_part.split(":")
    if len(time_segments) < 2:
        raise CronParseError(f"Invalid time segment in OnCalendar: '{calendar_spec}'")
    hour_seg, min_seg = time_segments[0], time_segments[1]
    hour_cron = _norm_cal_field(hour_seg)
    min_cron = _norm_cal_field(min_seg)

    cron_candidate = f"{min_cron} {hour_cron} {dom_cron} {month_cron} {dow_cron}"
    parse_cron(cron_candidate)
    return cron_candidate


def parse_systemd_timer_file(
    file_path: Path,
    default_duration_minutes: int = 1,
) -> list[ScheduledJob]:
    """Parse a systemd `.timer` unit file for `OnCalendar=` directives."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError:
        return []

    lines = content.splitlines()
    unit_target = f"{file_path.stem}.service"
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.lower().startswith("unit="):
            val = stripped.split("=", 1)[1].strip()
            if val:
                unit_target = val

    jobs: list[ScheduledJob] = []
    pending_duration: int | None = None
    pending_name: str | None = None
    occurrence = 0

    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith(";"):
            if DURATION_ANNOTATION_RE.search(stripped):
                pending_duration = _extract_duration_minutes(stripped, default_duration_minutes)
            name_hint = _extract_name_annotation(stripped)
            if name_hint:
                pending_name = name_hint
            continue

        if not stripped.lower().startswith("oncalendar="):
            continue

        raw_val = stripped.split("=", 1)[1].strip()
        inline_comment = ""
        if " #" in raw_val:
            raw_val, inline_comment = raw_val.split(" #", 1)
            inline_comment = "#" + inline_comment
            raw_val = raw_val.strip()

        if not raw_val:
            continue

        try:
            cron_expr = systemd_calendar_to_cron(raw_val)
        except CronParseError:
            continue

        occurrence += 1
        duration = pending_duration or default_duration_minutes
        if inline_comment:
            duration = _extract_duration_minutes(inline_comment, duration)
        explicit_name = _extract_name_annotation(inline_comment) or pending_name
        pending_duration = None
        pending_name = None

        base_name = file_path.stem
        job_name = explicit_name or (base_name if occurrence == 1 else f"{base_name}#{occurrence}")
        job_id = f"systemd:{file_path.as_posix()}:{idx}"

        jobs.append(
            ScheduledJob(
                job_id=job_id,
                name=job_name,
                expression=cron_expr,
                source_file=str(file_path),
                line_number=idx,
                source_type="systemd-timer",
                command=f"systemd:{unit_target}",
                estimated_duration_minutes=duration,
            )
        )

    return jobs


def _classify_schedule_file(file_path: Path) -> str | None:
    """Return 'crontab', 'github-actions', 'systemd-timer', or None for a candidate file."""
    name_lower = file_path.name.lower()
    suffix_lower = file_path.suffix.lower()

    if suffix_lower == ".timer":
        return "systemd-timer"

    if suffix_lower in (".yml", ".yaml"):
        parts_lower = [p.lower() for p in file_path.parts]
        if "workflows" in parts_lower or ".github" in parts_lower:
            return "github-actions"
        # Also allow explicitly passed YAML files that contain `schedule:` and `cron:`
        try:
            sample = file_path.read_text(encoding="utf-8", errors="ignore")
            if "schedule:" in sample and "cron:" in sample:
                return "github-actions"
        except OSError:
            pass
        return None

    if (
        name_lower in ("crontab", "cron", "cronfile")
        or suffix_lower in (".cron", ".crontab")
        or name_lower.startswith("crontab.")
        or "cron.d" in [p.lower() for p in file_path.parts]
    ):
        return "crontab"

    return None


def scan_path(
    target_path: Path,
    default_duration_minutes: int = 1,
) -> tuple[list[ScheduledJob], int]:
    """Scan a file or directory recursively for scheduled jobs.

    Returns (discovered_jobs, scanned_file_count).
    """
    if not target_path.exists():
        raise FileNotFoundError(f"Path does not exist: {target_path}")

    if target_path.is_file():
        kind = _classify_schedule_file(target_path)
        if kind == "github-actions":
            return parse_github_workflow_file(target_path, default_duration_minutes), 1
        if kind == "systemd-timer":
            return parse_systemd_timer_file(target_path, default_duration_minutes), 1
        # Default to crontab parser when an explicit file is passed
        return parse_crontab_file(target_path, default_duration_minutes), 1

    all_jobs: list[ScheduledJob] = []
    scanned_files = 0

    candidate_files: list[Path] = []
    for root_or_file in sorted(target_path.rglob("*")):
        if not root_or_file.is_file():
            continue
        rel_parts = root_or_file.relative_to(target_path).parts
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        candidate_files.append(root_or_file)

    for candidate in candidate_files:
        kind = _classify_schedule_file(candidate)
        if kind is None:
            continue
        scanned_files += 1
        if kind == "crontab":
            all_jobs.extend(parse_crontab_file(candidate, default_duration_minutes))
        elif kind == "github-actions":
            all_jobs.extend(parse_github_workflow_file(candidate, default_duration_minutes))
        elif kind == "systemd-timer":
            all_jobs.extend(parse_systemd_timer_file(candidate, default_duration_minutes))

    return all_jobs, scanned_files
