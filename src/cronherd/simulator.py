"""24-hour and 7-day schedule collision simulator and CH001-CH005 hazard detector."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from cronherd.cron_parser import (
    CronParseError,
    expand_24h_minutes,
    normalize_macro,
    parse_cron,
    suggest_jittered_cron,
)
from cronherd.models import (
    SEVERITY_ORDER,
    CollisionSlot,
    ScheduledJob,
    ScheduleHazard,
    SimulationReport,
)
from cronherd.scanners import scan_path


def _minimum_firing_interval(minutes: list[int]) -> int | None:
    """Return the minimum interval in minutes between consecutive firings in a 24h day."""
    if len(minutes) < 2:
        return None
    sorted_mins = sorted(minutes)
    gaps = [sorted_mins[i + 1] - sorted_mins[i] for i in range(len(sorted_mins) - 1)]
    return min(gaps) if gaps else None


def detect_hazards(
    jobs: list[ScheduledJob],
    reference_date: date,
    spike_threshold: int = 3,
) -> list[ScheduleHazard]:
    """Evaluate rules CH001 through CH005 across all discovered jobs."""
    hazards: list[ScheduleHazard] = []

    # Group jobs by firing pattern for CH001 and CH002
    midnight_jobs: list[ScheduledJob] = []
    top_of_hour_jobs: list[ScheduledJob] = []

    parsed_by_job_id: dict[str, tuple[list[int], str]] = {}

    for job in jobs:
        try:
            parsed = parse_cron(job.expression)
            norm = normalize_macro(job.expression)
            mins = expand_24h_minutes(job.expression, reference_date)
        except CronParseError:
            continue

        parsed_by_job_id[job.job_id] = (mins, norm)
        parts = norm.split()
        min_field, hour_field = parts[0], parts[1]

        # Midnight jobs: fire only at 00:00 (or have minute=0 and hour=0)
        if min_field in ("0", "00") and hour_field in ("0", "00"):
            midnight_jobs.append(job)
        elif min_field in ("0", "00") and len(parsed.hours) >= 2:
            # Fires at :00 across multiple hours (hourly or every N hours)
            top_of_hour_jobs.append(job)

        # CH003: High-frequency schedule (<= 5 min interval) or self-overlapping duration
        min_gap = _minimum_firing_interval(mins)
        is_self_overlap = min_gap is not None and job.estimated_duration_minutes > min_gap
        is_high_freq = min_gap is not None and min_gap <= 5

        if is_self_overlap or is_high_freq:
            suggestion = suggest_jittered_cron(job.expression, job.name)
            if is_self_overlap:
                msg = (
                    f"Job '{job.name}' runs every {min_gap}m but estimated duration is "
                    f"{job.estimated_duration_minutes}m (self-overlapping execution)."
                )
            else:
                msg = (
                    f"Job '{job.name}' triggers every {min_gap} minute(s) (`{job.expression}`), "
                    f"causing high scheduler churn."
                )
            hazards.append(
                ScheduleHazard(
                    rule_id="CH003",
                    severity="HIGH",
                    job_name=job.name,
                    expression=job.expression,
                    source_file=job.source_file,
                    line_number=job.line_number,
                    message=msg,
                    suggested_expression=suggestion,
                )
            )

        # CH005: Both day-of-month and day-of-week restricted
        if parsed.is_dom_and_dow_restricted:
            hazards.append(
                ScheduleHazard(
                    rule_id="CH005",
                    severity="LOW",
                    job_name=job.name,
                    expression=job.expression,
                    source_file=job.source_file,
                    line_number=job.line_number,
                    message=(
                        f"Job '{job.name}' restricts both day-of-month (`{parts[2]}`) and "
                        f"day-of-week (`{parts[4]}`); Vixie cron evaluates these with OR logic."
                    ),
                    suggested_expression=f"{parts[0]} {parts[1]} * {parts[3]} {parts[4]}",
                )
            )

    # CH001: Midnight thundering herd (2 or more jobs pinned to 00:00, or midnight total >= spike_threshold)
    midnight_active = [
        j for j in jobs if 0 in parsed_by_job_id.get(j.job_id, ([], ""))[0]
    ]
    if len(midnight_jobs) >= 2 or (len(midnight_jobs) >= 1 and len(midnight_active) >= spike_threshold):
        herd_size = max(len(midnight_jobs), len(midnight_active))
        for job in midnight_jobs:
            hazards.append(
                ScheduleHazard(
                    rule_id="CH001",
                    severity="CRITICAL",
                    job_name=job.name,
                    expression=job.expression,
                    source_file=job.source_file,
                    line_number=job.line_number,
                    message=(
                        f"Midnight thundering herd: {herd_size} jobs collide at 00:00 UTC "
                        f"(including '{job.name}')."
                    ),
                    suggested_expression=suggest_jittered_cron(job.expression, job.name),
                )
            )

    # CH002: Top-of-hour spike (:00 across hours)
    # Triggered when >= 2 multi-hour :00 jobs exist or any :00 minute meets spike_threshold
    max_top_of_hour_concurrency = 0
    for hour in range(1, 24):
        minute_idx = hour * 60
        count = sum(
            1 for j in jobs if minute_idx in parsed_by_job_id.get(j.job_id, ([], ""))[0]
        )
        if count > max_top_of_hour_concurrency:
            max_top_of_hour_concurrency = count

    if len(top_of_hour_jobs) >= 2 or (
        len(top_of_hour_jobs) >= 1 and max_top_of_hour_concurrency >= spike_threshold
    ):
        spike_count = max(len(top_of_hour_jobs), max_top_of_hour_concurrency)
        for job in top_of_hour_jobs:
            hazards.append(
                ScheduleHazard(
                    rule_id="CH002",
                    severity="HIGH",
                    job_name=job.name,
                    expression=job.expression,
                    source_file=job.source_file,
                    line_number=job.line_number,
                    message=(
                        f"Top-of-hour concurrency spike: {spike_count} jobs align at minute :00 "
                        f"(including '{job.name}')."
                    ),
                    suggested_expression=suggest_jittered_cron(job.expression, job.name),
                )
            )

    # CH004: Duplicate command or workflow scheduled multiple times
    jobs_by_command: dict[str, list[ScheduledJob]] = defaultdict(list)
    for job in jobs:
        normalized_cmd = " ".join(job.command.strip().split())
        if normalized_cmd:
            jobs_by_command[normalized_cmd].append(job)

    for cmd, cmd_jobs in jobs_by_command.items():
        if len(cmd_jobs) < 2:
            continue
        first = cmd_jobs[0]
        for dup in cmd_jobs[1:]:
            hazards.append(
                ScheduleHazard(
                    rule_id="CH004",
                    severity="MEDIUM",
                    job_name=dup.name,
                    expression=dup.expression,
                    source_file=dup.source_file,
                    line_number=dup.line_number,
                    message=(
                        f"Duplicate scheduled target '{cmd}' (also scheduled by "
                        f"'{first.name}' at {first.source_file}:{first.line_number})."
                    ),
                    suggested_expression=None,
                )
            )

    hazards.sort(
        key=lambda h: (
            -SEVERITY_ORDER.get(h.severity, 0),
            h.rule_id,
            h.source_file,
            h.line_number,
        )
    )
    return hazards


def simulate_24h(
    jobs: list[ScheduledJob],
    reference_date: date,
    scanned_files: int = 0,
    spike_threshold: int = 3,
) -> SimulationReport:
    """Simulate a 24-hour (1,440-minute) window for the given jobs and reference date."""
    active_by_minute: list[list[str]] = [[] for _ in range(1440)]

    for job in jobs:
        try:
            firing_minutes = expand_24h_minutes(job.expression, reference_date)
        except CronParseError:
            continue

        duration = max(1, job.estimated_duration_minutes)
        for start_min in firing_minutes:
            end_min = min(1440, start_min + duration)
            for m in range(start_min, end_min):
                if job.name not in active_by_minute[m]:
                    active_by_minute[m].append(job.name)

    minute_concurrency = [len(names) for names in active_by_minute]
    hourly_peak: list[int] = []
    for hour in range(24):
        window = minute_concurrency[hour * 60 : (hour + 1) * 60]
        hourly_peak.append(max(window) if window else 0)

    collisions: list[CollisionSlot] = []
    prev_signature: tuple[str, ...] | None = None

    for minute_idx, names in enumerate(active_by_minute):
        count = len(names)
        if count < spike_threshold:
            prev_signature = None
            continue

        signature = tuple(names)
        if signature == prev_signature:
            continue
        prev_signature = signature

        hour = minute_idx // 60
        minute = minute_idx % 60
        ts_iso = f"{reference_date.isoformat()}T{hour:02d}:{minute:02d}:00Z"
        severity = "CRITICAL" if (count >= spike_threshold + 2 or minute_idx == 0) else "HIGH"

        collisions.append(
            CollisionSlot(
                timestamp_iso=ts_iso,
                minute_of_day=minute_idx,
                concurrent_count=count,
                job_names=signature,
                severity=severity,
            )
        )

    collisions.sort(key=lambda c: (-c.concurrent_count, c.minute_of_day))
    hazards = detect_hazards(jobs, reference_date=reference_date, spike_threshold=spike_threshold)

    return SimulationReport(
        reference_date=reference_date,
        scanned_files=scanned_files,
        jobs=list(jobs),
        minute_concurrency=minute_concurrency,
        hourly_peak=hourly_peak,
        collisions=collisions,
        hazards=hazards,
        spike_threshold=spike_threshold,
    )


def simulate_7d_window(
    jobs: list[ScheduledJob],
    start_date: date,
    scanned_files: int = 0,
    spike_threshold: int = 3,
) -> list[SimulationReport]:
    """Simulate a 7-day rolling window starting at `start_date`."""
    reports: list[SimulationReport] = []
    for offset in range(7):
        day = start_date + timedelta(days=offset)
        reports.append(
            simulate_24h(
                jobs=jobs,
                reference_date=day,
                scanned_files=scanned_files,
                spike_threshold=spike_threshold,
            )
        )
    return reports


def analyze_repository(
    target_path: Path,
    reference_date: date | None = None,
    spike_threshold: int = 3,
    default_duration_minutes: int = 1,
) -> SimulationReport:
    """Scan `target_path` and run a 24-hour collision and hazard simulation."""
    ref = reference_date or date.today()
    jobs, scanned_files = scan_path(
        target_path=target_path,
        default_duration_minutes=default_duration_minutes,
    )
    return simulate_24h(
        jobs=jobs,
        reference_date=ref,
        scanned_files=scanned_files,
        spike_threshold=spike_threshold,
    )
