"""Output formatters (text with 24h ASCII heatmap, JSON, and Markdown) for CronHerd."""

from __future__ import annotations

import json
from typing import Iterable

from cronherd.models import RuleDefinition, SimulationReport


HEATMAP_BLOCKS: str = "▁▂▃▄▅▆▇█"


def render_24h_heatmap_bar(hourly_peak: list[int]) -> str:
    """Render a 24-character Unicode density bar (`▁▂▃▄▅▆▇█`) across hours 00..23."""
    if not hourly_peak:
        return HEATMAP_BLOCKS[0] * 24

    peak = max(hourly_peak)
    if peak <= 0:
        return HEATMAP_BLOCKS[0] * 24

    chars: list[str] = []
    max_idx = len(HEATMAP_BLOCKS) - 1
    for count in hourly_peak[:24]:
        if count <= 0:
            chars.append(HEATMAP_BLOCKS[0])
        else:
            scaled = int(round((count / peak) * max_idx))
            idx = max(1, min(max_idx, scaled))
            chars.append(HEATMAP_BLOCKS[idx])
    return "".join(chars)


def format_report_text(report: SimulationReport) -> str:
    """Format a SimulationReport as a rich terminal report with 24h ASCII heatmap."""
    lines: list[str] = []
    header = (
        f"CronHerd 24h Collision Report "
        f"(date: {report.reference_date.isoformat()} · "
        f"scanned {report.scanned_files} file(s) · "
        f"{len(report.jobs)} job(s))"
    )
    lines.append(header)
    lines.append("─" * 78)

    bar = render_24h_heatmap_bar(report.hourly_peak)
    lines.append(
        f"24h Concurrency Heatmap (peak: {report.peak_concurrency} concurrent @ {report.peak_time_hhmm} UTC)"
    )
    lines.append("  Hours : 00                  12                  23")
    lines.append(f"  Load  : {bar}")
    counts_str = " ".join(f"{c:2d}" for c in report.hourly_peak[:24])
    lines.append(f"  Peak/h: {counts_str}")
    lines.append("─" * 78)

    if report.collisions:
        lines.append(f"Collision Windows (>= {report.spike_threshold} concurrent jobs):")
        for slot in report.collisions[:15]:
            joined_jobs = ", ".join(slot.job_names)
            lines.append(
                f"  ⚡ {slot.time_hhmm} UTC  [{slot.severity}]  "
                f"{slot.concurrent_count} concurrent jobs: {joined_jobs}"
            )
    else:
        lines.append(f"No collision windows >= {report.spike_threshold} concurrent jobs detected.")

    lines.append("─" * 78)
    if report.hazards:
        lines.append(f"Schedule Hazards & Deterministic Jitter Advice ({len(report.hazards)}):")
        for hazard in report.hazards:
            lines.append(
                f"  [{hazard.rule_id}/{hazard.severity}] {hazard.source_file}:{hazard.line_number} "
                f"({hazard.job_name}: `{hazard.expression}`)"
            )
            lines.append(f"     └ {hazard.message}")
            if hazard.suggested_expression:
                lines.append(
                    f"     └ Suggested jitter rewrite: `{hazard.suggested_expression}`"
                )
    else:
        lines.append("No schedule hazards detected.")

    lines.append("─" * 78)
    lines.append(
        f"Summary: {len(report.jobs)} job(s), "
        f"{len(report.collisions)} collision window(s), "
        f"{len(report.hazards)} hazard(s)"
    )
    return "\n".join(lines)


def format_report_json(report: SimulationReport) -> str:
    """Serialize a SimulationReport as formatted JSON."""
    payload = report.to_dict()
    payload["heatmap_bar"] = render_24h_heatmap_bar(report.hourly_peak)
    return json.dumps(payload, indent=2)


def format_report_markdown(report: SimulationReport) -> str:
    """Format a SimulationReport as GitHub-flavored Markdown for PR comments."""
    bar = render_24h_heatmap_bar(report.hourly_peak)
    lines: list[str] = [
        f"# CronHerd 24h Schedule Collision Report ({report.reference_date.isoformat()})",
        "",
        f"- **Scanned Files:** {report.scanned_files}",
        f"- **Discovered Jobs:** {len(report.jobs)}",
        f"- **Peak Concurrency:** `{report.peak_concurrency}` @ `{report.peak_time_hhmm} UTC`",
        f"- **24h Density Heatmap (`00h..23h`):** `{bar}`",
        "",
    ]

    if report.collisions:
        lines.extend(
            [
                "## Collision Windows",
                "",
                "| Time (UTC) | Severity | Concurrent Jobs | Job Names |",
                "| :--- | :--- | ---: | :--- |",
            ]
        )
        for slot in report.collisions:
            jobs_str = ", ".join(f"`{n}`" for n in slot.job_names)
            lines.append(
                f"| `{slot.time_hhmm}` | **{slot.severity}** | {slot.concurrent_count} | {jobs_str} |"
            )
        lines.append("")

    if report.hazards:
        lines.extend(
            [
                "## Schedule Hazards & Jitter Suggestions",
                "",
                "| Rule | Severity | Job | Current Schedule | Suggested Jitter | Location |",
                "| :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
        )
        for hazard in report.hazards:
            suggested = (
                f"`{hazard.suggested_expression}`" if hazard.suggested_expression else "—"
            )
            lines.append(
                f"| `{hazard.rule_id}` | **{hazard.severity}** | `{hazard.job_name}` | "
                f"`{hazard.expression}` | {suggested} | `{hazard.source_file}:{hazard.line_number}` |"
            )
        lines.append("")

    return "\n".join(lines)


def format_rules(rules: Iterable[RuleDefinition], output_format: str = "text") -> str:
    """Format built-in rules catalog in text, json, or markdown."""
    rule_list = list(rules)
    if output_format == "json":
        return json.dumps([r.to_dict() for r in rule_list], indent=2)

    if output_format == "markdown":
        lines = [
            "# CronHerd Built-in Schedule Hazard Rules",
            "",
            "| Rule ID | Slug | Severity | Title | Description |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for r in rule_list:
            lines.append(
                f"| `{r.rule_id}` | `{r.slug}` | **{r.severity}** | {r.title} | {r.description} |"
            )
        return "\n".join(lines)

    lines = ["CronHerd Built-in Schedule Hazard Rules:"]
    lines.append("─" * 78)
    for r in rule_list:
        lines.append(f"  {r.rule_id} [{r.severity}] ({r.slug}) - {r.title}")
        lines.append(f"    {r.description}")
    return "\n".join(lines)
