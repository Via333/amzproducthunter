# Weekly Category Scan Automation

This folder contains the local macOS launchd automation for the weekly AMZ category scan.

## Schedule

- Label: `com.multica.amz-selection.weekly`
- Time: every Monday at 08:30 local time
- Entry point: `automation/run_weekly_category_scan.sh`
- Workflow called by the runner: `python3 refresh_selection_workflow.py`

The runner does not scrape Amazon pages directly. It only calls the existing project workflow, which uses Sorftime CLI through the existing scripts.

## Outputs

The scheduled run writes timestamped logs to:

```text
logs/weekly_category_scan_YYYYMMDD_HHMMSS.log
logs/weekly_category_scan.latest.log
logs/launchd.weekly_category_scan.out.log
logs/launchd.weekly_category_scan.err.log
```

The runner checks that these required outputs exist after each run:

```text
web/index.html
reports/discovered_categories.csv
reports/selection_ranked.csv
data/category_shape_validation.csv
archive/shape_opportunity_library.csv
```

## Re-entry Protection

The runner has two guards:

- `logs/weekly_category_scan.lock` prevents concurrent runs.
- If the same calendar day already has a completed seed snapshot and a completed category/form snapshot, and `web/index.html` exists, the runner exits without calling Sorftime or writing a new snapshot.

Use a forced rerun only after recording the reason in a Multica Issue:

```bash
automation/run_weekly_category_scan.sh --force
AMZ_WEEKLY_FORCE=1 automation/run_weekly_category_scan.sh
```

## Manual Checks

Validate the files without triggering a Sorftime scan:

```bash
zsh -n automation/run_weekly_category_scan.sh
plutil -lint automation/com.multica.amz-selection.weekly.plist
python3 tests/smoke_selection_workflow.py
```

The smoke test writes only under `tmp/smoke_selection_workflow/<run_id>/`. It verifies fixture scoring, rejected candidates, category/form validation, and a temporary shape opportunity pool without touching live reports or the formal archive.

Check whether launchd loaded the job:

```bash
launchctl print gui/501/com.multica.amz-selection.weekly
```

Run one manual weekly scan:

```bash
automation/run_weekly_category_scan.sh
```

## Install Or Reload

```bash
cp automation/com.multica.amz-selection.weekly.plist /Users/y33/Library/LaunchAgents/com.multica.amz-selection.weekly.plist
launchctl bootout gui/501 /Users/y33/Library/LaunchAgents/com.multica.amz-selection.weekly.plist
launchctl bootstrap gui/501 /Users/y33/Library/LaunchAgents/com.multica.amz-selection.weekly.plist
launchctl enable gui/501/com.multica.amz-selection.weekly
```

`bootout` may print an error when the job was not previously loaded; that is normal before first install.
