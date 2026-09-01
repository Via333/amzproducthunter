#!/usr/bin/env python3
"""Run or register one ASIN product research and archive the result."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "archive" / "product_research_index.csv"
INDEX_FIELDS = [
    "asin",
    "title",
    "listing_url",
    "first_researched",
    "last_researched",
    "research_count",
    "research_dir",
    "report_path",
    "web_page",
    "latest_snapshot_dir",
    "products_count",
    "forms_count",
    "reviews_count",
    "review_targets_count",
    "top_form",
    "status",
    "notes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh and archive a single product research page.")
    parser.add_argument("--asin", required=True, help="ASIN to research.")
    parser.add_argument("--domain", default=None, help="Sorftime domain id. Defaults to opportunity research config.")
    parser.add_argument("--register-existing", action="store_true", help="Do not call Sorftime; archive existing research/{ASIN}.")
    parser.add_argument("--legacy-cli", action="store_true", help="Use the retired CLI research path instead of Sorftime MCP.")
    parser.add_argument("--no-dashboard", action="store_true", help="Skip dashboard rebuild for batch research runs.")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def row_count(path: Path) -> int:
    return len(read_csv(path))


def seed_product(research_dir: Path) -> dict[str, str]:
    products = read_csv(research_dir / "top_products.csv")
    return next((row for row in products if row.get("competitor_type") == "seed"), products[0] if products else {})


def top_form(research_dir: Path) -> str:
    seed = seed_product(research_dir)
    if seed.get("product_form"):
        return seed["product_form"]
    forms = read_csv(research_dir / "product_forms.csv")
    return forms[0].get("product_form", "") if forms else ""


def copy_snapshot(asin: str, run_id: str, research_dir: Path, report_path: Path) -> Path:
    snapshot_dir = ROOT / "archive" / "product_research_runs" / asin / run_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    if research_dir.exists():
        shutil.copytree(research_dir, snapshot_dir / "research_files", dirs_exist_ok=True)
    if report_path.exists():
        shutil.copyfile(report_path, snapshot_dir / "report.md")
    return snapshot_dir


def update_index(asin: str, run_id: str) -> dict[str, str]:
    research_dir = ROOT / "research" / asin
    report_path = ROOT / "reports" / f"product_opportunity_research_{asin}.md"
    if not research_dir.exists():
        raise SystemExit(f"Research directory does not exist: {research_dir}")
    seed = seed_product(research_dir)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = read_csv(INDEX_PATH)
    existing = next((row for row in rows if row.get("asin") == asin), {})
    summary_path = research_dir / "research_summary.json"
    deep_path = research_dir / "deep_analysis.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    deep = json.loads(deep_path.read_text(encoding="utf-8")) if deep_path.exists() else {}
    snapshot_dir = copy_snapshot(asin, run_id, research_dir, report_path)
    updated = {
        **existing,
        "asin": asin,
        "title": seed.get("title") or existing.get("title", ""),
        "listing_url": seed.get("listing_url") or existing.get("listing_url", f"https://www.amazon.com/dp/{asin}"),
        "first_researched": existing.get("first_researched") or now,
        "last_researched": now,
        "research_count": str(int(float(existing.get("research_count") or 0)) + 1),
        "research_dir": f"research/{asin}",
        "report_path": f"reports/product_opportunity_research_{asin}.md",
        "web_page": f"web/research/{asin}.html",
        "latest_snapshot_dir": str(snapshot_dir.relative_to(ROOT)),
        "products_count": str(row_count(research_dir / "top_products.csv")),
        "forms_count": str(row_count(research_dir / "product_forms.csv")),
        "reviews_count": str(row_count(research_dir / "reviews.csv")),
        "review_targets_count": str(row_count(research_dir / "review_targets.csv")),
        "top_form": top_form(research_dir),
        "status": summary.get("archive_status") or deep.get("archive_status") or existing.get("status") or "researched",
        "notes": summary.get("decision_reason") or deep.get("one_line") or existing.get("notes", ""),
    }
    rows = [row for row in rows if row.get("asin") != asin]
    rows.append(updated)
    rows.sort(key=lambda row: row.get("last_researched", ""), reverse=True)
    write_csv(INDEX_PATH, rows)
    return updated


def main() -> None:
    args = parse_args()
    asin = args.asin.strip().upper()
    run_id = os.environ.get("AMZ_WEEKLY_RUN_ID") or datetime.now().strftime("%Y%m%d_%H%M%S")

    if not args.register_existing:
        script = "product_opportunity_research.py" if args.legacy_cli else "mcp_product_opportunity_research.py"
        command = ["python3", script, "--asin", asin]
        if args.domain and args.legacy_cli:
            command.extend(["--domain", args.domain])
        run(command)

    updated = update_index(asin, run_id)
    if not args.no_dashboard:
        run(["python3", "build_product_research_pages.py"])
        run(["python3", "build_dashboard.py"])

    print(f"Archived product research: {asin}")
    print(f"Research page: {updated['web_page']}")
    print(f"Research index: {INDEX_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
