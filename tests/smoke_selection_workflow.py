#!/usr/bin/env python3
"""Run a fixed-fixture smoke test for the AMZ selection workflow.

The test writes only under tmp/smoke_selection_workflow/<run_id>/ and never calls
Sorftime. It exercises scoring, initial opportunity archiving, shape validation,
and shape opportunity archiving with deterministic fixture data.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "selection_smoke"
FIXTURE_CANDIDATES = ROOT / "tests" / "fixtures" / "selection_smoke_candidates.csv"
DEFAULT_OUTPUT_ROOT = ROOT / "tmp" / "smoke_selection_workflow"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run offline fixture smoke test for selection and shape validation.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root directory for smoke outputs.")
    parser.add_argument("--run-id", help="Run id. Defaults to smoke_YYYYMMDD_HHMMSS_microseconds.")
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "Command failed ({code}): {cmd}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}".format(
                code=completed.returncode,
                cmd=" ".join(command),
                stdout=completed.stdout.strip(),
                stderr=completed.stderr.strip(),
            )
        )
    return completed


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def count(rows: list[dict[str, str]], key: str, value: str) -> int:
    return sum(1 for row in rows if row.get(key) == value)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_validation_rules(run_dir: Path, selection_csv: Path, validation_csv: Path, validation_md: Path, archive_dir: Path) -> Path:
    base_rules = json.loads((ROOT / "config" / "category_shape_validation_rules.json").read_text(encoding="utf-8"))
    base_rules.update(
        {
            "input": str(selection_csv),
            "deep_dive_summary": str(run_dir / "empty_deep_dive_summary.csv"),
            "allow_deep_dive_fallback": False,
            "research_root": str(run_dir / "research"),
            "output_csv": str(validation_csv),
            "output_report": str(validation_md),
            "archive_dir": str(archive_dir),
            "seed_limit": 10,
            "seed_recommendation_contains": "Watch",
        }
    )
    rules_path = run_dir / "category_shape_validation_rules.smoke.json"
    write_json(rules_path, base_rules)
    return rules_path


def assert_equal(actual: object, expected: object, label: str, failures: list[str]) -> None:
    if actual != expected:
        failures.append(f"{label}: expected {expected}, got {actual}")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"smoke_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    run_dir = Path(args.output_root) / run_id
    reports_dir = run_dir / "reports"
    data_dir = run_dir / "data"
    archive_dir = run_dir / "archive"
    research_dir = run_dir / "research"
    input_csv = run_dir / "input_candidates.csv"
    selection_csv = reports_dir / "selection_ranked.csv"
    selection_md = reports_dir / "selection_report.md"
    validation_csv = data_dir / "category_shape_validation.csv"
    validation_md = reports_dir / "category_shape_validation.md"
    result_json = run_dir / "smoke_result.json"

    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(FIXTURE_CANDIDATES, input_csv)
        shutil.copytree(FIXTURE_ROOT / "research", research_dir)

        scoring = run(
            [
                "python3",
                "product_selection.py",
                "--input",
                str(input_csv),
                "--output-csv",
                str(selection_csv),
                "--output-md",
                str(selection_md),
                "--archive-dir",
                str(archive_dir),
                "--top",
                "10",
            ]
        )

        rules_path = build_validation_rules(run_dir, selection_csv, validation_csv, validation_md, archive_dir)
        validation = run(["python3", "category_shape_validation.py", "--rules", str(rules_path)])

        selection_rows = read_csv(selection_csv)
        validation_rows = read_csv(validation_csv)
        opportunity_rows = read_csv(archive_dir / "opportunity_library.csv")
        shape_rows = read_csv(archive_dir / "shape_opportunity_library.csv")

        watch_rows = count(selection_rows, "recommendation", "Watch or collect more data")
        reject_rows = count(selection_rows, "recommendation", "Reject")
        shape_opportunities = count(validation_rows, "shape_recommendation", "Shape opportunity")
        active_shape_pool = count(shape_rows, "archive_status", "active_in_latest_run")
        active_initial_pool = count(opportunity_rows, "archive_status", "active_in_latest_run")

        failures: list[str] = []
        assert_equal(len(selection_rows), 3, "scored candidate rows", failures)
        assert_equal(watch_rows, 1, "Watch candidates", failures)
        assert_equal(reject_rows, 2, "Rejected candidates", failures)
        assert_equal(active_initial_pool, 1, "active initial opportunity archive rows", failures)
        assert_equal(len(validation_rows), 1, "shape validation rows", failures)
        assert_equal(shape_opportunities, 1, "Shape opportunity rows", failures)
        assert_equal(active_shape_pool, 1, "active shape opportunity pool rows", failures)

        summary = {
            "status": "pass" if not failures else "fail",
            "run_id": run_id,
            "run_dir": str(run_dir),
            "outputs": {
                "selection_csv": str(selection_csv),
                "selection_report": str(selection_md),
                "validation_csv": str(validation_csv),
                "validation_report": str(validation_md),
                "opportunity_library": str(archive_dir / "opportunity_library.csv"),
                "shape_opportunity_library": str(archive_dir / "shape_opportunity_library.csv"),
            },
            "metrics": {
                "scored_candidates": len(selection_rows),
                "watch_candidates": watch_rows,
                "rejected_candidates": reject_rows,
                "active_initial_pool": active_initial_pool,
                "validation_rows": len(validation_rows),
                "shape_opportunities": shape_opportunities,
                "active_shape_pool": active_shape_pool,
            },
            "failures": failures,
            "commands": {
                "product_selection": scoring.stdout.strip(),
                "category_shape_validation": validation.stdout.strip(),
            },
        }
        write_json(result_json, summary)

        print("Selection workflow smoke: {status}".format(status=summary["status"].upper()))
        print(f"Run dir: {run_dir}")
        print(
            "Scored: {scored_candidates}; Watch: {watch_candidates}; Reject: {rejected_candidates}".format(
                **summary["metrics"]
            )
        )
        print(
            "Validation rows: {validation_rows}; Shape opportunity: {shape_opportunities}; Active shape pool: {active_shape_pool}".format(
                **summary["metrics"]
            )
        )
        print(f"Result JSON: {result_json}")
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001 - write a result artifact for failed smoke runs.
        summary = {
            "status": "error",
            "run_id": run_id,
            "run_dir": str(run_dir),
            "error": str(exc),
        }
        write_json(result_json, summary)
        print(f"Selection workflow smoke: ERROR\nRun dir: {run_dir}\nResult JSON: {result_json}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
