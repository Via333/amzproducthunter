"""Minimal display metadata from already-fetched category and research data."""

import csv
import json
from pathlib import Path

from category_shape_validation import load_category_report, resolve_discovery_run_dir


def image_url(value):
    if isinstance(value, str) and value.startswith("["):
        try:
            value = json.loads(value)
        except ValueError:
            return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    if not isinstance(value, str) or not value.startswith("https://"):
        return ""
    return value


def build_catalog(root, shape_rows, manual_root=None):
    root = Path(root)
    manual_root = Path(manual_root or root)
    rules = json.loads((root / "config/category_shape_validation_rules.json").read_text())
    rules["discovery_runs_root"] = str(root / rules.get("discovery_runs_root", "archive/discovery_runs"))
    run_ids = {row.get("validation_run_id") for row in shape_rows if row.get("validation_run_id")}
    if len(run_ids) == 1:
        rules["discovery_run_id"] = next(iter(run_ids))
    run_dir = resolve_discovery_run_dir(rules)
    catalog = {}
    for node in sorted({row.get("source_category_id") for row in shape_rows if row.get("source_category_id")}):
        report = load_category_report({"source_category_id": node}, run_dir)
        # load_category_report returns the decoded payload (or no data).
        from category_shape_validation import extract_category_report_products
        for product in extract_category_report_products(report or {}):
            asin = str(product.get("asin", "")).strip().upper()
            if not asin:
                continue
            catalog.setdefault(asin, {
                "asin": asin, "title": product.get("title", ""),
                "image": image_url(product.get("main_image") or product.get("image_url") or product.get("image")),
                "category_id": node,
            })
    index = manual_root / "archive/product_research_index.csv"
    if index.is_file():
        with index.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                asin = row.get("asin", "")
                entry = catalog.setdefault(asin, {"asin": asin, "title": row.get("title", "")})
                if (manual_root / "web/research" / f"{asin}.html").is_file():
                    entry["url"] = f"research/{asin}.html"
                products_path = manual_root / "research" / asin / "top_products.csv"
                if products_path.is_file():
                    with products_path.open(encoding="utf-8-sig", newline="") as products_file:
                        for product in csv.DictReader(products_file):
                            product_asin = product.get("asin", "")
                            product_entry = catalog.get(product_asin)
                            if product_entry is not None and not product_entry.get("image"):
                                product_entry["image"] = image_url(product.get("main_image_url") or product.get("image_file"))
    return catalog
