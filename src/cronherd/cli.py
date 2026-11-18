"""Command-line interface for CronHerd."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Sequence

from cronherd import __version__
from cronherd.cron_parser import CronParseError, suggest_jittered_cron
from cronherd.formatters import (
    format_report_json,
    format_report_markdown,
    format_report_text,
    format_rules,
)
from cronherd.models import BUILTIN_RULES
from cronherd.simulator import analyze_repository


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level argument parser for `cronherd`."""
    parser = argparse.ArgumentParser(
        prog="cronherd",
        description=(
            "CronHerd: Crontab, GitHub Actions & systemd timer collision simulator, "
            "24h heatmap generator, and deterministic jitter advisor."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"cronherd {__version__}",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    # analyze subcommand
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Scan a file or repository and simulate 24h schedule collisions and hazards.",
    )
    analyze_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="File or directory path to scan (default: current directory).",
    )
    analyze_parser.add_argument(
        "--date",
        dest="reference_date",
        default=None,
        help="Reference date in YYYY-MM-DD format (default: today).",
    )
    analyze_parser.add_argument(
        "--spike-threshold",
        type=int,
        default=3,
        help="Concurrent jobs threshold to flag a collision window (default: 3).",
    )
    analyze_parser.add_argument(
        "--default-duration",
        type=int,
        default=1,
        help="Default job execution duration in minutes when unannotated (default: 1).",
    )
    analyze_parser.add_argument(
        "--format",
        choices=("text", "json", "markdown"),
        default="text",
        help="Output format (default: text).",
    )
    analyze_parser.add_argument(
        "--fail-on-collision",
        action="store_true",
        help="Exit with code 1 if any collision windows or hazards are found.",
    )

    # jitter subcommand
    jitter_parser = subparsers.add_parser(
        "jitter",
        help="Compute a deterministic SHA-256 jittered cron schedule for a job name.",
    )
    jitter_parser.add_argument(
        "expression",
        help="5-field cron expression or @macro (e.g. '0 0 * * *' or '@hourly').",
    )
    jitter_parser.add_argument(
        "--name",
        required=True,
        help="Unique job or workflow name used to seed the deterministic hash.",
    )
    jitter_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )

    # rules subcommand
    rules_parser = subparsers.add_parser(
        "rules",
        help="List all built-in schedule collision and cron hazard rules (CH001-CH005).",
    )
    rules_parser.add_argument(
        "--format",
        choices=("text", "json", "markdown"),
        default="text",
        help="Output format (default: text).",
    )

    return parser


def _parse_iso_date(raw_date: str | None) -> date:
    if not raw_date:
        return date.today()
    try:
        return date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError(f"Invalid --date '{raw_date}': expected YYYY-MM-DD") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `cronherd` CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "rules":
        print(format_rules(BUILTIN_RULES, output_format=args.format))
        return 0

    if args.subcommand == "jitter":
        try:
            suggested = suggest_jittered_cron(args.expression, args.name)
        except CronParseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        if args.format == "json":
            print(
                json.dumps(
                    {
                        "job_name": args.name,
                        "original_expression": args.expression,
                        "suggested_expression": suggested,
                    },
                    indent=2,
                )
            )
        else:
            print(
                f"Job '{args.name}': `{args.expression}` -> `{suggested}`"
            )
        return 0

    if args.subcommand == "analyze":
        try:
            ref_date = _parse_iso_date(args.reference_date)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        target = Path(args.path)
        if not target.exists():
            print(f"error: path does not exist: {target}", file=sys.stderr)
            return 2

        report = analyze_repository(
            target_path=target,
            reference_date=ref_date,
            spike_threshold=max(1, args.spike_threshold),
            default_duration_minutes=max(1, args.default_duration),
        )

        if args.format == "json":
            print(format_report_json(report))
        elif args.format == "markdown":
            print(format_report_markdown(report))
        else:
            print(format_report_text(report))

        if args.fail_on_collision and (report.collisions or report.hazards):
            return 1
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
