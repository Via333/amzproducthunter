#!/usr/bin/env python3
"""Search Sorftime by keyword, score related listings, and archive every run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from discover_sorftime_opportunities import product_passes_filters
from import_sorftime_candidates import (
    OUTPUT_FIELDS as CANDIDATE_FIELDS,
    build_candidate,
    call_sorftime,
    dedupe_candidates,
    find_product_records,
    load_json,
    write_candidates,
)
from product_selection import OUTPUT_FIELDS as SCORE_FIELDS


ROOT = Path(__file__).resolve().parent
INDEX_FIELDS = [
    "run_id",
    "keyword",
    "searched_at",
    "raw_result_count",
    "eligible_candidate_count",
    "go_count",
    "watch_count",
    "reject_count",
    "top_score",
    "top_asin",
    "top_title",
    "run_dir",
    "ranked_csv",
    "report_md",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search Amazon US opportunities by keyword through Sorftime.")
    parser.add_argument("--keyword", required=True, help="Keyword to search, for example: bread storage bag.")
    parser.add_argument("--rules", default="config/keyword_search_rules.json", help="Keyword search rules JSON.")
    parser.add_argument("--defaults", default="config/import_defaults.json", help="Candidate import defaults JSON.")
    parser.add_argument("--discovery-rules", default="config/autodiscovery_rules.json", help="Shared product filters JSON.")
    parser.add_argument("--pages", type=int, help="Override number of Sorftime result pages.")
    parser.add_argument("--from-json", action="append", default=[], help="Use saved response JSON instead of Sorftime; repeat per page.")
    parser.add_argument(
        "--archive-root",
        default="archive/keyword_search_runs",
        help="Root directory for keyword run snapshots.",
    )
    parser.add_argument("--index", default="archive/keyword_search_index.csv", help="Keyword search history index CSV.")
    parser.add_argument("--no-dashboard", action="store_true", help="Do not rebuild web/index.html after archiving.")
    return parser.parse_args()


def slugify(keyword: str) -> str:
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    if ascii_slug:
        return ascii_slug[:64]
    digest = hashlib.sha1(keyword.encode("utf-8")).hexdigest()[:10]
    return f"keyword-{digest}"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def stored_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def search_responses(args: argparse.Namespace, rules: dict) -> list[dict]:
    if args.from_json:
        return [load_json(path) for path in args.from_json]

    pages = args.pages or int(rules.get("pages", 5))
    page_size = int(rules.get("page_size", 20))
    responses = []
    for page in range(1, pages + 1):
        payload = {
            "keyword": args.keyword.strip(),
            "pageIndex": page,
            "pageSize": page_size,
        }
        response = call_sorftime(
            rules.get("method", "KeywordSearchResults"),
            json.dumps(payload, ensure_ascii=False),
            rules.get("domain", "1"),
        )
        responses.append(response)
        record_count = len(find_product_records(response))
        print(f"Keyword page {page}: {record_count} related products")
        if record_count == 0:
            break
    return responses


def score_candidates(input_csv: Path, ranked_csv: Path, report_md: Path, top_n: int) -> None:
    subprocess.run(
        [
            "python3",
            "product_selection.py",
            "--input",
            str(input_csv),
            "--output-csv",
            str(ranked_csv),
            "--output-md",
            str(report_md),
            "--top",
            str(top_n),
            "--no-archive",
        ],
        cwd=ROOT,
        check=True,
    )


def recommendation_count(rows: list[dict[str, str]], needle: str) -> int:
    needle = needle.lower()
    return sum(1 for row in rows if needle in str(row.get("recommendation", "")).lower())


def append_index(index_path: Path, row: dict[str, object]) -> None:
    rows = read_csv(index_path)
    rows.append({field: row.get(field, "") for field in INDEX_FIELDS})
    rows.sort(key=lambda item: item.get("searched_at", ""), reverse=True)
    write_csv(index_path, rows, INDEX_FIELDS)


def write_empty_report(report_md: Path, keyword: str, raw_count: int) -> None:
    report_md.write_text(
        "\n".join(
            [
                f"# Keyword Opportunity Report: {keyword}",
                "",
                f"- Related products found: {raw_count}",
                "- Candidates after personal-seller filters: 0",
                "",
                "No related listing passed the current price, demand, review, brand, category and risk filters.",
                "This keyword is not an opportunity under the current rules, unless the filters are intentionally adjusted.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    keyword = args.keyword.strip()
    if not keyword:
        raise SystemExit("Keyword cannot be empty.")

    rules = load_json(ROOT / args.rules)
    defaults = load_json(ROOT / args.defaults)
    discovery_rules = load_json(ROOT / args.discovery_rules)
    defaults["marketplace"] = rules.get("marketplace", "US")
    responses = search_responses(args, rules)

    raw_candidates = []
    max_results = int(rules.get("max_results", 100))
    for response in responses:
        for item in find_product_records(response):
            candidate = build_candidate(item, defaults)
            candidate["source_strategy"] = f"keyword:{keyword}"
            raw_candidates.append(candidate)
    raw_candidates = dedupe_candidates(raw_candidates)[:max_results]

    product_filters = discovery_rules.get("product_filters", {})
    eligible_candidates = [candidate for candidate in raw_candidates if product_passes_filters(candidate, product_filters)]

    now = datetime.now()
    run_id = now.strftime("%Y%m%d_%H%M%S")
    run_dir = resolve_path(args.archive_root) / slugify(keyword) / run_id
    raw_csv = run_dir / "raw_candidates.csv"
    input_csv = run_dir / "eligible_candidates.csv"
    ranked_csv = run_dir / "selection_ranked.csv"
    report_md = run_dir / "keyword_opportunity_report.md"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_candidates(raw_candidates, raw_csv)
    write_candidates(eligible_candidates, input_csv)

    if eligible_candidates:
        score_candidates(input_csv, ranked_csv, report_md, int(rules.get("top_n", 30)))
    else:
        write_csv(ranked_csv, [], CANDIDATE_FIELDS + SCORE_FIELDS)
        write_empty_report(report_md, keyword, len(raw_candidates))

    ranked_rows = read_csv(ranked_csv)
    top = ranked_rows[0] if ranked_rows else {}
    index_row = {
        "run_id": run_id,
        "keyword": keyword,
        "searched_at": now.isoformat(timespec="seconds"),
        "raw_result_count": len(raw_candidates),
        "eligible_candidate_count": len(eligible_candidates),
        "go_count": recommendation_count(ranked_rows, "supplier validation"),
        "watch_count": recommendation_count(ranked_rows, "watch"),
        "reject_count": recommendation_count(ranked_rows, "reject"),
        "top_score": top.get("opportunity_score", ""),
        "top_asin": top.get("source_asin", ""),
        "top_title": top.get("product_name", ""),
        "run_dir": stored_path(run_dir),
        "ranked_csv": stored_path(ranked_csv),
        "report_md": stored_path(report_md),
    }
    append_index(resolve_path(args.index), index_row)
    (run_dir / "run_meta.json").write_text(json.dumps(index_row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.no_dashboard:
        subprocess.run(["python3", "build_dashboard.py"], cwd=ROOT, check=True)

    print(f"Keyword: {keyword}")
    print(f"Related products: {len(raw_candidates)}")
    print(f"Candidates after filters: {len(eligible_candidates)}")
    print(f"Preliminary Go/Watch: {index_row['go_count']}/{index_row['watch_count']}")
    print(f"Archive: {run_dir}")
    print(f"Dashboard: {ROOT / 'web' / 'index.html'}")


if __name__ == "__main__":
    main()
