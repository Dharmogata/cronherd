"""Comprehensive test suite for CronHerd."""

from __future__ import annotations

from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cronherd.cli import main
from cronherd.cron_parser import (
    CronParseError,
    expand_24h_minutes,
    parse_cron,
    suggest_jittered_cron,
)
from cronherd.formatters import (
    format_report_json,
    format_report_markdown,
    format_report_text,
    render_24h_heatmap_bar,
)
from cronherd.models import ScheduledJob
from cronherd.scanners import (
    parse_crontab_file,
    parse_github_workflow_file,
    parse_systemd_timer_file,
    scan_path,
    systemd_calendar_to_cron,
)
from cronherd.simulator import analyze_repository, simulate_24h, simulate_7d_window


class TestCronParser(unittest.TestCase):
    """Tests for 5-field cron expression parsing, 24h expansion, and jitter suggestions."""

    def test_parse_standard_and_step_ranges(self) -> None:
        parsed = parse_cron("10-40/10 1,5,9 1-15 JAN-MAR MON-FRI")
        self.assertEqual(parsed.minutes, frozenset({10, 20, 30, 40}))
        self.assertEqual(parsed.hours, frozenset({1, 5, 9}))
        self.assertEqual(parsed.months, frozenset({1, 2, 3}))
        self.assertEqual(parsed.days_of_week, frozenset({1, 2, 3, 4, 5}))
        self.assertTrue(parsed.is_dom_and_dow_restricted)

    def test_parse_macros(self) -> None:
        hourly = parse_cron("@hourly")
        self.assertEqual(hourly.minutes, frozenset({0}))
        self.assertEqual(len(hourly.hours), 24)

        daily = parse_cron("@daily")
        self.assertEqual(daily.minutes, frozenset({0}))
        self.assertEqual(daily.hours, frozenset({0}))

        weekly = parse_cron("@weekly")
        self.assertEqual(weekly.days_of_week, frozenset({0}))

    def test_invalid_cron_raises(self) -> None:
        with self.assertRaises(CronParseError):
            parse_cron("0 0 * *")
        with self.assertRaises(CronParseError):
            parse_cron("65 * * * *")
        with self.assertRaises(CronParseError):
            parse_cron("*/0 * * * *")
        with self.assertRaises(CronParseError):
            parse_cron("30-10 * * * *")

    def test_expand_24h_minutes_and_vixie_or_semantics(self) -> None:
        # 2026-11-16 is a Monday (cron dow = 1)
        monday = date(2026, 11, 16)
        mins = expand_24h_minutes("0 */6 * * *", monday)
        self.assertEqual(mins, [0, 360, 720, 1080])

        # Restricted DOM=13 and DOW=FRI (OR semantics: Monday 2026-11-16 is neither 13th nor Friday)
        self.assertEqual(expand_24h_minutes("0 0 13 * FRI", monday), [])
        # Friday 2026-11-20 matches DOW=FRI even though day != 13
        friday = date(2026, 11, 20)
        self.assertEqual(expand_24h_minutes("0 0 13 * FRI", friday), [0])

    def test_deterministic_jitter_advisor(self) -> None:
        s1 = suggest_jittered_cron("0 0 * * *", "nightly-db-backup")
        s2 = suggest_jittered_cron("0 0 * * *", "nightly-db-backup")
        self.assertEqual(s1, s2)
        self.assertNotEqual(s1, "0 0 * * *")

        parts = s1.split()
        self.assertNotEqual(parts[0], "0")
        self.assertIn(int(parts[1]), range(1, 6))

        hourly_jitter = suggest_jittered_cron("0 * * * *", "metrics-rollup")
        h_parts = hourly_jitter.split()
        self.assertIn(int(h_parts[0]), range(1, 59))
        self.assertEqual(h_parts[1], "*")

        step_jitter = suggest_jittered_cron("*/15 * * * *", "cache-warmer")
        self.assertTrue(step_jitter.split()[0].endswith("-59/15"))


