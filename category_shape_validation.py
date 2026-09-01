#!/usr/bin/env python3
"""Validate seed ASINs at the minimum-category and product-form level."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

from import_sorftime_candidates import to_float
from product_selection import amazon_listing_url


OUTPUT_FIELDS = [
    "seed_rank",
    "seed_asin",
    "seed_listing_url",
    "seed_title",
    "seed_score",
    "seed_recommendation",
    "source_category_id",
    "source_category_name",
    "validation_run_id",
    "category_path",
    "data_quality",
    "product_form",
    "shape_scope",
    "shape_score",
    "shape_recommendation",
    "category_sample_count",
    "category_total_sales",
    "category_top10_sales_share",
    "category_top20_sales_share",
    "category_top50_sales_share",
    "category_review_sales_spearman",
    "category_new_listing_share_12m",
    "category_price_p25",
    "category_price_median",
    "category_price_p75",
    "category_median_reviews",
    "category_top10_median_reviews",
    "category_top_brand",
    "category_top_brand_share",
    "category_low_review_high_sales_count",
    "category_cn_hk_seller_share",
    "category_fba_share",
    "form_count",
    "form_direct_count",
    "form_keyword_count",
    "form_avg_price",
    "form_avg_sales",
    "form_median_sales",
    "form_total_sales",
    "form_sales_share",
    "form_top3_sales_share",
    "form_median_reviews",
    "form_avg_rating",
    "form_low_review_high_sales_count",
    "form_top_materials",
    "form_top_packs",
    "form_top_styles",
    "validation_flags",
    "opportunity_thesis",
    "next_action",
    "research_page",
]


ARCHIVE_FIELDS = [
    "shape_archive_key",
    "archive_first_seen",
    "archive_last_seen",
    "archive_seen_count",
    "archive_best_score",
    "archive_latest_score",
    "archive_status",
    "archive_last_run_id",
    "research_status",
    "archive_notes",
]


KNOWN_TITLE_FORMS = {
    "magnetic tool mat",
    "card binder",
    "tool holster",
    "duster refill",
    "boat rail cup holder",
    "boat storage bag",
    "boat caddy",
    "digital scale",
    "socket organizer",
    "beeswax bread bag",
    "disposable bakery bread bag",
    "bread box",
    "beeswax wrap",
    "toilet brush holder",
    "toilet plunger brush combo",
    "under-rim toilet brush",
    "silicone toilet brush",
    "disposable toilet brush refill",
    "hose splitter",
    "hose connector",
    "bike air pump",
    "salt grinder",
    "solar pathway lights",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Validate Amazon seeds by category and product form.")
    parser.add_argument("--rules", default="config/category_shape_validation_rules.json")
    parser.add_argument("--input", help="Ranked seed CSV. Defaults to rules input.")
    parser.add_argument("--output-csv", help="Validation output CSV.")
    parser.add_argument("--output-report", help="Markdown report output.")
    parser.add_argument("--archive-dir", help="Archive directory.")
    parser.add_argument("--no-archive", action="store_true")
    return parser.parse_args()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_csv(path):
    csv_path = Path(path)
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows, fields):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_fields = list(fields)
    for row in rows:
        for key in row:
            if key not in all_fields:
                all_fields.append(key)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=all_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def avg(values):
    values = [to_float(value, 0) for value in values if value not in (None, "")]
    return mean(values) if values else 0.0


def med(values):
    values = [to_float(value, 0) for value in values if value not in (None, "")]
    return median(values) if values else 0.0


def percentile(values, fraction):
    numbers = sorted(to_float(value, 0) for value in values if value not in (None, ""))
    if not numbers:
        return 0.0
    position = (len(numbers) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return numbers[lower]
    return numbers[lower] + (numbers[upper] - numbers[lower]) * (position - lower)


def rank_values(values):
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor
        value = values[order[cursor]]
        while end + 1 < len(order) and values[order[end + 1]] == value:
            end += 1
        rank = (cursor + end + 2) / 2
        for index in order[cursor : end + 1]:
            ranks[index] = rank
        cursor = end + 1
    return ranks


def spearman(values_a, values_b):
    if len(values_a) != len(values_b) or len(values_a) < 2:
        return 0.0
    ranks_a = rank_values(values_a)
    ranks_b = rank_values(values_b)
    avg_a = mean(ranks_a)
    avg_b = mean(ranks_b)
    numerator = sum((a - avg_a) * (b - avg_b) for a, b in zip(ranks_a, ranks_b))
    denominator = math.sqrt(
        sum((a - avg_a) ** 2 for a in ranks_a) * sum((b - avg_b) ** 2 for b in ranks_b)
    )
    return numerator / denominator if denominator else 0.0


def normalize_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def short_title(value, limit=110):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def select_seed_rows(rows, rules):
    selected = []
    recommendation_contains = str(rules.get("seed_recommendation_contains", "Watch"))
    for rank, row in enumerate(rows, start=1):
        asin = str(row.get("source_asin", "") or "").strip()
        if not asin:
            continue
        if recommendation_contains and recommendation_contains not in str(row.get("recommendation", "")):
            continue
        selected.append({**row, "_seed_rank": rank})
    return selected[: int(rules.get("seed_limit", len(selected)))]


def by_key(rows, key):
    return {row.get(key, ""): row for row in rows if row.get(key, "")}


def product_form_from_title(title):
    text = normalize_text(title)
    rules = [
        ("magnetic tool mat", ["magnetic tool mat", "magnetic work mat"]),
        ("card binder", ["card binder", "trading card album", "card collection binder"]),
        ("tool holster", ["tool holster", "drill holster", "tool belt holder"]),
        ("duster refill", ["duster refill", "duster refills", "duster replacement"]),
        ("boat rail cup holder", ["boat rail cup holder", "marine cup holder", "clamp on cup holder"]),
        ("boat storage bag", ["boat storage bag", "boat gear bag", "boat tote bag"]),
        ("boat caddy", ["boat caddy", "marine caddy"]),
        ("digital scale", ["digital gram scale", "digital kitchen scale", "digital scale"]),
        ("socket organizer", ["socket organizer", "socket holder", "socket tray"]),
        ("beeswax bread bag", ["beeswax bread bag", "beeswax linen", "beeswax sourdough", "waxed bread bag"]),
        ("disposable bakery bread bag", ["bakery bread bag", "bread bag with window", "paper bread bag"]),
        ("bread box", ["bread box", "bread storage container", "bread keeper"]),
        ("beeswax wrap", ["beeswax wrap", "food wrap"]),
        ("toilet brush holder", ["toilet brush and holder", "toilet bowl brush and holder", "toilet brush holder"]),
        ("toilet plunger brush combo", ["plunger and bowl brush", "plunger and toilet brush", "plunger brush combo"]),
        ("under-rim toilet brush", ["under rim", "under-rim"]),
        ("silicone toilet brush", ["silicone toilet brush"]),
        ("disposable toilet brush refill", ["disposable toilet brush", "toilet wand refill", "refill heads"]),
        ("hose splitter", ["hose splitter", "faucet splitter", "spigot splitter", "water hose bib"]),
        ("hose connector", ["hose connector", "garden hose connector", "hose adapter"]),
        ("bike air pump", ["bike air pump", "electric bike pump", "bicycle pump"]),
        ("salt grinder", ["salt mill", "salt grinder", "pepper grinder", "peppermill"]),
        ("solar pathway lights", ["solar pathway lights", "solar walkway lights", "landscape lights"]),
    ]
    for label, terms in rules:
        if any(term in text for term in terms):
            return label
    tokens = [token for token in text.split() if len(token) > 2]
    return " ".join(tokens[:3]) if tokens else "unknown"


def product_form_from_category_product(product):
    title_form = product_form_from_title(product.get("title", ""))
    if title_form in KNOWN_TITLE_FORMS:
        return title_form
    product_category = normalize_text(product.get("product_category", ""))
    if product_category and product_category != "unknown":
        return product_category
    return title_form


def active_research_dir(seed_asin, rules):
    root = Path(rules.get("research_root", "research"))
    path = root / seed_asin
    if (path / "top_products.csv").exists() and (path / "product_forms.csv").exists():
        return path
    return None


def resolve_discovery_run_dir(rules):
    runs_root = Path(rules.get("discovery_runs_root", "archive/discovery_runs"))
    requested_run_id = str(os.environ.get("AMZ_WEEKLY_RUN_ID", "") or rules.get("discovery_run_id", "")).strip()
    if requested_run_id:
        requested = runs_root / requested_run_id
        manifest_path = requested / "run_manifest.json"
        if manifest_path.exists():
            manifest = load_json(manifest_path)
            replay_source = str(manifest.get("replay_source", "") or "").strip()
            if replay_source:
                replay_dir = Path(replay_source)
                if (replay_dir / "raw_category_reports").exists():
                    return replay_dir
            if (requested / "raw_category_reports").exists():
                return requested

    candidates = []
    if not runs_root.exists():
        return None
    for run_dir in runs_root.iterdir():
        manifest_path = run_dir / "run_manifest.json"
        raw_dir = run_dir / "raw_category_reports"
        if not manifest_path.exists() or not raw_dir.exists():
            continue
        try:
            manifest = load_json(manifest_path)
        except (json.JSONDecodeError, OSError):
            continue
        if manifest.get("status") != "success":
            continue
        finished_at = str(manifest.get("finished_at", "") or "")
        candidates.append((finished_at, run_dir.stat().st_mtime, run_dir))
    return max(candidates, default=("", 0, None))[2]


def extract_category_report_products(report):
    if not isinstance(report, dict):
        return []
    data = report.get("data")
    if isinstance(data, dict) and isinstance(data.get("top100_products"), list):
        return [row for row in data["top100_products"] if isinstance(row, dict)]
    if isinstance(report.get("top100_products"), list):
        return [row for row in report["top100_products"] if isinstance(row, dict)]
    return []


def load_category_report(seed, discovery_run_dir):
    if not discovery_run_dir:
        return None
    category_id = str(seed.get("source_category_id", "") or "").strip()
    if not category_id:
        return None
    report_path = Path(discovery_run_dir) / "raw_category_reports" / f"{category_id}.json"
    if not report_path.exists():
        return None
    try:
        return load_json(report_path)
    except (json.JSONDecodeError, OSError):
        return None


def top_counts(values, limit=3):
    counts = Counter(value for value in values if value and value != "unknown")
    return "; ".join(f"{key}:{count}" for key, count in counts.most_common(limit))


def seller_address(row):
    return str(row.get("seller_address") or row.get("BuyboxSellerAddress") or "").upper()


def category_metrics(product_rows):
    rows = [row for row in product_rows if row.get("asin")]
    sales = [to_float(row.get("monthly_sales"), 0) for row in rows]
    reviews = [to_float(row.get("reviews"), 0) for row in rows]
    top10 = sorted(rows, key=lambda row: to_float(row.get("monthly_sales"), 0), reverse=True)[:10]
    top10_reviews = [to_float(row.get("reviews"), 0) for row in top10]
    brands = [row.get("brand", "") for row in top10 if row.get("brand")]
    brand_counts = Counter(brands)
    top_brand, top_brand_count = brand_counts.most_common(1)[0] if brand_counts else ("", 0)
    low_review_high_sales = [
        row for row in rows if to_float(row.get("reviews"), 0) <= 300 and to_float(row.get("monthly_sales"), 0) >= 500
    ]
    cn_hk = [row for row in rows if seller_address(row) in {"CN", "HK"}]
    fba = [row for row in rows if str(row.get("is_fba", "")).lower() in {"true", "1", "yes"}]
    category_paths = [row.get("bsr_category", "") for row in rows if row.get("bsr_category")]
    return {
        "category_path": category_paths[0] if category_paths else "",
        "category_sample_count": len(rows),
        "category_total_sales": round(sum(sales), 0),
        "category_median_reviews": round(med(reviews), 0),
        "category_top10_median_reviews": round(med(top10_reviews), 0),
        "category_top_brand": top_brand,
        "category_top_brand_share": round(top_brand_count / len(top10), 3) if top10 else 0,
        "category_low_review_high_sales_count": len(low_review_high_sales),
        "category_cn_hk_seller_share": round(len(cn_hk) / len(rows), 3) if rows else 0,
        "category_fba_share": round(len(fba) / len(rows), 3) if rows else 0,
    }


def normalized_category_product(product, category_path):
    seller_origin = str(product.get("seller_origin", "") or "").strip().lower()
    if "香港" in seller_origin or seller_origin == "hk":
        seller_code = "HK"
    elif "中国" in seller_origin or seller_origin in {"cn", "china"}:
        seller_code = "CN"
    else:
        seller_code = seller_origin.upper()
    delivery_type = str(product.get("delivery_type", "") or "").strip().lower()
    return {
        "asin": str(product.get("asin", "") or "").strip(),
        "title": str(product.get("title", "") or "").strip(),
        "brand": str(product.get("brand", "") or "").strip(),
        "bsr_category": category_path,
        "monthly_sales": to_float(product.get("monthly_sales_volume"), 0),
        "reviews": to_float(product.get("review_count"), 0),
        "rating": to_float(product.get("star_rating"), 0),
        "price": to_float(product.get("price"), 0),
        "online_date": str(product.get("online_date", "") or "").strip(),
        "seller_address": seller_code,
        "is_fba": delivery_type in {"fba", "amzfba"},
        "product_form": product_form_from_category_product(product),
    }


def sales_share(rows, limit):
    sales = sorted((to_float(row.get("monthly_sales"), 0) for row in rows), reverse=True)
    total = sum(sales)
    return sum(sales[:limit]) / total if total else 0.0


def recent_listing_share(rows, months=12):
    cutoff_days = months * 30.5
    ages = []
    now = datetime.now()
    for row in rows:
        raw_date = str(row.get("online_date", "") or "").strip()
        if not raw_date:
            continue
        try:
            listed_at = datetime.strptime(raw_date[:10], "%Y-%m-%d")
        except ValueError:
            continue
        ages.append((now - listed_at).days)
    return sum(1 for age in ages if age <= cutoff_days) / len(ages) if ages else 0.0


def category_market_metrics(rows):
    metrics = category_metrics(rows)
    prices = [row.get("price") for row in rows]
    sales = [to_float(row.get("monthly_sales"), 0) for row in rows]
    reviews = [to_float(row.get("reviews"), 0) for row in rows]
    metrics.update(
        {
            "category_top10_sales_share": round(sales_share(rows, 10), 3),
            "category_top20_sales_share": round(sales_share(rows, 20), 3),
            "category_top50_sales_share": round(sales_share(rows, 50), 3),
            "category_review_sales_spearman": round(spearman(reviews, sales), 3),
            "category_new_listing_share_12m": round(recent_listing_share(rows, 12), 3),
            "category_price_p25": round(percentile(prices, 0.25), 2),
            "category_price_median": round(percentile(prices, 0.5), 2),
            "category_price_p75": round(percentile(prices, 0.75), 2),
        }
    )
    return metrics


def form_summary_rows(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("product_form") or "unknown"].append(row)
    category_sales = sum(to_float(row.get("monthly_sales"), 0) for row in rows)
    summaries = []
    for product_form, form_products in grouped.items():
        form_sales = [to_float(row.get("monthly_sales"), 0) for row in form_products]
        form_reviews = [to_float(row.get("reviews"), 0) for row in form_products]
        total_sales = sum(form_sales)
        low_review_high_sales = sum(
            1
            for row in form_products
            if to_float(row.get("reviews"), 0) <= 300 and to_float(row.get("monthly_sales"), 0) >= 500
        )
        summaries.append(
            {
                "product_form": product_form,
                "count": len(form_products),
                "direct_count": len(form_products),
                "keyword_count": 0,
                "avg_price": round(avg(row.get("price") for row in form_products), 2),
                "avg_monthly_sales": round(avg(form_sales), 0),
                "median_monthly_sales": round(med(form_sales), 0),
                "total_monthly_sales": round(total_sales, 0),
                "sales_share": round(total_sales / category_sales, 3) if category_sales else 0,
                "top3_sales_share": round(sum(sorted(form_sales, reverse=True)[:3]) / total_sales, 3)
                if total_sales
                else 0,
                "median_reviews": round(med(form_reviews), 0),
                "avg_rating": round(avg(row.get("rating") for row in form_products), 1),
                "low_review_high_sales_count": low_review_high_sales,
                "top_materials": "",
                "top_pack_counts": "",
                "top_styles": "",
            }
        )
    summaries.sort(key=lambda row: (-to_float(row.get("total_monthly_sales"), 0), row.get("product_form", "")))
    return summaries


def seed_context(seed):
    asin = seed.get("source_asin", "")
    return {
        "seed_rank": seed.get("_seed_rank", ""),
        "seed_asin": asin,
        "seed_listing_url": seed.get("listing_url") or amazon_listing_url(asin),
        "seed_title": seed.get("product_name", ""),
        "seed_score": seed.get("opportunity_score", ""),
        "seed_recommendation": seed.get("recommendation", ""),
        "source_category_id": seed.get("source_category_id", ""),
        "source_category_name": seed.get("source_category_name", ""),
    }


def build_rows_from_category_report(seed, report, rules, run_id=""):
    category_path = seed.get("source_category_path") or seed.get("category", "")
    raw_products = extract_category_report_products(report)
    products = [normalized_category_product(product, category_path) for product in raw_products]
    products = [product for product in products if product.get("asin")]
    if not products:
        return []

    metrics = category_market_metrics(products)
    seed_asin = seed.get("source_asin", "")
    seed_product = next((row for row in products if row.get("asin") == seed_asin), None)
    seed_form = (
        seed_product.get("product_form")
        if seed_product
        else product_form_from_title(seed.get("product_name", ""))
    )
    rows = []
    for form in form_summary_rows(products):
        product_form = form.get("product_form") or "unknown"
        scope = "seed_form" if normalize_text(product_form) == normalize_text(seed_form) else "adjacent_form"
        row = {
            **seed_context(seed),
            **metrics,
            "validation_run_id": run_id,
            "data_quality": "category_top100",
            "product_form": product_form,
            "shape_scope": scope,
            "form_count": form.get("count", 0),
            "form_direct_count": form.get("direct_count", 0),
            "form_keyword_count": form.get("keyword_count", 0),
            "form_avg_price": form.get("avg_price", 0),
            "form_avg_sales": form.get("avg_monthly_sales", 0),
            "form_median_sales": form.get("median_monthly_sales", 0),
            "form_total_sales": form.get("total_monthly_sales", 0),
            "form_sales_share": form.get("sales_share", 0),
            "form_top3_sales_share": form.get("top3_sales_share", 0),
            "form_median_reviews": form.get("median_reviews", 0),
            "form_avg_rating": form.get("avg_rating", 0),
            "form_low_review_high_sales_count": form.get("low_review_high_sales_count", 0),
            "form_top_materials": form.get("top_materials", ""),
            "form_top_packs": form.get("top_pack_counts", ""),
            "form_top_styles": form.get("top_styles", ""),
            "research_page": "",
        }
        rows.append(evaluate_shape_row(row, rules))
    return rows


def build_rows_from_research(seed, research_dir, rules):
    top_products = read_csv(research_dir / "top_products.csv")
    form_rows = read_csv(research_dir / "product_forms.csv")
    metrics = category_metrics(top_products)
    market_path = research_dir / "market_structure.json"
    market = load_json(market_path) if market_path.exists() else {}
    metrics.update(
        {
            "category_top10_sales_share": market.get("top10_sales_share", 0),
            "category_top20_sales_share": market.get("top20_sales_share", 0),
            "category_top50_sales_share": market.get("top50_sales_share", 0),
            "category_review_sales_spearman": market.get("review_sales_spearman", 0),
            "category_new_listing_share_12m": market.get("new_listing_share_12m", 0),
            "category_price_p25": market.get("price_p25", 0),
            "category_price_median": market.get("price_median", 0),
            "category_price_p75": market.get("price_p75", 0),
        }
    )
    seed_asin = seed.get("source_asin", "")
    seed_product = next((row for row in top_products if row.get("asin") == seed_asin or row.get("source") == "seed"), {})
    seed_form = seed_product.get("visual_product_form") or seed_product.get("product_form") or product_form_from_title(seed.get("product_name", ""))
    rows = []
    for form in form_rows:
        product_form = form.get("product_form") or "unknown"
        scope = "seed_form" if normalize_text(product_form) == normalize_text(seed_form) else "adjacent_form"
        row = {
            **seed_context(seed),
            **metrics,
            "data_quality": "category_top100",
            "product_form": product_form,
            "shape_scope": scope,
            "form_count": form.get("count", 0),
            "form_direct_count": form.get("direct_count", 0),
            "form_keyword_count": form.get("keyword_count", 0),
            "form_avg_price": form.get("avg_price", 0),
            "form_avg_sales": form.get("avg_monthly_sales", 0),
            "form_median_sales": form.get("median_monthly_sales") or form.get("avg_monthly_sales", 0),
            "form_total_sales": form.get("total_monthly_sales")
            or to_float(form.get("avg_monthly_sales"), 0) * max(1, to_float(form.get("count"), 1)),
            "form_sales_share": form.get("sales_share", 0),
            "form_top3_sales_share": form.get("top3_sales_share", 0),
            "form_median_reviews": form.get("median_reviews", 0),
            "form_avg_rating": form.get("avg_rating", 0),
            "form_low_review_high_sales_count": form.get("low_review_high_sales_count", 0),
            "form_top_materials": form.get("top_materials", ""),
            "form_top_packs": form.get("top_pack_counts", ""),
            "form_top_styles": form.get("top_styles", ""),
            "research_page": f"web/research/{seed_asin}.html" if seed_asin else "",
        }
        rows.append(evaluate_shape_row(row, rules))
    return rows


def build_row_from_deep_summary(seed, summary, rules):
    product_form = product_form_from_title(seed.get("product_name", ""))
    row = {
        **seed_context(seed),
        "category_path": summary.get("category") or seed.get("category", ""),
        "data_quality": "competitor_deep_dive_only",
        "product_form": product_form,
        "shape_scope": "seed_form",
        "category_sample_count": summary.get("competitor_count", 0),
        "category_total_sales": summary.get("total_top_competitor_sales", 0),
        "category_median_reviews": summary.get("median_competitor_reviews", 0),
        "category_top10_median_reviews": summary.get("median_competitor_reviews", 0),
        "category_top_brand": summary.get("top_brand", ""),
        "category_top_brand_share": summary.get("top_brand_share", 0),
        "category_low_review_high_sales_count": summary.get("low_review_high_sales_count", 0),
        "category_cn_hk_seller_share": summary.get("cn_hk_seller_share", 0),
        "category_fba_share": summary.get("fba_share", 0),
        "form_count": summary.get("direct_competitor_count", 0) or summary.get("competitor_count", 0),
        "form_direct_count": summary.get("direct_competitor_count", 0),
        "form_keyword_count": summary.get("keyword_competitor_count", 0),
        "form_avg_price": summary.get("avg_competitor_price", 0),
        "form_avg_sales": summary.get("avg_competitor_sales", 0),
        "form_median_reviews": summary.get("median_competitor_reviews", 0),
        "form_avg_rating": summary.get("avg_competitor_rating", 0),
        "form_low_review_high_sales_count": summary.get("low_review_high_sales_count", 0),
        "form_top_materials": "",
        "form_top_packs": "",
        "form_top_styles": "",
        "validation_flags": summary.get("deep_dive_flags", ""),
        "research_page": "",
    }
    return evaluate_shape_row(row, rules)


def build_pending_row(seed, rules):
    row = {
        **seed_context(seed),
        "category_path": seed.get("category", ""),
        "data_quality": "seed_only",
        "product_form": product_form_from_title(seed.get("product_name", "")),
        "shape_scope": "seed_form",
        "category_sample_count": 0,
        "category_total_sales": 0,
        "category_median_reviews": 0,
        "category_top10_median_reviews": 0,
        "category_top_brand": "",
        "category_top_brand_share": 0,
        "category_low_review_high_sales_count": 0,
        "category_cn_hk_seller_share": 0,
        "category_fba_share": 0,
        "form_count": 0,
        "form_direct_count": 0,
        "form_keyword_count": 0,
        "form_avg_price": seed.get("target_price", 0),
        "form_avg_sales": seed.get("est_monthly_sales", 0),
        "form_median_reviews": seed.get("avg_review_count", 0),
        "form_avg_rating": seed.get("avg_rating", 0),
        "form_low_review_high_sales_count": 0,
        "form_top_materials": "",
        "form_top_packs": "",
        "form_top_styles": "",
        "research_page": "",
    }
    return evaluate_shape_row(row, rules)


def evaluate_shape_row(row, rules):
    thresholds = rules["thresholds"]
    data_quality = row.get("data_quality", "")
    form_avg_sales = to_float(row.get("form_avg_sales"), 0)
    form_median_sales = to_float(row.get("form_median_sales"), form_avg_sales)
    form_total_sales = to_float(row.get("form_total_sales"), form_avg_sales * max(1, to_float(row.get("form_count"), 1)))
    form_median_reviews = to_float(row.get("form_median_reviews"), 0)
    form_low_gap = to_float(row.get("form_low_review_high_sales_count"), 0)
    form_count = to_float(row.get("form_count"), 0)
    top_brand_share = to_float(row.get("category_top_brand_share"), 0)
    category_top10_reviews = to_float(row.get("category_top10_median_reviews"), 0)
    category_median_reviews = to_float(row.get("category_median_reviews"), 0)
    top10_sales_share = to_float(row.get("category_top10_sales_share"), 0)
    form_top3_sales_share = to_float(row.get("form_top3_sales_share"), 0)
    category_sample_count = to_float(row.get("category_sample_count"), 0)

    flags = [part.strip() for part in str(row.get("validation_flags", "")).split(";") if part.strip()]
    if data_quality == "seed_only":
        flags.append("needs category Top100")
    if data_quality == "competitor_deep_dive_only":
        flags.append("needs minimum-category Top100")
    if category_median_reviews >= thresholds["category_review_wall_median"]:
        flags.append("category review wall")
    if category_top10_reviews >= thresholds["category_top10_review_wall"]:
        flags.append("top10 review wall")
    if top_brand_share >= thresholds["category_top_brand_share"]:
        flags.append("brand concentration")
    if (
        category_sample_count >= thresholds.get("category_concentration_min_count", 20)
        and top10_sales_share >= thresholds.get("category_top10_sales_share", 1.1)
    ):
        flags.append("sales concentration")
    if form_count and form_count < thresholds["form_min_count"]:
        flags.append("thin form sample")
    if form_avg_sales < thresholds["form_min_avg_sales"] and form_total_sales < thresholds.get("form_min_total_sales", 0):
        flags.append("weak form demand")
    if (
        form_count >= thresholds.get("form_concentration_min_count", 6)
        and form_top3_sales_share >= thresholds.get("form_top3_sales_share", 1.1)
    ):
        flags.append("form sales concentrated")
    if form_median_reviews > thresholds["form_max_median_reviews"] and form_low_gap < thresholds["form_min_low_review_high_sales"]:
        flags.append("form review wall")

    demand_score = (
        min(100, form_total_sales / thresholds.get("form_full_total_sales", 3000) * 100) * 0.55
        + min(100, form_median_sales / thresholds.get("form_full_median_sales", 500) * 100) * 0.45
    )
    access_score = max(0, 100 - form_median_reviews / 1000 * 100)
    gap_score = min(100, form_low_gap * 20)
    concentration_score = max(0, 100 - top_brand_share * 100)
    scope_score = 100 if row.get("shape_scope") == "seed_form" else 70
    shape_score = round(
        demand_score * 0.28
        + access_score * 0.28
        + gap_score * 0.22
        + concentration_score * 0.12
        + scope_score * 0.1,
        1,
    )

    hard_flags = {
        "review wall",
        "category review wall",
        "top10 review wall",
        "brand concentration",
        "sales concentration",
        "form sales concentrated",
        "form review wall",
        "no low-review high-sales gap",
    }
    has_hard_flag = any(flag in hard_flags for flag in flags)
    has_thin_sample = "thin form sample" in flags
    has_top100 = data_quality == "category_top100"
    is_seed_form = row.get("shape_scope") == "seed_form"

    if not has_top100 and has_hard_flag and form_low_gap < thresholds["form_min_low_review_high_sales"]:
        recommendation = "Reject category/form"
    elif not has_top100:
        recommendation = "Needs category Top100"
    elif has_hard_flag and form_low_gap < thresholds["form_min_low_review_high_sales"]:
        recommendation = "Reject category/form"
    elif has_hard_flag:
        recommendation = "Watch shape" if shape_score >= thresholds["watch_shape_score"] else "Reject category/form"
    elif has_thin_sample:
        recommendation = "Watch shape" if shape_score >= thresholds["watch_shape_score"] else "Reject category/form"
    elif is_seed_form and shape_score >= thresholds["shape_opportunity_score"]:
        recommendation = "Shape opportunity"
    elif shape_score >= thresholds["watch_shape_score"]:
        recommendation = "Watch shape"
    else:
        recommendation = "Reject category/form"

    row["shape_score"] = shape_score
    row["shape_recommendation"] = recommendation
    row["validation_flags"] = "; ".join(dict.fromkeys(flags))
    row["opportunity_thesis"] = opportunity_thesis(row)
    row["next_action"] = next_action(row)
    return row


def opportunity_thesis(row):
    rec = row.get("shape_recommendation", "")
    form = row.get("product_form", "")
    if rec == "Shape opportunity":
        return (
            f"{form} 形态已通过类目 Top100 验证：月销均值 {row.get('form_avg_sales')}，"
            f"形态总月销 {row.get('form_total_sales')}，评论中位数 {row.get('form_median_reviews')}，"
            f"低评高销样本 {row.get('form_low_review_high_sales_count')} 个。"
        )
    if rec == "Watch shape":
        return f"{form} 有部分需求信号，但仍需确认评论墙、品牌集中或供应链差异化。"
    if rec == "Needs category Top100":
        return f"{form} 目前只是种子入口，还不能判定机会；需要拉最小类目 Top100 后按形态复核。"
    return f"{form} 当前类目/形态竞争结构不适合直接进入机会档案。"


def next_action(row):
    rec = row.get("shape_recommendation", "")
    if rec == "Shape opportunity":
        return "进入单品研究/供应商验证"
    if rec == "Watch shape":
        return "补评论和供应商报价后再判断"
    if rec == "Needs category Top100":
        return "下次类目扫描补齐该最小类目 Top100；不要为此单独重复调用旧 CLI"
    return "淘汰或仅保留历史追溯"


def build_validation_rows(seeds, deep_rows, rules, discovery_run_dir=None):
    deep_by_asin = by_key(deep_rows, "source_asin") if rules.get("allow_deep_dive_fallback") else {}
    rows = []
    validation_run_id = Path(discovery_run_dir).name if discovery_run_dir else ""
    for seed in seeds:
        asin = seed.get("source_asin", "")
        category_report = load_category_report(seed, discovery_run_dir)
        research_dir = active_research_dir(asin, rules)
        if category_report:
            report_rows = build_rows_from_category_report(seed, category_report, rules, validation_run_id)
            rows.extend(report_rows or [build_pending_row(seed, rules)])
        elif research_dir:
            rows.extend(build_rows_from_research(seed, research_dir, rules))
        elif asin in deep_by_asin:
            rows.append(build_row_from_deep_summary(seed, deep_by_asin[asin], rules))
        else:
            rows.append(build_pending_row(seed, rules))
    rows.sort(
        key=lambda row: (
            to_float(row.get("seed_rank"), 999999),
            row.get("shape_scope") != "seed_form",
            -to_float(row.get("shape_score"), 0),
            row.get("product_form", ""),
        )
    )
    return rows


def shape_archive_key(row):
    category = normalize_text(row.get("category_path"))
    form = normalize_text(row.get("product_form"))
    return f"shape:{category}|{form}" if category or form else ""


def should_archive_shape(row):
    return row.get("shape_recommendation") == "Shape opportunity"


def read_csv_rows(path):
    return read_csv(path)


def update_shape_archive(rows, archive_dir, run_id):
    archive_path = archive_dir / "shape_opportunity_library.csv"
    existing = read_csv_rows(archive_path)
    by_archive_key = {row.get("shape_archive_key", ""): row for row in existing if row.get("shape_archive_key", "")}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    active_keys = set()
    for row in rows:
        if not should_archive_shape(row):
            continue
        key = shape_archive_key(row)
        if not key:
            continue
        active_keys.add(key)
        prior = by_archive_key.get(key, {})
        score = to_float(row.get("shape_score"), 0)
        best_score = max(to_float(prior.get("archive_best_score"), 0), score)
        merged = {**prior, **row}
        merged.update(
            {
                "shape_archive_key": key,
                "archive_first_seen": prior.get("archive_first_seen") or now,
                "archive_last_seen": now,
                "archive_seen_count": int(to_float(prior.get("archive_seen_count"), 0)) + 1,
                "archive_best_score": round(best_score, 1),
                "archive_latest_score": row.get("shape_score", ""),
                "archive_status": "active_in_latest_run",
                "archive_last_run_id": run_id,
                "research_status": prior.get("research_status") or "needs_supplier_validation",
                "archive_notes": prior.get("archive_notes", ""),
            }
        )
        by_archive_key[key] = merged

    for key, row in by_archive_key.items():
        if key not in active_keys:
            row["archive_status"] = "not_in_latest_run"

    archive_rows = list(by_archive_key.values())
    archive_rows.sort(
        key=lambda row: (
            row.get("archive_status") != "active_in_latest_run",
            -to_float(row.get("archive_best_score"), 0),
            row.get("product_form", ""),
        )
    )
    # A zero-opportunity run is still a valid result. Always create a CSV with
    # headers so downstream reporting can distinguish "none found" from a
    # failed or missing archive step.
    write_csv(archive_path, archive_rows, OUTPUT_FIELDS + ARCHIVE_FIELDS)
    return archive_path, len(active_keys), len(archive_rows)


def archive_run(rows, input_path, output_csv_path, report_path, archive_dir):
    run_id = os.environ.get("AMZ_WEEKLY_RUN_ID") or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = archive_dir / "category_shape_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_csv(run_dir / "category_shape_validation.csv", rows, OUTPUT_FIELDS)
    if Path(input_path).exists():
        shutil.copyfile(input_path, run_dir / "source_selection_ranked.csv")
    if Path(output_csv_path).exists():
        shutil.copyfile(output_csv_path, run_dir / "latest_category_shape_validation.csv")
    if Path(report_path).exists():
        shutil.copyfile(report_path, run_dir / "category_shape_validation.md")
    archive_path, active_count, total_count = update_shape_archive(rows, archive_dir, run_id)
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "archive_path": archive_path,
        "active_count": active_count,
        "total_count": total_count,
    }


def write_report(path, rows):
    lines = [
        "# 类目/形态验证报告",
        "",
        f"- 验证记录数：{len(rows)}",
        f"- 通过形态机会：{sum(1 for row in rows if row.get('shape_recommendation') == 'Shape opportunity')}",
        f"- 待补 Top100：{sum(1 for row in rows if row.get('shape_recommendation') == 'Needs category Top100')}",
        "",
        "## 形态排序",
        "",
        "| 种子 | 形态 | 数据层级 | 分数 | 结论 | 类目/评论墙 | 形态月销 | 形态评论 | 低评高销 | 说明 |",
        "| --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {seed} | {form} | {quality} | {score} | {rec} | {cat_reviews}/{top_reviews} | {sales} | {reviews} | {gap} | {thesis} |".format(
                seed=markdown_link(row.get("seed_asin", ""), row.get("seed_listing_url", "")),
                form=escape_pipe(row.get("product_form", "")),
                quality=escape_pipe(row.get("data_quality", "")),
                score=row.get("shape_score", ""),
                rec=escape_pipe(row.get("shape_recommendation", "")),
                cat_reviews=row.get("category_median_reviews", ""),
                top_reviews=row.get("category_top10_median_reviews", ""),
                sales=row.get("form_avg_sales", ""),
                reviews=row.get("form_median_reviews", ""),
                gap=row.get("form_low_review_high_sales_count", ""),
                thesis=escape_pipe(row.get("opportunity_thesis", "")),
            )
        )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def markdown_link(label, url):
    label = escape_pipe(label)
    return f"[{label}]({url})" if url else label


def escape_pipe(value):
    return str(value).replace("|", "\\|")


def main():
    args = parse_args()
    rules = load_json(args.rules)
    input_path = args.input or rules["input"]
    output_csv = args.output_csv or rules["output_csv"]
    output_report = args.output_report or rules["output_report"]
    archive_dir = Path(args.archive_dir or rules["archive_dir"])

    seeds = select_seed_rows(read_csv(input_path), rules)
    deep_rows = read_csv(rules["deep_dive_summary"])
    discovery_run_dir = resolve_discovery_run_dir(rules)
    rows = build_validation_rows(seeds, deep_rows, rules, discovery_run_dir)
    write_csv(output_csv, rows, OUTPUT_FIELDS)
    write_report(output_report, rows)
    archive_result = None
    if not args.no_archive:
        archive_result = archive_run(rows, input_path, output_csv, output_report, archive_dir)

    print(f"Validated seeds: {len(seeds)}")
    print(f"Validation rows: {len(rows)}")
    print(f"Category Top100 source: {discovery_run_dir or 'none'}")
    print(f"CSV: {output_csv}")
    print(f"Report: {output_report}")
    if archive_result:
        print(f"Snapshot: {archive_result['run_dir']}")
        print(
            "Shape library: {path} ({active} active, {total} total)".format(
                path=archive_result["archive_path"],
                active=archive_result["active_count"],
                total=archive_result["total_count"],
            )
        )


if __name__ == "__main__":
    main()
