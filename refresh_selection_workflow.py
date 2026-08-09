#!/usr/bin/env python3
"""Refresh product discovery, archive scored opportunities, and rebuild the dashboard."""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from weekly_scan_observability import (
    build_report,
    env_int,
    maybe_post_issue_comment,
    now_iso,
    report_markdown,
    write_report_files,
)


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the weekly AMZ selection workflow and write an observability report.")
    parser.add_argument("--issue-id", default=os.environ.get("AMZ_WEEKLY_REPORT_ISSUE_ID", ""), help="Multica issue id for the run report comment.")
    parser.add_argument("--no-issue-comment", action="store_true", help="Write local reports but do not post a Multica issue comment.")
    parser.add_argument("--run-id", default=os.environ.get("AMZ_WEEKLY_RUN_ID", ""), help="Override report run id.")
    parser.add_argument("--log-path", default=os.environ.get("AMZ_WEEKLY_LOG_FILE", ""), help="Current run log path for the report.")
    parser.add_argument("--report-only", action="store_true", help="Skip Sorftime/scoring/validation and report on existing local outputs.")
    parser.add_argument("--mock-failure-step", help="Write a failure report for a named mock step, without running workflow commands.")
    parser.add_argument(
        "--zero-pool-weeks",
        type=int,
        default=env_int("AMZ_HEALTH_ZERO_POOL_WEEKS", 3),
        help="Health threshold for consecutive scan days with 0 Shape opportunity rows.",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=env_int("AMZ_HEALTH_STALE_DAYS", 8),
        help="Health threshold for stale dashboard or weekly success log age.",
    )
    return parser.parse_args()


def run(command: list[str], step_name: str) -> None:
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        command_text = " ".join(command)
        raise RuntimeError(f"{step_name} failed with exit code {exc.returncode}: {command_text}") from exc


def csv_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return max(0, sum(1 for _ in csv.DictReader(handle)))


def workflow_steps() -> list[tuple[str, list[str]]]:
    return [
        ("discover_sorftime_opportunities", ["python3", "discover_sorftime_opportunities.py", "--strategy", "category", "--score"]),
        ("category_shape_validation", ["python3", "category_shape_validation.py"]),
        ("build_dashboard", ["python3", "build_dashboard.py"]),
    ]


def required_outputs() -> list[Path]:
    return [
        ROOT / "web" / "index.html",
        ROOT / "reports" / "discovered_categories.csv",
        ROOT / "reports" / "selection_ranked.csv",
        ROOT / "data" / "category_shape_validation.csv",
        ROOT / "archive" / "shape_opportunity_library.csv",
    ]


def validate_required_outputs() -> None:
    missing = [str(path.relative_to(ROOT)) for path in required_outputs() if not path.exists() or path.stat().st_size == 0]
    if missing:
        raise RuntimeError(f"Required outputs are missing or empty: {', '.join(missing)}")


def resolve_log_path(raw_path: str) -> Path | None:
    raw_path = str(raw_path or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else ROOT / path


def write_and_post_report(args: argparse.Namespace, report: dict) -> None:
    json_path, md_path, archive_dir = write_report_files(ROOT, report)
    print(report_markdown(report))
    print(f"Weekly report JSON: {json_path}")
    print(f"Weekly report Markdown: {md_path}")
    print(f"Weekly report archive: {archive_dir}")
    if not args.no_issue_comment:
        posted = maybe_post_issue_comment(args.issue_id, report)
        if args.issue_id:
            print(f"Multica issue comment posted: {posted}")


def print_legacy_summary() -> None:
    """Keep the old stdout lines for existing log readers."""

    candidates = csv_count(ROOT / "reports" / "selection_ranked.csv")
    archived = csv_count(ROOT / "archive" / "shape_opportunity_library.csv")
    validations = csv_count(ROOT / "data" / "category_shape_validation.csv")
    seed_snapshots = sorted((ROOT / "archive" / "selection_runs").glob("*"))
    shape_snapshots = sorted((ROOT / "archive" / "category_shape_runs").glob("*"))
    latest_seed_snapshot = seed_snapshots[-1] if seed_snapshots else ""
    latest_shape_snapshot = shape_snapshots[-1] if shape_snapshots else ""

    print(f"Seed candidates: {candidates}")
    print(f"Category/form validations: {validations}")
    print(f"Shape opportunity library: {archived}")
    print(f"Latest seed snapshot: {latest_seed_snapshot}")
    print(f"Latest category/form snapshot: {latest_shape_snapshot}")
    print(f"Dashboard: {ROOT / 'web' / 'index.html'}")


def main() -> None:
    args = parse_args()
    started_at = now_iso()
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = resolve_log_path(args.log_path)
    current_step = ""

    try:
        if args.mock_failure_step:
            current_step = args.mock_failure_step
            raise RuntimeError(f"Mock failure requested for {args.mock_failure_step}")

        if not args.report_only:
            for step_name, command in workflow_steps():
                current_step = step_name
                run(command, step_name)
            current_step = "required_outputs"
            validate_required_outputs()

        status = "report_only" if args.report_only else "success"
        report = build_report(
            root=ROOT,
            run_id=run_id,
            status=status,
            started_at=started_at,
            log_path=log_path,
            zero_pool_weeks=args.zero_pool_weeks,
            stale_days=args.stale_days,
        )
        write_and_post_report(args, report)
        print_legacy_summary()
    except Exception as exc:  # noqa: BLE001 - the failure report must be written before exiting.
        report = build_report(
            root=ROOT,
            run_id=run_id,
            status="failure",
            started_at=started_at,
            log_path=log_path,
            failed_step=current_step,
            error_summary=str(exc),
            zero_pool_weeks=args.zero_pool_weeks,
            stale_days=args.stale_days,
        )
        write_and_post_report(args, report)
        print(f"Weekly selection workflow failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