class TestScanners(unittest.TestCase):
    """Tests for crontab, GitHub Actions workflow, and systemd timer scanners."""

    def test_parse_crontab_with_annotations_and_macros(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cron_file = Path(tmp) / "crontab"
            cron_file.write_text(
                "SHELL=/bin/bash\n"
                "# name: db-backup\n"
                "# duration: 15m\n"
                "0 0 * * * /usr/local/bin/backup.sh --full\n"
                "@hourly /usr/local/bin/sync.py # duration: 5m\n",
                encoding="utf-8",
            )
            jobs = parse_crontab_file(cron_file)
            self.assertEqual(len(jobs), 2)
            self.assertEqual(jobs[0].name, "db-backup")
            self.assertEqual(jobs[0].estimated_duration_minutes, 15)
            self.assertEqual(jobs[0].expression, "0 0 * * *")
            self.assertEqual(jobs[1].expression, "@hourly")
            self.assertEqual(jobs[1].estimated_duration_minutes, 5)

    def test_parse_github_actions_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wf_dir = Path(tmp) / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            wf_file = wf_dir / "nightly.yml"
            wf_file.write_text(
                "name: Nightly Security Audit\n"
                "on:\n"
                "  schedule:\n"
                '    - cron: "0 0 * * *" # duration: 12m\n'
                "    - cron: '0 * * * *'\n",
                encoding="utf-8",
            )
            jobs = parse_github_workflow_file(wf_file)
            self.assertEqual(len(jobs), 2)
            self.assertEqual(jobs[0].name, "Nightly Security Audit")
            self.assertEqual(jobs[0].estimated_duration_minutes, 12)
            self.assertEqual(jobs[1].name, "Nightly Security Audit#2")

    def test_parse_systemd_timer(self) -> None:
        self.assertEqual(systemd_calendar_to_cron("daily"), "0 0 * * *")
        self.assertEqual(systemd_calendar_to_cron("*-*-* *:00:00"), "0 * * * *")
        self.assertEqual(systemd_calendar_to_cron("Mon *-*-* 03:15:00"), "15 3 * * MON")

        with tempfile.TemporaryDirectory() as tmp:
            timer_file = Path(tmp) / "log-prune.timer"
            timer_file.write_text(
                "[Unit]\n"
                "Description=Prune old logs\n\n"
                "[Timer]\n"
                "# duration: 8m\n"
                "OnCalendar=*-*-* 00:00:00\n"
                "Unit=log-prune.service\n",
                encoding="utf-8",
            )
            jobs = parse_systemd_timer_file(timer_file)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].expression, "0 0 * * *")
            self.assertEqual(jobs[0].estimated_duration_minutes, 8)
            self.assertEqual(jobs[0].command, "systemd:log-prune.service")


