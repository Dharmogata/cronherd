"""Data models and built-in hazard rules for CronHerd."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


SEVERITY_ORDER: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


@dataclass(frozen=True)
class RuleDefinition:
    """Metadata describing a built-in schedule collision or cron hazard rule."""

    rule_id: str
    slug: str
    severity: str
    title: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "slug": self.slug,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
        }


BUILTIN_RULES: tuple[RuleDefinition, ...] = (
    RuleDefinition(
        rule_id="CH001",
        slug="midnight-thundering-herd",
        severity="CRITICAL",
        title="Midnight Thundering Herd (00:00 Spike)",
        description=(
            "Multiple jobs are scheduled directly at 00:00 UTC (`0 0 * * *` or `@daily`), "
            "causing simultaneous database locks, I/O saturation, or CI runner queue exhaustion."
        ),
    ),
    RuleDefinition(
        rule_id="CH002",
        slug="top-of-hour-spike",
        severity="HIGH",
        title="Top-of-Hour Concurrency Spike (:00)",
        description=(
            "High concurrency detected at minute `:00` across hourly or periodic jobs "
            "(`0 * * * *`, `0 */2 * * *`). Use deterministic minute jitter to spread load."
        ),
    ),
    RuleDefinition(
        rule_id="CH003",
        slug="high-frequency-overlap",
        severity="HIGH",
        title="High-Frequency Schedule or Self-Overlap",
        description=(
            "Job runs every <= 5 minutes (`*/1`, `*/2`, `*/5`) or fires again before its "
            "estimated execution duration finishes, risking overlapping runs."
        ),
    ),
    RuleDefinition(
        rule_id="CH004",
        slug="duplicate-schedule-command",
        severity="MEDIUM",
        title="Duplicate Scheduled Command or Workflow",
        description=(
            "Identical command or workflow target is scheduled multiple times across "
            "one or more schedule configuration files."
        ),
    ),
    RuleDefinition(
        rule_id="CH005",
        slug="unpinned-day-of-week-and-month",
        severity="LOW",
        title="Both Day-of-Month and Day-of-Week Restricted (OR Semantics)",
        description=(
            "Cron expression restricts both field 3 (day-of-month) and field 5 (day-of-week). "
            "Standard Vixie cron evaluates these with OR logic rather than AND, surprising operators."
        ),
    ),
)

RULES_BY_ID: dict[str, RuleDefinition] = {rule.rule_id: rule for rule in BUILTIN_RULES}


@dataclass(frozen=True)
class ScheduledJob:
    """A single scheduled job discovered in crontab, GitHub Actions, or systemd timers."""

    job_id: str
    name: str
    expression: str
    source_file: str
    line_number: int
    source_type: str  # "crontab", "github-actions", "systemd-timer"
    command: str
    estimated_duration_minutes: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "expression": self.expression,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "source_type": self.source_type,
            "command": self.command,
            "estimated_duration_minutes": self.estimated_duration_minutes,
        }


@dataclass(frozen=True)
class CollisionSlot:
    """A 1-minute window during the simulated day where concurrent jobs exceed the spike threshold."""

    timestamp_iso: str
    minute_of_day: int
    concurrent_count: int
    job_names: tuple[str, ...]
    severity: str

    @property
    def time_hhmm(self) -> str:
        hour = self.minute_of_day // 60
        minute = self.minute_of_day % 60
        return f"{hour:02d}:{minute:02d}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_iso": self.timestamp_iso,
            "time_hhmm": self.time_hhmm,
            "minute_of_day": self.minute_of_day,
            "concurrent_count": self.concurrent_count,
            "job_names": list(self.job_names),
            "severity": self.severity,
        }


@dataclass(frozen=True)
class ScheduleHazard:
    """A static or simulated hazard detected in a scheduled job or group of jobs."""

    rule_id: str
    severity: str
    job_name: str
    expression: str
    source_file: str
    line_number: int
    message: str
    suggested_expression: str | None = None

    @property
    def rule_slug(self) -> str:
        rule = RULES_BY_ID.get(self.rule_id)
        return rule.slug if rule else "unknown-rule"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_slug": self.rule_slug,
            "severity": self.severity,
            "job_name": self.job_name,
            "expression": self.expression,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "message": self.message,
            "suggested_expression": self.suggested_expression,
        }


@dataclass
class SimulationReport:
    """Aggregated 24-hour collision simulation and hazard analysis report."""

    reference_date: date
    scanned_files: int
    jobs: list[ScheduledJob] = field(default_factory=list)
    minute_concurrency: list[int] = field(default_factory=lambda: [0] * 1440)
    hourly_peak: list[int] = field(default_factory=lambda: [0] * 24)
    collisions: list[CollisionSlot] = field(default_factory=list)
    hazards: list[ScheduleHazard] = field(default_factory=list)
    spike_threshold: int = 3

    @property
    def peak_concurrency(self) -> int:
        return max(self.minute_concurrency) if self.minute_concurrency else 0

    @property
    def peak_minute_of_day(self) -> int:
        if not self.minute_concurrency:
            return 0
        peak = max(self.minute_concurrency)
        return self.minute_concurrency.index(peak)

    @property
    def peak_time_hhmm(self) -> str:
        minute_idx = self.peak_minute_of_day
        return f"{minute_idx // 60:02d}:{minute_idx % 60:02d}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_date": self.reference_date.isoformat(),
            "scanned_files": self.scanned_files,
            "spike_threshold": self.spike_threshold,
            "peak_concurrency": self.peak_concurrency,
            "peak_time_hhmm": self.peak_time_hhmm,
            "hourly_peak": list(self.hourly_peak),
            "jobs": [job.to_dict() for job in self.jobs],
            "collisions": [slot.to_dict() for slot in self.collisions],
            "hazards": [hazard.to_dict() for hazard in self.hazards],
        }
