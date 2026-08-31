#!/usr/bin/env python3
"""Research a small, ranked shortlist before category/form validation."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from product_risk import has_valid_category


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run minimum-category Top100 research for ranked candidates.")
    parser.add_argument("--input", default="reports/selection_ranked.csv")
    parser.add_argument("--rules", default="config/workflow_rules.json")
    parser.add_argument("--run-id", default=os.environ.get("AMZ_WEEKLY_RUN_ID", ""))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def to_float(value: object) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except ValueError:
        return 0.0


def select_shortlist(rows: list[dict[str, str]], rules: dict) -> list[dict[str, str]]:
    allowed = set(rules.get("recommendations") or [])
    minimum_score = to_float(rules.get("minimum_score"))
    selected = []
    seen = set()
    for row in sorted(rows, key=lambda item: to_float(item.get("opportunity_score")), reverse=True):
        asin = str(row.get("source_asin") or "").strip().upper()
        if not asin or asin in seen:
            continue
        if allowed and row.get("recommendation") not in allowed:
            continue
        if to_float(row.get("opportunity_score")) < minimum_score:
            continue
        if str(row.get("hard_stop_reason") or "").strip():
            continue
        if to_float(row.get("compliance_risk")) > to_float(rules.get("max_compliance_risk", 100)):
            continue
        if to_float(row.get("fragile_risk")) > to_float(rules.get("max_fragile_risk", 100)):
            continue
        if to_float(row.get("oversize_risk")) > to_float(rules.get("max_oversize_risk", 100)):
            continue
        if rules.get("require_valid_category", False) and not has_valid_category(row.get("category", "")):
            continue
        seen.add(asin)
        selected.append(row)
    return selected[: int(rules.get("max_candidates", 4))]


def cached_research_is_fresh(asin: str, rules: dict) -> bool:
    products_path = ROOT / "research" / asin / "top_products.csv"
    if not products_path.exists():
        return False
    if len(read_csv(products_path)) < int(rules.get("minimum_cached_products", 50)):
        return False
    age_days = max(0.0, (datetime.now().timestamp() - products_path.stat().st_mtime) / 86400)
    return age_days <= float(rules.get("freshness_days", 30))


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    workflow_rules = json.loads((ROOT / args.rules).read_text(encoding="utf-8"))
    rules = workflow_rules.get("auto_top100_research", {})
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = ROOT / "archive" / "auto_research_runs" / run_id / "run_manifest.json"
    shortlist = select_shortlist(read_csv(ROOT / args.input), rules) if rules.get("enabled", True) else []
    manifest = {
        "run_id": run_id,
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": "",
        "selected_count": len(shortlist),
        "researched_count": 0,
        "cached_count": 0,
        "failed_count": 0,
        "items": [],
    }
    write_manifest(manifest_path, manifest)

    for row in shortlist:
        asin = str(row.get("source_asin") or "").strip().upper()
        item = {"asin": asin, "score": row.get("opportunity_score", ""), "status": ""}
        if not args.force and cached_research_is_fresh(asin, rules):
            item["status"] = "fresh_cache"
            manifest["cached_count"] += 1
        elif args.dry_run:
            item["status"] = "would_research"
        else:
            command = ["python3", "refresh_product_research.py", "--asin", asin, "--no-dashboard"]
            completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
            if completed.returncode == 0:
                item["status"] = "researched"
                manifest["researched_count"] += 1
            else:
                item["status"] = "failed"
                item["error"] = (completed.stderr or completed.stdout).strip().splitlines()[-1][:500]
                manifest["failed_count"] += 1
                if not rules.get("continue_on_error", True):
                    manifest["items"].append(item)
                    write_manifest(manifest_path, manifest)
                    raise SystemExit(item["error"])
        manifest["items"].append(item)
        write_manifest(manifest_path, manifest)

    manifest["status"] = "dry_run" if args.dry_run else ("partial" if manifest["failed_count"] else "success")
    manifest["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_manifest(manifest_path, manifest)
    print(
        "Auto Top100 shortlist: {selected} selected; {researched} researched; {cached} cached; {failed} failed".format(
            selected=manifest["selected_count"],
            researched=manifest["researched_count"],
            cached=manifest["cached_count"],
            failed=manifest["failed_count"],
        )
    )
    print(f"Manifest: {manifest_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
