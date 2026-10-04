#!/usr/bin/env python3
"""Search Sorftime by keyword, score related listings, and archive every run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
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
from research_workbench import research_lock, valid_keyword
from sorftime_mcp_client import SorftimeMcpClient


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
    parser.add_argument("--run-id", default="", help="Stable queue job identifier.")
    parser.add_argument("--publish-root", default="web/keyword", help="Browser-accessible result directory.")
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


def search_responses(args: argparse.Namespace, rules: dict, run_dir: Path) -> list:
    pages = args.pages or int(rules.get("pages", 5))
    if not 1 <= pages <= 5:
        raise ValueError("搜索页数必须为 1-5")
    page_size = int(rules.get("page_size", 20))
    client = SorftimeMcpClient(timeout=180, max_attempts=1) if not args.from_json and rules.get("transport") == "mcp" else None
    responses = []
    seen = set()
    for page in range(1, (len(args.from_json) if args.from_json else pages) + 1):
        payload = {
            "keyword": args.keyword.strip(),
            "pageIndex": page,
            "pageSize": page_size,
        }
        if args.from_json:
            response = load_json(args.from_json[page - 1])
        elif client:
            response = client.call_tool(rules.get("method", "product_search_from_name"),
                                        {"name": args.keyword, "amz_site": rules.get("marketplace", "US"), "page": page})
        else:
            response = call_sorftime(rules.get("method", "KeywordSearchResults"),
                                     json.dumps(payload, ensure_ascii=False), rules.get("domain", "1"))
        (run_dir / f"response_page_{page}.json").write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if client:
            # An unexpected/error payload must never look like a successful empty search.
            records = response.get("data", response.get("Data")) if isinstance(response, dict) else response
            if not isinstance(records, list) or not all(isinstance(row, dict) and row.get("asin") for row in records):
                raise ValueError("Sorftime 搜索返回格式异常，原始响应已保留")
            response = records
        responses.append(response)
        records = find_product_records(response)
        record_count = len(records)
        print(f"Keyword page {page}: {record_count} related products")
        identities = {str(item.get("asin") or item.get("ASIN") or item.get("title")) for item in records}
        new = identities - seen
        seen.update(identities)
        if record_count == 0 or not new or len(seen) >= int(rules.get("max_results", 100)):
            break
    return responses


def score_candidates(input_csv: Path, ranked_csv: Path, report_md: Path, top_n: int) -> None:
    subprocess.run(
        [
            sys.executable,
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


def search_candidate(item, defaults):
    # MCP search uses a singular `sale` field and structured category tuples.
    source = dict(item)
    if "monthly_sale_volume" in source:
        source["monthly_sales"] = source["monthly_sale_volume"]
    subcategories = source.get("subcategory") or []
    leaf = next((row for row in subcategories if isinstance(row, list) and len(row) >= 2 and row[0]), [])
    top = source.get("top_category") or []
    if not source.get("category"):
        source["category"] = " > ".join(str(value) for value in [top[0] if isinstance(top, list) and top else "", leaf[0] if leaf else ""] if value)
    candidate = build_candidate(source, defaults)
    if leaf:
        candidate.update(source_category_id=str(leaf[1]), source_category_name=str(leaf[0]), source_category_path=source["category"])
    return candidate


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


def run_search(args) -> None:
    keyword = args.keyword = valid_keyword(args.keyword)
    now = datetime.now()
    run_id = args.run_id or now.strftime("%Y%m%d_%H%M%S_%f")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("搜索批次标识无效")
    run_dir = resolve_path(args.archive_root) / slugify(keyword) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    rules = load_json(ROOT / args.rules)
    defaults = load_json(ROOT / args.defaults)
    discovery_rules = load_json(ROOT / args.discovery_rules)
    defaults["marketplace"] = rules.get("marketplace", "US")
    responses = search_responses(args, rules, run_dir)

    raw_candidates = []
    max_results = int(rules.get("max_results", 100))
    for response in responses:
        for item in find_product_records(response):
            candidate = search_candidate(item, defaults)
            candidate["source_strategy"] = f"keyword:{keyword}"
            raw_candidates.append(candidate)
    raw_candidates = dedupe_candidates(raw_candidates)[:max_results]

    product_filters = discovery_rules.get("product_filters", {})
    eligible_candidates = [candidate for candidate in raw_candidates if product_passes_filters(candidate, product_filters)]

    raw_csv = run_dir / "raw_candidates.csv"
    input_csv = run_dir / "eligible_candidates.csv"
    ranked_csv = run_dir / "selection_ranked.csv"
    report_md = run_dir / "keyword_opportunity_report.md"
    write_candidates(raw_candidates, raw_csv)
    write_candidates(eligible_candidates, input_csv)

    if eligible_candidates:
        score_candidates(input_csv, ranked_csv, report_md, int(rules.get("top_n", 30)))
    else:
        write_csv(ranked_csv, [], CANDIDATE_FIELDS + SCORE_FIELDS)
        write_empty_report(report_md, keyword, len(raw_candidates))

    ranked_rows = read_csv(ranked_csv)
    published = resolve_path(args.publish_root) / slugify(keyword) / run_id
    published.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ranked_csv, published / ranked_csv.name)
    shutil.copy2(report_md, published / report_md.name)
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
        "ranked_csv": stored_path(published / ranked_csv.name),
        "report_md": stored_path(published / report_md.name),
    }
    append_index(resolve_path(args.index), index_row)
    (run_dir / "run_meta.json").write_text(json.dumps(index_row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from build_dashboard import normalize_keyword_search_index, normalize_selection
    result = {"summary": normalize_keyword_search_index([index_row])[0], "rows": normalize_selection(ranked_rows)}
    (published / "results.json").write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")

    if not args.no_dashboard:
        subprocess.run([sys.executable, "build_dashboard.py"], cwd=ROOT, check=True)

    print(f"Keyword: {keyword}")
    print(f"Related products: {len(raw_candidates)}")
    print(f"Candidates after filters: {len(eligible_candidates)}")
    print(f"Preliminary Go/Watch: {index_row['go_count']}/{index_row['watch_count']}")
    print(f"Archive: {run_dir}")
    print(f"Dashboard: {ROOT / 'web' / 'index.html'}")


def main() -> None:
    args = parse_args()
    with research_lock(ROOT):
        run_search(args)


if __name__ == "__main__":
    main()
