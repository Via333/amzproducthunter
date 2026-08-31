#!/usr/bin/env python3
"""Rebuild derived analytics for existing product research without Sorftime calls."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from business_feasibility import write_business_feasibility
from demand_analysis import DEMAND_FIELDS, analyze_demand
from market_structure import analyze_market
from product_opportunity_research import (
    FORM_FIELDS,
    PRODUCT_FIELDS,
    apply_visual_labels,
    build_form_rows,
    load_json,
    write_csv,
)
from product_taxonomy import classify_product_form


ROOT = Path(__file__).resolve().parent


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh derived analytics from archived research CSV files.")
    parser.add_argument("--asin", action="append", default=[])
    return parser.parse_args()


def research_directories(asins: list[str]) -> list[Path]:
    if asins:
        return [ROOT / "research" / asin.strip().upper() for asin in asins]
    root = ROOT / "research"
    return sorted(path for path in root.iterdir() if path.is_dir()) if root.exists() else []


def main() -> None:
    args = parse_args()
    rules = load_json(ROOT / "config" / "opportunity_research_rules.json")
    updated = 0
    for research_dir in research_directories(args.asin):
        products = read_csv(research_dir / "top_products.csv")
        if not products:
            continue
        for row in products:
            row["product_form"] = classify_product_form(
                row.get("title", ""),
                row.get("bsr_category", ""),
                row.get("product_type", ""),
                rules.get("product_taxonomy", "config/product_taxonomy.json"),
            )
        apply_visual_labels(products)
        forms = build_form_rows(products, rules)
        market = analyze_market(products)
        reviews = read_csv(research_dir / "reviews.csv")
        demand = analyze_demand(reviews, products, ROOT / rules["demand_taxonomy"])
        write_csv(research_dir / "top_products.csv", products, PRODUCT_FIELDS)
        write_csv(research_dir / "product_forms.csv", forms, FORM_FIELDS)
        write_csv(research_dir / "demand_analysis.csv", demand, DEMAND_FIELDS)
        write_csv(
            research_dir / "price_bands.csv",
            market["price_bands"],
            ["price_band", "listing_count", "monthly_sales", "sales_share", "median_reviews"],
        )
        (research_dir / "market_structure.json").write_text(
            json.dumps(market, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        write_business_feasibility(research_dir, ROOT / rules["business_feasibility_rules"])
        updated += 1
        print(f"Updated derived analytics: {research_dir.relative_to(ROOT)}")
    print(f"Research analytics updated: {updated}")


if __name__ == "__main__":
    main()
