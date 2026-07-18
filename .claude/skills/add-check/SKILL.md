---
name: add-check
description: >
  Add a new check to the dbx-platform toolkit, correctly wired to BOTH surfaces
  (CLI subcommand + bundle job) with an offline test. Use whenever adding or
  extending a cost/housekeeping/security/governance check, a system-table
  query, or any capability that should run both ad-hoc and as a bundle job.
---

# Adding a check

Every check in this repo is **one code path exposed two ways**: an ad-hoc CLI
subcommand, and a bundle job task that runs the *same* code via
`python_wheel_task`. A change that touches only one surface is a bug. Follow all
steps; do not stop after the CLI works.

Pick the area (`cost`, `housekeeping`, `security`, `governance`, `dashboards`,
`release`) and a `<command>` name. The example below adds a fictional
`housekeeping big-tables` check — substitute your own.

## 1. Decision logic in the area module — pure and testable

In `src/dbx_platform/<area>.py`, keep I/O and logic separate. This split is what
lets tests run offline.

- `fetch_<thing>(w: WorkspaceClient) -> list[dict]` — the only part that touches
  the SDK. No decisions here.
- `classify_<thing>(rows, ...) -> list[dict]` (or `find_<thing>`) — **pure**
  function: inputs in, findings out, no `WorkspaceClient`, no clock, no env. Pass
  `now_ms` and thresholds as arguments. This is what the test targets.
- Only if the check mutates: `apply_<thing>(w, findings) -> list[str]` returning
  human-readable applied-action lines.

## 2. CLI command in `src/dbx_platform/cli.py`

Add a `cmd_<area>_<command>(args) -> int` following the existing shape:

```python
def cmd_big_tables(args) -> int:
    s = Settings.from_env()
    apply_now = check_apply(args)            # ONLY if the check mutates
    w = get_client(args.profile)
    findings = housekeeping.classify_big_tables(
        housekeeping.fetch_big_tables(w), _now_ms(), s.some_threshold
    )
    notes = []
    if findings and not apply_now:           # ONLY if mutating
        notes.append("DRY RUN — re-run with --apply --yes to <action>.")
    emit(args, "Big tables", findings, notes)
    if apply_now:                            # ONLY if mutating
        for line in housekeeping.apply_big_tables(w, findings):
            print(f"  applied: {line}")
    return 0
```

Then register the parser in the builder, next to its siblings:

```python
x = ph.add_parser("big-tables", parents=[common, mutating],   # drop `mutating` if read-only
                  help="…")
x.set_defaults(func=cmd_big_tables)
```

## 3. SAFETY MODEL — non-negotiable

Read-only by default. If the check mutates:

- Give the parser `parents=[common, mutating]` (adds `--apply` / `--yes`) and call
  `check_apply(args)`. `check_apply` returns `False` (dry run) unless `--apply`,
  and **exits 2** if `--apply` is given without `--yes` (or
  `DBX_PLATFORM_CONFIRM=true`). Do not reimplement this gate.
- Prefer the conservative precedent: `orphaned-jobs` only *pauses*, `policy-sync`
  never *deletes* unmanaged resources. A new hard-delete needs a strong reason;
  default to the reversible action.
- The mutating branch runs only when `apply_now` is true. The dry-run path must
  never mutate.

## 4. Bundle job in `resources/<area>_jobs.yml`

Add a task so the check also runs as a bundle job. Match the existing tasks:

```yaml
        - task_key: big_tables
          environment_key: default
          python_wheel_task:
            package_name: dbx_platform
            entry_point: dbx-platform
            parameters: ["housekeeping", "big-tables"]
```

Rules:
- **Job runs are report-only** — never put `--apply` in a job's
  `parameters`. Applying is a deliberate manual/local action.
- **Schedules ship paused** — if the job gets a `schedule:` block, include
  `pause_status: PAUSED`. The cron documents intended cadence; every run is
  human-initiated (console Jobs page, agent proposal, or `databricks bundle run`).
- The environment `dependencies` entry is `../dist/*.whl` (paths in resource files
  resolve relative to `resources/`, and the wheel builds at the bundle root).
- If the task needs a warehouse, pass `"--warehouse-id", "${var.warehouse_id}"`.
- Failure email is `${var.notification_email}`.

## 5. Offline test in `tests/test_<area>.py`

Import the **pure** function and test the decision logic with fixtures from
`conftest.py` (`now_ms`, `days_ago`, `hours_ago`). Cover the boundary case.

```python
from dbx_platform.housekeeping import classify_big_tables

def test_big_table_flagged(now_ms):
    rows = [{"name": "t", "size_gb": 5000}]
    assert classify_big_tables(rows, now_ms, threshold_gb=1000)
```

The test must not construct a `WorkspaceClient`, hit the network, or need
credentials — CI runs the suite without any secrets, and it must stay that way.

## 6. Wheel-packaged data — only if you added a runtime data file

Scheduled jobs have **no repo checkout**; they only have the wheel. If the check
reads a new data directory at runtime (a new `queries/` sibling, a new
`policies/`-like dir), you must:

1. Add it under `[tool.hatch.build.targets.wheel.force-include]` in
   `pyproject.toml`.
2. Extend the "Verify packaged data" assertion in `.github/workflows/ci.yml` so CI
   proves the file ships in the wheel.

SQL under `src/dbx_platform/queries/` is already inside the package — no action
needed for that.

## 7. Verify before you're done

```bash
ruff check . && pytest              # what CI runs; must be green
databricks bundle validate -t dev   # the new job task must parse
dbx-platform <area> <command>       # dry-run report renders
```

Update the command table in `README.md` and the runbook if the check is
user-facing. A check is only complete when the CLI command, the scheduled task,
and the offline test all exist and pass.