class TestSimulatorAndHazards(unittest.TestCase):
    """Tests for 24h collision simulation and CH001-CH005 hazard detection."""

    def test_detect_all_rules_ch001_to_ch005(self) -> None:
        jobs = [
            ScheduledJob(
                job_id="1",
                name="midnight-etl",
                expression="0 0 * * *",
                source_file="crontab",
                line_number=1,
                source_type="crontab",
                command="/opt/etl.sh",
                estimated_duration_minutes=10,
            ),
            ScheduledJob(
                job_id="2",
                name="midnight-billing",
                expression="@daily",
                source_file="crontab",
                line_number=2,
                source_type="crontab",
                command="/opt/billing.sh",
                estimated_duration_minutes=10,
            ),
            ScheduledJob(
                job_id="3",
                name="hourly-sync-a",
                expression="0 * * * *",
                source_file="crontab",
                line_number=3,
                source_type="crontab",
                command="/opt/sync_a.sh",
                estimated_duration_minutes=2,
            ),
            ScheduledJob(
                job_id="4",
                name="hourly-sync-b",
                expression="0 * * * *",
                source_file="crontab",
                line_number=4,
                source_type="crontab",
                command="/opt/sync_a.sh",  # Duplicate command -> CH004
                estimated_duration_minutes=2,
            ),
            ScheduledJob(
                job_id="5",
                name="fast-poller",
                expression="*/2 * * * *",  # High frequency + duration overlap -> CH003
                source_file="crontab",
                line_number=5,
                source_type="crontab",
                command="/opt/poll.sh",
                estimated_duration_minutes=5,
            ),
            ScheduledJob(
                job_id="6",
                name="friday-13th-trap",
                expression="30 4 13 * FRI",  # DOM and DOW restricted -> CH005
                source_file="crontab",
                line_number=6,
                source_type="crontab",
                command="/opt/audit.sh",
                estimated_duration_minutes=1,
            ),
        ]

        ref_date = date(2026, 11, 16)
        report = simulate_24h(jobs, reference_date=ref_date, scanned_files=1, spike_threshold=3)

        self.assertGreaterEqual(report.peak_concurrency, 5)
        self.assertEqual(report.peak_time_hhmm, "00:00")
        self.assertTrue(len(report.collisions) >= 1)

        rule_ids = {h.rule_id for h in report.hazards}
        self.assertEqual(rule_ids, {"CH001", "CH002", "CH003", "CH004", "CH005"})

        # Check 7-day window simulation
        weekly_reports = simulate_7d_window(jobs, start_date=ref_date, scanned_files=1)
        self.assertEqual(len(weekly_reports), 7)


class TestFormattersAndCLI(unittest.TestCase):
    """Tests for ASCII heatmap rendering, formatters, and CLI commands."""

    def test_heatmap_and_report_formatters(self) -> None:
        hourly = [6, 1, 1, 1, 1, 1, 2, 2, 3, 3, 3, 3, 4, 3, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1]
        bar = render_24h_heatmap_bar(hourly)
        self.assertEqual(len(bar), 24)
        self.assertEqual(bar[0], "█")

    def test_cli_analyze_jitter_and_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crontab").write_text(
                "0 0 * * * /bin/backup_db.sh\n"
                "0 0 * * * /bin/export_reports.sh\n"
                "0 * * * * /bin/hourly_metrics.sh\n",
                encoding="utf-8",
            )

            buf = io.StringIO()
            with patch("sys.stdout", buf):
                code = main(["analyze", str(root), "--date", "2026-11-16", "--spike-threshold", "2"])
            self.assertEqual(code, 0)
            output = buf.getvalue()
            self.assertIn("CronHerd 24h Collision Report", output)
            self.assertIn("CH001", output)

            # JSON format
            buf_json = io.StringIO()
            with patch("sys.stdout", buf_json):
                code_json = main(["analyze", str(root), "--date", "2026-11-16", "--format", "json"])
            self.assertEqual(code_json, 0)
            parsed_json = json.loads(buf_json.getvalue())
            self.assertEqual(parsed_json["peak_concurrency"], 3)

            # Markdown format
            buf_md = io.StringIO()
            with patch("sys.stdout", buf_md):
                code_md = main(["analyze", str(root), "--date", "2026-11-16", "--format", "markdown"])
            self.assertEqual(code_md, 0)
            self.assertIn("# CronHerd 24h Schedule Collision Report", buf_md.getvalue())

            # Jitter subcommand
            buf_jitter = io.StringIO()
            with patch("sys.stdout", buf_jitter):
                code_jitter = main(["jitter", "0 0 * * *", "--name", "billing-rollup"])
            self.assertEqual(code_jitter, 0)
            self.assertIn("billing-rollup", buf_jitter.getvalue())

            # Rules subcommand
            buf_rules = io.StringIO()
            with patch("sys.stdout", buf_rules):
                code_rules = main(["rules"])
            self.assertEqual(code_rules, 0)
            self.assertIn("CH001", buf_rules.getvalue())
            self.assertIn("CH005", buf_rules.getvalue())


if __name__ == "__main__":
    unittest.main()
