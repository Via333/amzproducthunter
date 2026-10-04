#!/usr/bin/env python3
"""Offline historical acceptance audit. Retains isolated outputs for inspection."""
import csv
import hashlib
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow_storage import atomic_write_json
from category_shape_validation import build_rows_from_category_report


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def fingerprint():
    names = ["data/category_shape_validation.csv", "data/discovered_candidates.csv",
             "reports/discovered_categories.csv", "reports/weekly_scan_report.json",
             "archive/shape_opportunity_library.csv", "archive/category_scan_state.csv", "web/index.html",
             ".local/current_generation.json"]
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() if (ROOT / name).exists() else None for name in names}


def funnel(records):
    return {"rows": len(records), "recommendations": dict(Counter(r["shape_recommendation"] for r in records)),
            "classification": dict(Counter(r.get("classification_status", "") for r in records)),
            "decisive_reasons": Counter(r.get("decisive_reject_reason") or "legacy reason not recorded"
                for r in records if r["shape_recommendation"] == "Reject category/form").most_common(5)}


def changes(before, after):
    def keyed(records):
        return {(r.get("source_category_id", ""), r["product_form"]): r for r in records
                if r["shape_recommendation"] in {"Watch shape", "Shape opportunity"}}
    left, right = keyed(before), keyed(after)
    def detail(key, row):
        return {"category": key[0], "form": key[1], "status": row["shape_recommendation"],
                "flags": row.get("validation_flags", ""), "classification": row.get("classification_status", "")}
    return {"added": [detail(k, right[k]) for k in sorted(right.keys() - left.keys())],
            "removed": [detail(k, left[k]) for k in sorted(left.keys() - right.keys())]}


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = ROOT / ".local/optimization_audits" / stamp
    output.mkdir(parents=True, exist_ok=False)
    before_files = fingerprint()
    report = {"started_at": datetime.now().astimezone().isoformat(), "live_files_before": before_files}
    for source, suffix, expected_exit in [("20260921_083005", "0921", 0), ("20260928_073040", "0928", 2)]:
        run_id = f"{stamp}_audit_{suffix}"
        with (output / f"{suffix}.log").open("w") as log:
            result = subprocess.run([sys.executable, str(ROOT / "refresh_selection_workflow.py"),
                "--replay-source-run", str(ROOT / "archive/discovery_runs" / source),
                "--run-id", run_id, "--no-issue-comment"], cwd=ROOT,
                env={**os.environ, "AMZ_OFFLINE":"1"}, stdout=log, stderr=subprocess.STDOUT)
        assert result.returncode == expected_exit, f"Unexpected exit {result.returncode}; see {output}"
        manifest = json.loads((ROOT / "archive/discovery_runs" / run_id / "run_manifest.json").read_text())
        report[suffix] = {"run_id": run_id, "exit": result.returncode, "manifest": manifest}
        if suffix == "0921":
            before = rows(ROOT / "archive/category_shape_runs" / source / "category_shape_validation.csv")
            after = rows(ROOT / "archive/category_shape_runs" / run_id / "category_shape_validation.csv")
            report[suffix].update(before=funnel(before), after=funnel(after), changes=changes(before, after))
        assert fingerprint() == before_files, "Offline replay modified live outputs"

    # Inspect the 31 available reports separately; never publish this incomplete batch.
    rules = json.loads((ROOT / "config/category_shape_validation_rules.json").read_text())
    source_dir = ROOT / "archive/discovery_runs/20260928_073040"
    os.environ["AMZ_AS_OF_DATE"] = "2026-09-28"
    partial_rows = []
    for category in rows(source_dir / "categories.csv"):
        path = source_dir / "raw_category_reports" / (category["category_id"] + ".json")
        if path.exists():
            partial_rows.extend(build_rows_from_category_report({"source_category_id":category["category_id"],
                "source_category_path":category["path"]}, json.loads(path.read_text()), rules))
    report["0928"]["available_reports_only"] = funnel(partial_rows)
    report["live_files_unchanged"] = fingerprint() == before_files
    atomic_write_json(output / "audit.json", report)
    print(json.dumps({"artifact":str(output / "audit.json"), "live_files_unchanged":report["live_files_unchanged"],
                     "0921_before":report["0921"]["before"], "0921_after":report["0921"]["after"],
                     "0928":report["0928"]["available_reports_only"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
