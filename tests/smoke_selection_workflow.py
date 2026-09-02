#!/usr/bin/env python3
"""Run a fixed-fixture smoke test for the shape-first weekly workflow.

The test writes only under tmp/smoke_selection_workflow/<run_id>/ and never calls
Sorftime. It validates every successful category, product-form conclusions, and
shape opportunity archiving with deterministic fixture data.
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


def shape_product(asin: str, title: str, product_category: str, sales: int, reviews: int, brand: str, date: str, price: float) -> dict:
    return {
        "asin": asin,
        "title": title,
        "product_category": product_category,
        "monthly_sales_volume": sales,
        "review_count": reviews,
        "star_rating": 4.3,
        "price": price,
        "brand": brand,
        "delivery_type": "FBA",
        "seller_origin": "China",
        "online_date": date,
    }


def run_shape_first_smoke(args: argparse.Namespace) -> int:
    run_id = args.run_id or f"smoke_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    run_dir = Path(args.output_root) / run_id
    discovery_root = run_dir / "archive" / "discovery_runs"
    discovery_run = discovery_root / "smoke_run"
    raw_dir = discovery_run / "raw_category_reports"
    validation_csv = run_dir / "data" / "category_shape_validation.csv"
    validation_md = run_dir / "reports" / "category_shape_validation.md"
    archive_dir = run_dir / "archive"
    result_json = run_dir / "smoke_result.json"

    try:
        raw_dir.mkdir(parents=True, exist_ok=False)
        write_json(discovery_run / "run_manifest.json", {"status": "success", "run_id": "smoke_run"})
        with (discovery_run / "categories.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["category_id", "name", "path", "scan_status", "category_health_score", "category_health_rank"],
            )
            writer.writeheader()
            writer.writerows(
                [
                    {"category_id": "A", "name": "Magnetic Parts Trays", "path": "Tools > Magnetic Parts Trays", "scan_status": "success", "category_health_score": 82, "category_health_rank": 1},
                    {"category_id": "B", "name": "Feather Dusters", "path": "Home > Cleaning > Feather Dusters", "scan_status": "success", "category_health_score": 65, "category_health_rank": 2},
                    {"category_id": "C", "name": "Failed", "path": "Home > Failed", "scan_status": "failed", "category_health_score": "", "category_health_rank": ""},
                ]
            )

        write_json(
            raw_dir / "A.json",
            {"data": {"top100_products": [
                shape_product("A1", "Magnetic Parts Tray", "MAGNETIC PARTS TRAY", 700, 80, "Maker A", "2026-01-10", 24),
                shape_product("A2", "Flexible Magnetic Parts Tray", "MAGNETIC PARTS TRAY", 600, 60, "Maker B", "2025-12-05", 28),
                shape_product("A3", "Magnetic Parts Holder Tray", "MAGNETIC PARTS TRAY", 450, 45, "Maker C", "2025-11-02", 26),
                shape_product("A4", "Mechanic Magnetic Parts Tray", "MAGNETIC PARTS TRAY", 300, 120, "Maker D", "2023-01-01", 22),
            ]}},
        )
        write_json(
            raw_dir / "B.json",
            {"data": {"top100_products": [
                shape_product("B1", "Duster Refills Compatible with Swiffer", "DUSTER REFILL", 1500, 100, "Maker E", "2026-01-01", 20),
                shape_product("B2", "Refills for Swiffer Duster", "DUSTER REFILL", 1200, 80, "Maker F", "2025-12-01", 21),
                shape_product("B3", "Replacement Duster Refills for Swiffer", "DUSTER REFILL", 1000, 60, "Maker G", "2025-11-01", 22),
                shape_product("B4", "Premium Telescoping Microfiber Duster", "DUSTER", 700, 70, "Maker H", "2026-01-01", 140),
                shape_product("B5", "Professional Extendable Microfiber Duster", "DUSTER", 650, 50, "Maker I", "2025-12-01", 160),
            ]}},
        )

        rules = json.loads((ROOT / "config" / "category_shape_validation_rules.json").read_text(encoding="utf-8"))
        rules.update(
            {
                "discovery_runs_root": str(discovery_root),
                "discovery_run_id": "smoke_run",
                "category_ranking": "",
                "category_ranking_limit": 0,
                "category_limit": 0,
                "seed_limit": 0,
                "allow_deep_dive_fallback": False,
                "output_csv": str(validation_csv),
                "output_report": str(validation_md),
                "archive_dir": str(archive_dir),
            }
        )
        rules_path = run_dir / "category_shape_validation_rules.smoke.json"
        write_json(rules_path, rules)
        validation = run(["python3", "category_shape_validation.py", "--rules", str(rules_path)])

        validation_rows = read_csv(validation_csv)
        shape_rows = read_csv(archive_dir / "shape_opportunity_library.csv")
        recommendations = {row.get("shape_recommendation") for row in validation_rows}
        failures: list[str] = []
        assert_equal(len({row.get("category_path") for row in validation_rows}), 2, "validated categories", failures)
        assert_equal(count(validation_rows, "shape_recommendation", "Shape opportunity"), 1, "Shape opportunity rows", failures)
        assert_equal(count(validation_rows, "shape_recommendation", "Watch shape"), 1, "Watch shape rows", failures)
        if "Reject category/form" not in recommendations:
            failures.append("Reject category/form: expected at least one")
        assert_equal(count(shape_rows, "archive_status", "active_in_latest_run"), 1, "active shape pool", failures)
        opportunity = next((row for row in validation_rows if row.get("shape_recommendation") == "Shape opportunity"), {})
        if not opportunity.get("form_reference_asins"):
            failures.append("reference ASINs: expected non-empty")

        summary = {
            "status": "pass" if not failures else "fail",
            "run_id": run_id,
            "run_dir": str(run_dir),
            "metrics": {
                "validated_categories": len({row.get("category_path") for row in validation_rows}),
                "validated_shapes": len(validation_rows),
                "shape_opportunities": count(validation_rows, "shape_recommendation", "Shape opportunity"),
                "watch_shapes": count(validation_rows, "shape_recommendation", "Watch shape"),
                "rejected_shapes": count(validation_rows, "shape_recommendation", "Reject category/form"),
                "active_shape_pool": count(shape_rows, "archive_status", "active_in_latest_run"),
            },
            "failures": failures,
            "command": validation.stdout.strip(),
        }
        write_json(result_json, summary)
        print(f"Shape-first workflow smoke: {summary['status'].upper()}")
        print(f"Run dir: {run_dir}")
        print(json.dumps(summary["metrics"], ensure_ascii=False))
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001
        write_json(result_json, {"status": "error", "run_id": run_id, "run_dir": str(run_dir), "error": str(exc)})
        print(f"Shape-first workflow smoke: ERROR\n{exc}", file=sys.stderr)
        return 1


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
    raise SystemExit(run_shape_first_smoke(parse_args()))
