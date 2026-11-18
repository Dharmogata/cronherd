# CronHerd Engineering Roadmap (200+ Hours)

This roadmap details the planned multi-phase evolution of **CronHerd** from a 24-hour crontab, GitHub Actions, and systemd timer collision simulator into a comprehensive distributed schedule orchestration, DST transition analyzer, and automated jitter patch generator.

---

## Phase 1: Core 24h Collision Simulator, Multi-Format Scanners & Jitter Advisor (Completed - v0.1.0)
- [x] Full 5-field cron expression parser supporting lists, ranges, steps, named months/days (`JAN`-`DEC`, `MON`-`SUN`), and `@hourly`/`@daily`/`@midnight`/`@weekly`/`@monthly` macros (`cron_parser.py`).
- [x] Scanners for Unix `crontab` files, GitHub Actions `.github/workflows/*.yml` (`schedule:` `- cron:`), and `systemd` `.timer` (`OnCalendar=`) units with `# duration: 10m` annotations (`scanners.py`).
- [x] 24-hour (1,440-minute) and 7-day rolling concurrency simulator with built-in rules `CH001` (Midnight Thundering Herd), `CH002` (Top-of-Hour Spike), `CH003` (High-Frequency / Self-Overlap), `CH004` (Duplicate Command), and `CH005` (Restricted DOM + DOW OR-Logic Trap) (`simulator.py`).
- [x] Deterministic SHA-256 jitter advisor (`suggest_jittered_cron`) and 24-hour ASCII density heatmap (`▁▂▃▄▅▆▇█`), JSON, and Markdown formatters (`formatters.py`, `cli.py`).

---

## Phase 2: Kubernetes CronJobs, Airflow DAGs, Celery Beat & EventBridge Scanners (~40 Hours)
- [ ] **Kubernetes `CronJob` Manifest Scanner (`k8s_scanner.py`):** Parse `batch/v1` `CronJob` YAML manifests (`spec.schedule`, `spec.concurrencyPolicy: Allow|Forbid|Replace`, `startingDeadlineSeconds`).
- [ ] **Apache Airflow & Celery Beat AST Scanner:** Extract `schedule_interval` / `schedule` from Python Airflow DAG definitions and `crontab(...)` entries in Celery `beat_schedule` dicts.
- [ ] **AWS EventBridge & Terraform CloudWatch Schedule Rules:** Parse `cron(...)` and `rate(...)` expressions from `.tf` and serverless configuration files.

---

## Phase 3: Daylight Saving Time (DST), Timezone & Calendar Edge-Case Rules (`CH006`–`CH015`) (~35 Hours)
- [ ] **Rule `CH006` (DST Spring-Forward Skipped Execution):** Detect `CRON_TZ` or `systemd` timezone-pinned jobs scheduled between `02:00` and `02:59` local time that silently skip once a year or fire twice during Fall-Back.
- [ ] **Rule `CH007` (End-of-Month `29-31` Silent Skip):** Flag schedules pinned to day `31` (`0 0 31 * *`) that silently fail to run in February, April, June, September, and November.
- [ ] **Rule `CH008` (GitHub Actions 5-Minute Minimum Interval Violation):** Flag GitHub Actions `schedule` workflows attempting intervals shorter than 5 minutes or pinned to `0 * * * *` where GitHub's internal runner queue drops or delays triggers.
- [ ] **Rule `CH009` – `CH015`:** Missing `flock -n` on overlapping crontabs, systemd `RandomizedDelaySec=` absence detection, and cross-timezone collision normalization.

---

## Phase 4: Optimal Load-Leveling Solver & Automated In-Place Codemod (`cronherd fix`) (~40 Hours)
- [ ] **Global Minimum-Peak Load Balancer (`solver.py`):** Beyond independent per-job SHA-256 hashing, implement a constraint-aware min-max solver that assigns minute/hour offsets across a set of jobs to provably minimize the 24-hour peak concurrency while respecting maintenance window constraints (`# window: 01:00-05:00`).
- [ ] **Automated Rewrite & Diff Engine (`cronherd fix --write`):** Safely rewrite `crontab`, `.github/workflows/*.yml`, and `.timer` files in place with deterministic jittered expressions while preserving comments and formatting.

---

## Phase 5: Historical Execution Telemetry & Log Duration Calibrator (~45 Hours)
- [ ] **Execution Log Ingestor (`cronherd calibrate`):** Parse `/var/log/syslog` cron entries, `journalctl -u *.timer --output=json`, and GitHub Actions workflow run durations JSON to automatically populate real p50/p95 job durations instead of relying on static `# duration: 10m` comments.
- [ ] **Resource-Weighted Concurrency Simulation:** Weight each job by CPU, memory, or database connection pool impact (`# weight: db=4, cpu=2`) to simulate resource-specific saturation curves.

---

## Phase 6: Interactive HTML Timeline, SARIF v2.1.0 & CI Pull-Request Guard (~40 Hours)
- [ ] **Interactive 24h Gantt & Flamegraph Exporter (`cronherd analyze --format html -o schedule.html`):** Generate a standalone zero-dependency HTML heatmap and swimlane timeline showing every job's firing window across 24 hours.
- [ ] **SARIF v2.1.0 Output & GitHub Action:** Emit inline PR annotations whenever a newly added cron job collides with an existing peak window.
