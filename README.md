# CronHerd

**CronHerd** is a command-line crontab, GitHub Actions, and `systemd` timer collision simulator, 24-hour density heatmap generator, and deterministic jitter advisor that detects "thundering herd" schedule pileups (`00:00` midnight spikes and `:00` top-of-hour stampedes) before they saturate your database or CI runners.

Most engineering teams add scheduled tasks (`0 0 * * *`, `@hourly`, `OnCalendar=daily`) one file at a time without seeing the aggregate 24-hour execution timeline. CronHerd scans Unix `crontab` files, `.github/workflows/*.yml`, and `systemd` `.timer` units, simulates all 1,440 minutes of the day (accounting for job durations), renders a 24-hour Unicode density heatmap (`▁▂▃▄▅▆▇█`), and computes deterministic SHA-256 jittered rewrites to spread load evenly.

---

## Who It's For

- **Platform, SRE, and DevOps engineers** eliminating `00:00 UTC` database lock contention, OOM kills, and I/O spikes caused by dozens of cron jobs firing at the exact same minute.
- **CI/CD maintainers** preventing GitHub Actions scheduled workflow queue delays at the top of every hour (`0 * * * *`).

---

## What It's Built With

- **Language:** Python 3.10+
- **Core Architecture:** Pure Python standard library (`re`, `datetime`, `hashlib`, `argparse`, `pathlib`, `json`, `dataclasses`, `unittest`) with **zero external runtime dependencies**.
- **Test Suite:** Compatible with both `pytest` and `python -m unittest`.

---

## Features

- **Multi-Format Schedule Discovery:** Scans Unix `crontab` / `*.cron` files, GitHub Actions workflows (`.github/workflows/*.yml` `schedule:` `- cron:`), and `systemd` `.timer` (`OnCalendar=`) units in one pass.
- **Duration-Aware 1,440-Minute Simulation:** Honors inline `# duration: 10m` comments or `--default-duration` to detect overlapping execution windows across jobs.
- **24-Hour ASCII Density Heatmap:** Visualizes hourly peak concurrency across `00h..23h` (`▁▂▃▄▅▆▇█`).
- **Deterministic SHA-256 Jitter Advisor (`cronherd jitter`):** Hashes each job's name to suggest a reproducible, conflict-resistant minute/hour offset off `:00` and `00:00`.
- **Built-in Schedule Hazard Rules (`CH001` – `CH005`):**
  - `CH001` (`Midnight Thundering Herd`): Multiple jobs piled up at `00:00 UTC` (`0 0 * * *`, `@daily`, `@midnight`).
  - `CH002` (`Top-of-Hour Spike`): High concurrency at minute `:00` across hourly or periodic schedules (`0 * * * *`, `0 */2 * * *`).
  - `CH003` (`High-Frequency / Self-Overlap`): Job runs every `<= 5` minutes (`*/1`, `*/2`, `*/5`) or overlaps its own estimated execution duration.
  - `CH004` (`Duplicate Scheduled Command`): Identical command or workflow target scheduled multiple times.
  - `CH005` (`Restricted DOM + DOW Trap`): Cron expression restricts both day-of-month and day-of-week (which Vixie cron evaluates with `OR` logic rather than `AND`).

```text
$ cronherd analyze . --date 2026-11-16

CronHerd 24h Collision Report (date: 2026-11-16 · scanned 3 file(s) · 6 job(s))
──────────────────────────────────────────────────────────────────────────────
24h Concurrency Heatmap (peak: 5 concurrent @ 00:00 UTC)
  Hours : 00                  12                  23
  Load  : █▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
  Peak/h:  5  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3  3
──────────────────────────────────────────────────────────────────────────────
Collision Windows (>= 3 concurrent jobs):
  ⚡ 00:00 UTC  [CRITICAL]  5 concurrent jobs: midnight-etl, midnight-billing, hourly-sync-a, hourly-sync-b, fast-poller
  ⚡ 01:00 UTC  [HIGH]      3 concurrent jobs: hourly-sync-a, hourly-sync-b, fast-poller
──────────────────────────────────────────────────────────────────────────────
Schedule Hazards & Deterministic Jitter Advice (7):
  [CH001/CRITICAL] crontab:1 (midnight-etl: `0 0 * * *`)
     └ Midnight thundering herd: 5 jobs collide at 00:00 UTC (including 'midnight-etl').
     └ Suggested jitter rewrite: `37 4 * * *`
  [CH002/HIGH] crontab:3 (hourly-sync-a: `0 * * * *`)
     └ Top-of-hour concurrency spike: 3 jobs align at minute :00 (including 'hourly-sync-a').
     └ Suggested jitter rewrite: `19 * * * *`
──────────────────────────────────────────────────────────────────────────────
Summary: 6 job(s), 24 collision window(s), 7 hazard(s)
```

---

## How to Run It

### 1. Install

```bash
git clone https://github.com/Dharmogata/cronherd.git
cd cronherd
pip install -e .
```

### 2. Analyze Schedules & Generate Deterministic Jitter

```bash
# Scan a directory or crontab file and print 24h collision heatmap
cronherd analyze .

# Pin a reference date and customize concurrency spike threshold
cronherd analyze . --date 2026-11-16 --spike-threshold 3

# Export report as JSON or GitHub-flavored Markdown
cronherd analyze . --format markdown

# Compute a deterministic SHA-256 jittered cron expression for a specific job
cronherd jitter "0 0 * * *" --name "nightly-db-backup"

# List all built-in schedule hazard rules (CH001-CH005)
cronherd rules
```

### 3. Run the Test Suite

```bash
# Using pytest
pytest -v

# Or using standard library unittest
PYTHONPATH=src python -m unittest discover -s tests -v
```

---

## Planned Work Ahead (TODO & Roadmap)

CronHerd has **200+ hours of planned engineering work** detailed in [ROADMAP.md](ROADMAP.md):

- [ ] **Phase 2 (~40h): Kubernetes `CronJob`, Airflow DAG, Celery Beat & AWS EventBridge Scanners** (extract schedules and `concurrencyPolicy` across cloud-native and Python schedulers).
- [ ] **Phase 3 (~35h): Daylight Saving Time (DST), Timezone & Calendar Edge-Case Rules (`CH006`–`CH015`)** (detect `02:00–02:59` DST skipped/duplicate runs, day `31` silent month skips, and missing `flock -n` locks).
- [ ] **Phase 4 (~40h): Optimal Load-Leveling Solver & Automated In-Place Codemod (`cronherd fix --write`)** (global min-max peak solver and comment-preserving file rewriter).
- [ ] **Phase 5 (~45h): Historical Execution Telemetry & Log Duration Calibrator (`cronherd calibrate`)** (ingest `syslog`, `journalctl`, and GitHub Actions run history to calibrate real p95 job durations).
- [ ] **Phase 6 (~40h): Interactive 24h HTML Gantt Heatmap, SARIF v2.1.0 Exporter & GitHub Action**.

See [ROADMAP.md](ROADMAP.md) for the complete specification.

---

## License

Released under the [MIT License](LICENSE).
