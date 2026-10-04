# Weekly Category Scan Automation

## Schedule

- Keep only `com.amz-selection.weekly` enabled. The old `com.multica.amz-selection.weekly` points at a retired checkout and must remain disabled/unloaded; retain its file for history.
- launchd checks daily at 08:30 local wall-clock time, with `RunAtLoad=false`.
- The runner uses `TZ=Asia/Shanghai` and explicit timezone timestamps, and wraps execution in `caffeinate -i`.
- `--scheduled` skips API calls when a verified, non-replay published generation is less than seven days old. A legacy success log or partial run cannot satisfy this condition.
- Over eight days without a verified publication is a local critical alert. Feishu delivery is **not implemented/enabled** pending explicit recipient and data-scope approval.
- Normal scheduled execution can call Sorftime. Tests and replay commands cannot.

## Publication and Locking

`refresh_selection_workflow.py` owns an OS advisory lock at `.local/weekly-workflow.lock`. The file may remain after exit, but the lock is released by the OS even on a crash. Do not use or delete old PID lock files as a concurrency mechanism.

Each batch is built independently under `.local/weekly_generations/<run_id>/`. Category failures, empty results, and unattempted categories count as missing coverage. More than 20% missing coverage blocks publication and exits 2; this is deliberately stricter than only returning an error above 50%.

After validation, one atomic replacement of `.local/current_generation.json` publishes the complete batch. Consumers use `workflow_storage.current_root()`; the root-level legacy CSVs are no longer the current weekly data after the first generation is published. Never copy a few files back manually or point the pointer at replay output.

Per-run discovery, validation and weekly reports are also retained in the root `archive/` for traceability. Partial attempts carry forward failure bookkeeping, not success dates. Manual research stays in the workspace and is overlaid when rebuilding the dashboard. Run IDs must be unique.

## Logs and Checks

Logs are retained at `logs/weekly_category_scan_<run_id>.log` and `logs/weekly_category_scan.latest.log`. launchd stdout/stderr go to the corresponding `logs/launchd.weekly_category_scan.*.log` files.

Offline checks:

```bash
python3 -m unittest discover -s tests
python3 tests/smoke_selection_workflow.py
python3 tests/audit_optimization_replays.py
AMZ_OFFLINE=1 python3 discover_sorftime_opportunities.py --dry-run --strategy category --run-id UNIQUE_DRY_RUN_ID
zsh -n automation/run_weekly_category_scan.sh
plutil -lint automation/com.amz-selection.weekly.plist
```

The historical audit requires the local September archives, saves results under `.local/optimization_audits/`, checks live-file hashes, and must not run in CI without those inputs. The fixed smoke fixture uses a system temporary directory and a fixed analysis date. Browser acceptance uses `tests/verify_dashboard_ui.cjs` with a locally installed Playwright runtime, blocks external requests, and saves screenshots under ignored `output/playwright/`.

Pool repair is a separate offline operation: `python3 repair_opportunity_archive.py` previews changes; `--apply` first saves a backup, preserves all rows and proof dates, and never clears risk holds.

## Install Or Reload

Inspect both labels before changes and verify no scan is running. Adapt checkout paths in the plist when installing on a different computer. Back up the installed plist first.

```bash
launchctl print gui/501/com.amz-selection.weekly
launchctl print gui/501/com.multica.amz-selection.weekly
launchctl disable gui/501/com.multica.amz-selection.weekly
launchctl bootout gui/501/com.multica.amz-selection.weekly
launchctl bootout gui/501/com.amz-selection.weekly
cp automation/com.amz-selection.weekly.plist ~/Library/LaunchAgents/com.amz-selection.weekly.plist
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.amz-selection.weekly.plist
launchctl enable gui/501/com.amz-selection.weekly
```

An already-unloaded label can make `bootout` fail; check status instead of ignoring other errors. Do not use `kickstart` on the weekly job merely to test installation: that initiates a real scan. `automation/run_weekly_category_scan.sh --force` is an explicit, potentially paid manual rerun.
