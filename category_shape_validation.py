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
from product_risk import infer_compliance_risk, infer_fragile_risk, infer_oversize_risk
from product_selection import amazon_listing_url, brand_moat_reason, hard_exclusion_reason
from product_taxonomy import classify_product_form


OUTPUT_FIELDS = [
    "seed_rank",
    "seed_asin",
    "seed_listing_url",
    "seed_title",
    "seed_score",
    "seed_recommendation",
    "seed_asins",
    "seed_count",
    "source_category_id",
    "source_category_name",
    "validation_run_id",
    "category_path",
    "category_health_score",
    "category_health_rank",
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
    "form_dated_count",
    "form_new_entrant_count",
    "form_new_entrant_success_count",
    "form_new_entrant_success_rate",
    "form_new_entrant_median_sales",
    "form_new_entrant_median_reviews",
    "form_brand_dependent_share",
    "form_excluded_share",
    "form_excluded_reasons",
    "form_price_median",
    "form_reference_asins",
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

GENERIC_FORM_NAMES = {
    "unknown",
    "sporting goods",
    "home organizers and storage",
    "home kitchen",
    "sports outdoors",
    "office products",
    "patio lawn garden",
    "tools home improvement",
}

INVALIDATING_FLAGS = {"brand-dependent accessory", "excluded products"}


def parse_args():
    parser = argparse.ArgumentParser(description="Validate Amazon seeds by category and product form.")
    parser.add_argument("--rules", default="config/category_shape_validation_rules.json")
    parser.add_argument("--input", help="Ranked seed CSV. Defaults to rules input.")
    parser.add_argument("--output-csv", help="Validation output CSV.")
    parser.add_argument("--output-report", help="Markdown report output.")
    parser.add_argument("--archive-dir", help="Archive directory.")
    parser.add_argument("--asin", action="append", default=[], help="Manually prioritize an ASIN found in this run's raw Top100 reports. Repeatable.")
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
    limit = int(to_float(rules.get("seed_limit"), 0))
    if limit <= 0:
        return []
    selected = []
    recommendation_contains = str(rules.get("seed_recommendation_contains", "Watch"))
    for rank, row in enumerate(rows, start=1):
        asin = str(row.get("source_asin", "") or "").strip()
        if not asin:
            continue
        if recommendation_contains and recommendation_contains not in str(row.get("recommendation", "")):
            continue
        selected.append({**row, "_seed_rank": rank})
    return selected[:limit]


def by_key(rows, key):
    return {row.get(key, ""): row for row in rows if row.get(key, "")}


def product_form_from_title(title):
    return classify_product_form(title)


def product_form_from_category_product(product, category_path="", category_brands=None):
    return classify_product_form(
        product.get("title", ""),
        category_path,
        product.get("product_category", ""),
        brand=product.get("brand", ""),
        category_brands=category_brands,
    )


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


def normalized_category_product(product, category_path, category_brands=None):
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
        "product_form": product_form_from_category_product(product, category_path, category_brands),
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


DEFAULT_NEW_ENTRANT_THRESHOLDS = {
    "new_entrant_months": 18,
    "new_entrant_min_sales": 200,
    "new_entrant_max_reviews": 300,
    "reference_asin_limit": 5,
}

BRAND_DEPENDENT_PATTERNS = re.compile(
    r"\b(compatible with|compatible for|replacement for|refills? for|fits? (?:for )?(?:all |most )?[a-z0-9]+ (?:brand|models?)|for use with)\b",
    re.IGNORECASE,
)


def listing_age_days(raw_date, now=None):
    raw_date = str(raw_date or "").strip()
    if not raw_date:
        return None
    try:
        listed_at = datetime.strptime(raw_date[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return ((now or datetime.now()) - listed_at).days


def category_brand_names(rows, min_length=4):
    """Brand names seen in the category, used to detect 'compatible with <Brand>' accessories."""
    names = set()
    for row in rows:
        brand = normalize_text(row.get("brand", ""))
        if len(brand) >= min_length and brand not in {"generic", "unbranded", "unknown"}:
            names.add(brand)
    return names


def is_brand_dependent(product, brand_names):
    """True when a listing positions itself as an accessory/refill for another brand.

    Two signals: an explicit 'compatible with / replacement for' phrase, or the
    title naming a *different* brand that also sells in this category
    (e.g. 'Duster Refills Compatible with Swiffer' sold by a third party).
    """

    title = str(product.get("title", "") or "")
    if BRAND_DEPENDENT_PATTERNS.search(title):
        return True
    own_brand = normalize_text(product.get("brand", ""))
    title_norm = f" {normalize_text(title)} "
    for name in brand_names:
        if name == own_brand:
            continue
        if f" {name} " in title_norm:
            return True
    return False


def new_entrant_metrics(form_products, thresholds, now=None):
    months = to_float(thresholds.get("new_entrant_months"), DEFAULT_NEW_ENTRANT_THRESHOLDS["new_entrant_months"])
    min_sales = to_float(thresholds.get("new_entrant_min_sales"), DEFAULT_NEW_ENTRANT_THRESHOLDS["new_entrant_min_sales"])
    max_reviews = to_float(thresholds.get("new_entrant_max_reviews"), DEFAULT_NEW_ENTRANT_THRESHOLDS["new_entrant_max_reviews"])
    reference_limit = int(to_float(thresholds.get("reference_asin_limit"), DEFAULT_NEW_ENTRANT_THRESHOLDS["reference_asin_limit"]))
    cutoff_days = months * 30.5

    dated = []
    for product in form_products:
        age = listing_age_days(product.get("online_date"), now)
        if age is not None:
            dated.append((age, product))
    entrants = [product for age, product in dated if age <= cutoff_days]
    successes = [product for product in entrants if to_float(product.get("monthly_sales"), 0) >= min_sales]
    references = sorted(
        (product for product in successes if to_float(product.get("reviews"), 0) <= max_reviews),
        key=lambda product: -to_float(product.get("monthly_sales"), 0),
    )[:reference_limit]
    return {
        "dated_count": len(dated),
        "new_entrant_count": len(entrants),
        "new_entrant_success_count": len(successes),
        "new_entrant_success_rate": round(len(successes) / len(entrants), 3) if entrants else 0,
        "new_entrant_median_sales": round(med(to_float(p.get("monthly_sales"), 0) for p in entrants), 0) if entrants else 0,
        "new_entrant_median_reviews": round(med(to_float(p.get("reviews"), 0) for p in entrants), 0) if entrants else 0,
        "reference_asins": "; ".join(
            f"{p.get('asin')}:{int(to_float(p.get('monthly_sales'), 0))}/{int(to_float(p.get('reviews'), 0))}"
            for p in references
        ),
    }


def product_exclusion_reason(product, scoring_rules):
    row = {
        "product_name": product.get("title", ""),
        "category": product.get("bsr_category", ""),
        "brand": product.get("brand", ""),
    }
    reason = hard_exclusion_reason(row, scoring_rules) or brand_moat_reason(row, scoring_rules)
    if reason:
        return reason
    title = row["product_name"]
    category = row["category"]
    brand = row["brand"]
    if infer_oversize_risk(title, category, brand, 0) >= 65:
        return "oversize risk"
    if infer_compliance_risk(title, category, brand, 0) >= 80:
        return "compliance risk"
    if infer_fragile_risk(title, category, brand, 0) >= 80:
        return "fragile risk"
    return ""


def form_summary_rows(rows, thresholds=None, now=None, scoring_rules=None):
    thresholds = thresholds or DEFAULT_NEW_ENTRANT_THRESHOLDS
    scoring_rules = scoring_rules or {}
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("product_form") or "unknown"].append(row)
    category_sales = sum(to_float(row.get("monthly_sales"), 0) for row in rows)
    brand_names = category_brand_names(rows)
    summaries = []
    for product_form, form_products in grouped.items():
        exclusion_reasons = [product_exclusion_reason(row, scoring_rules) for row in form_products]
        brand_dependent_flags = [is_brand_dependent(row, brand_names) for row in form_products]
        eligible_products = [
            row
            for row, reason, brand_dependent in zip(form_products, exclusion_reasons, brand_dependent_flags)
            if not reason and not brand_dependent
        ]
        form_sales = [to_float(row.get("monthly_sales"), 0) for row in eligible_products]
        form_reviews = [to_float(row.get("reviews"), 0) for row in eligible_products]
        total_sales = sum(form_sales)
        low_review_high_sales = sum(
            1
            for row in eligible_products
            if to_float(row.get("reviews"), 0) <= 300 and to_float(row.get("monthly_sales"), 0) >= 500
        )
        brand_dependent = sum(brand_dependent_flags)
        exclusion_counts = Counter(reason for reason in exclusion_reasons if reason)
        prices = [to_float(row.get("price"), 0) for row in eligible_products if to_float(row.get("price"), 0) > 0]
        entrant = new_entrant_metrics(eligible_products, thresholds, now)
        summaries.append(
            {
                "product_form": product_form,
                "count": len(eligible_products),
                "direct_count": len(eligible_products),
                "keyword_count": 0,
                "avg_price": round(avg(row.get("price") for row in eligible_products), 2),
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
                "brand_dependent_share": round(brand_dependent / len(form_products), 3) if form_products else 0,
                "excluded_share": round(sum(exclusion_counts.values()) / len(form_products), 3) if form_products else 0,
                "excluded_reasons": "; ".join(f"{reason}:{count}" for reason, count in exclusion_counts.most_common(3)),
                "price_median": round(med(prices), 2) if prices else 0,
                **entrant,
                "top_materials": "",
                "top_pack_counts": "",
                "top_styles": "",
            }
        )
    summaries.sort(key=lambda row: (-to_float(row.get("total_monthly_sales"), 0), row.get("product_form", "")))
    return summaries


def seed_context(seed, all_seeds=None):
    """Seed columns for one category.

    ``seed`` is the representative (best-ranked) seed; ``all_seeds`` lists every
    seed that landed in the same minimum category so the validation card can
    show them without duplicating the whole Top100 analysis per seed.
    """

    all_seeds = [s for s in (all_seeds or [seed]) if s.get("source_asin")]
    asin = seed.get("source_asin", "")
    return {
        "seed_rank": seed.get("_seed_rank", ""),
        "seed_asin": asin,
        "seed_listing_url": (seed.get("listing_url") or amazon_listing_url(asin)) if asin else "",
        "seed_title": seed.get("product_name", ""),
        "seed_score": seed.get("opportunity_score", ""),
        "seed_recommendation": seed.get("recommendation", ""),
        "seed_asins": "; ".join(str(s.get("source_asin", "")).strip() for s in all_seeds if s.get("source_asin")),
        "seed_count": len(all_seeds),
        "source_category_id": seed.get("source_category_id", ""),
        "source_category_name": seed.get("source_category_name", ""),
        "category_health_score": seed.get("category_health_score", ""),
        "category_health_rank": seed.get("category_health_rank", ""),
    }


def seed_forms_in_report(seeds, products):
    forms = set()
    for seed in seeds:
        seed_asin = seed.get("source_asin", "")
        if not seed_asin:
            continue
        seed_product = next((row for row in products if row.get("asin") == seed_asin), None)
        form = seed_product.get("product_form") if seed_product else product_form_from_title(seed.get("product_name", ""))
        if form:
            forms.add(normalize_text(form))
    return forms


def build_rows_from_category_report(seeds, report, rules, run_id=""):
    """Build one row per product form for a category Top100 report.

    ``seeds`` may be a single seed dict (legacy) or the list of all seeds that
    fell into this category. The Top100 is analysed once; seed forms are marked
    ``seed_form`` and every other form ``adjacent_form`` (or ``category_form``
    when the category was picked by health ranking rather than by a seed).
    """

    if isinstance(seeds, dict):
        seeds = [seeds]
    seeds = list(seeds)
    representative = seeds[0]
    category_path = representative.get("source_category_path") or representative.get("category", "")
    raw_products = extract_category_report_products(report)
    category_brands = [product.get("brand", "") for product in raw_products if product.get("brand")]
    products = [normalized_category_product(product, category_path, category_brands) for product in raw_products]
    products = [product for product in products if product.get("asin")]
    if not products:
        return []

    thresholds = rules.get("thresholds", {})
    metrics = category_market_metrics(products)
    seed_forms = seed_forms_in_report(seeds, products)
    has_seed = any(seed.get("source_asin") for seed in seeds)
    rows = []
    for form in form_summary_rows(products, thresholds, scoring_rules=rules.get("_scoring_rules", {})):
        product_form = form.get("product_form") or "unknown"
        if normalize_text(product_form) in seed_forms:
            scope = "seed_form"
        elif has_seed:
            scope = "adjacent_form"
        else:
            scope = "category_form"
        row = {
            **seed_context(representative, seeds),
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
            "form_dated_count": form.get("dated_count", 0),
            "form_new_entrant_count": form.get("new_entrant_count", 0),
            "form_new_entrant_success_count": form.get("new_entrant_success_count", 0),
            "form_new_entrant_success_rate": form.get("new_entrant_success_rate", 0),
            "form_new_entrant_median_sales": form.get("new_entrant_median_sales", 0),
            "form_new_entrant_median_reviews": form.get("new_entrant_median_reviews", 0),
            "form_brand_dependent_share": form.get("brand_dependent_share", 0),
            "form_excluded_share": form.get("excluded_share", 0),
            "form_excluded_reasons": form.get("excluded_reasons", ""),
            "form_price_median": form.get("price_median", 0),
            "form_reference_asins": form.get("reference_asins", ""),
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
    if normalize_text(row.get("product_form")) in GENERIC_FORM_NAMES:
        flags.append("generic form classification")
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

    # New-entrant evidence: among listings launched in the last N months, how
    # many are already selling? This is the most direct answer to "can a new
    # product win in this form", so it carries the weight the old seed-scope
    # bonus used to have.
    dated_count = to_float(row.get("form_dated_count"), 0)
    entrant_count = to_float(row.get("form_new_entrant_count"), 0)
    entrant_success = to_float(row.get("form_new_entrant_success_count"), 0)
    entrant_rate = to_float(row.get("form_new_entrant_success_rate"), 0)
    entrant_min_count = to_float(thresholds.get("new_entrant_min_count"), 2)
    brand_dependent_share = to_float(row.get("form_brand_dependent_share"), 0)
    excluded_share = to_float(row.get("form_excluded_share"), 0)
    form_price_median = to_float(row.get("form_price_median"), 0)

    if dated_count <= 0:
        entrant_score = 50.0  # no listing-age data: neutral, not a penalty
        if data_quality == "category_top100":
            flags.append("no listing-age data")
    elif entrant_count <= 0:
        entrant_score = 0.0
        flags.append("no new entrants")
    elif entrant_count < entrant_min_count:
        entrant_score = entrant_rate * 100 * 0.6  # one data point: discount it
    else:
        entrant_score = entrant_rate * 100
    if entrant_count >= entrant_min_count and entrant_success <= 0:
        flags.append("new entrants not selling")
    if brand_dependent_share >= to_float(thresholds.get("brand_dependent_share"), 0.5):
        flags.append("brand-dependent accessory")
    if excluded_share >= to_float(thresholds.get("form_excluded_share"), 0.5):
        flags.append("excluded products")
    price_min = to_float(thresholds.get("price_min"), 12)
    price_max = to_float(thresholds.get("price_max"), 80)
    if form_price_median and not price_min <= form_price_median <= price_max:
        flags.append("price band outside target")

    demand_score = (
        min(100, form_total_sales / thresholds.get("form_full_total_sales", 3000) * 100) * 0.55
        + min(100, form_median_sales / thresholds.get("form_full_median_sales", 500) * 100) * 0.45
    )
    access_score = max(0, 100 - form_median_reviews / 1000 * 100)
    gap_score = min(100, form_low_gap * 20)
    concentration_score = max(0, 100 - top_brand_share * 100)
    shape_score = round(
        demand_score * 0.25
        + access_score * 0.25
        + gap_score * 0.15
        + entrant_score * 0.25
        + concentration_score * 0.10,
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
        "brand-dependent accessory",
        "new entrants not selling",
        "excluded products",
    }
    # Flags that always reject: an accessory market for someone else's brand is
    # an IP / hijack trap, and a form where recent launches all failed is a
    # closed market regardless of how good the aggregate numbers look.
    always_reject_flags = {"brand-dependent accessory", "new entrants not selling", "excluded products"}
    has_hard_flag = any(flag in hard_flags for flag in flags)
    has_always_reject = any(flag in always_reject_flags for flag in flags)
    has_thin_sample = "thin form sample" in flags
    has_price_band_risk = "price band outside target" in flags
    has_generic_form = "generic form classification" in flags
    has_top100 = data_quality == "category_top100"
    is_seed_form = row.get("shape_scope") == "seed_form"
    allow_adjacent = bool(rules.get("allow_adjacent_shape_opportunity", True))
    adjacent_min_count = to_float(thresholds.get("form_min_count_adjacent"), 3)
    # Adjacent / category-only forms have no seed evidence behind them, so they
    # need a larger sample before they can enter the pool on their own.
    scope_allows_opportunity = is_seed_form or (allow_adjacent and form_count >= adjacent_min_count)

    if not has_top100 and has_hard_flag and form_low_gap < thresholds["form_min_low_review_high_sales"]:
        recommendation = "Reject category/form"
    elif not has_top100:
        recommendation = "Needs category Top100"
    elif has_always_reject:
        recommendation = "Reject category/form"
    elif has_hard_flag and form_low_gap < thresholds["form_min_low_review_high_sales"]:
        recommendation = "Reject category/form"
    elif has_hard_flag:
        recommendation = "Watch shape" if shape_score >= thresholds["watch_shape_score"] else "Reject category/form"
    elif has_thin_sample:
        recommendation = "Watch shape" if shape_score >= thresholds["watch_shape_score"] else "Reject category/form"
    elif has_price_band_risk:
        recommendation = "Watch shape" if shape_score >= thresholds["watch_shape_score"] else "Reject category/form"
    elif has_generic_form:
        recommendation = "Watch shape" if shape_score >= thresholds["watch_shape_score"] else "Reject category/form"
    elif scope_allows_opportunity and shape_score >= thresholds["shape_opportunity_score"]:
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


def entrant_sentence(row):
    entrant_count = int(to_float(row.get("form_new_entrant_count"), 0))
    if entrant_count <= 0:
        return ""
    rate = to_float(row.get("form_new_entrant_success_rate"), 0)
    success = int(to_float(row.get("form_new_entrant_success_count"), 0))
    return f"近 18 个月新上架 {entrant_count} 个，其中 {success} 个已达标（{rate * 100:.0f}%）。"


def opportunity_thesis(row):
    rec = row.get("shape_recommendation", "")
    form = row.get("product_form", "")
    entrant = entrant_sentence(row)
    if rec == "Shape opportunity":
        text = (
            f"{form} 形态已通过类目 Top100 验证：月销均值 {row.get('form_avg_sales')}，"
            f"形态总月销 {row.get('form_total_sales')}，评论中位数 {row.get('form_median_reviews')}，"
            f"低评高销样本 {row.get('form_low_review_high_sales_count')} 个。{entrant}"
        )
        if row.get("form_reference_asins"):
            text += f" 参考新进入者：{row.get('form_reference_asins')}"
        return text
    if rec == "Watch shape":
        return f"{form} 有部分需求信号，但仍需确认评论墙、品牌集中或供应链差异化。{entrant}"
    if rec == "Needs category Top100":
        return f"{form} 目前只是种子入口，还不能判定机会；需要拉最小类目 Top100 后按形态复核。"
    flags = str(row.get("validation_flags", "") or "")
    reason = f"（{flags}）" if flags else ""
    return f"{form} 当前类目/形态竞争结构不适合直接进入机会档案{reason}。"


def next_action(row):
    rec = row.get("shape_recommendation", "")
    if rec == "Shape opportunity":
        if row.get("form_reference_asins"):
            return "从参考新进入者 ASIN 里挑 1-2 个做单品研究，再进供应商验证"
        return "进入单品研究/供应商验证"
    if rec == "Watch shape":
        return "补评论和供应商报价后再判断"
    if rec == "Needs category Top100":
        return "下次类目扫描补齐该最小类目 Top100；不要为此单独重复调用旧 CLI"
    return "淘汰或仅保留历史追溯"


def category_key(seed):
    category_id = str(seed.get("source_category_id", "") or "").strip()
    if category_id:
        return f"id:{category_id}"
    path = normalize_text(seed.get("source_category_path") or seed.get("category", ""))
    return f"path:{path}" if path else ""


def group_seeds_by_category(seeds):
    grouped = {}
    order = []
    for seed in seeds:
        key = category_key(seed)
        if not key:
            key = f"seed:{seed.get('source_asin', '')}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(seed)
    return [(key, grouped[key]) for key in order]


def category_seed_from_row(row, **overrides):
    seed = {
        "source_category_id": row.get("category_id", ""),
        "source_category_name": row.get("name", ""),
        "source_category_path": row.get("path", ""),
        "category": row.get("path", ""),
        "category_health_score": row.get("category_health_score", ""),
        "category_health_rank": row.get("category_health_rank", ""),
    }
    seed.update(overrides)
    return seed


def manual_asin_seeds(asins, discovery_run_dir):
    requested = [str(asin or "").strip().upper() for asin in asins if str(asin or "").strip()]
    if not requested or not discovery_run_dir:
        return []
    run_dir = Path(discovery_run_dir)
    categories = {str(row.get("category_id", "")): row for row in read_csv(run_dir / "categories.csv")}
    found = {}
    for report_path in sorted((run_dir / "raw_category_reports").glob("*.json")):
        category_id = report_path.stem
        try:
            products = extract_category_report_products(load_json(report_path))
        except (json.JSONDecodeError, OSError):
            continue
        for product in products:
            asin = str(product.get("asin", "") or "").strip().upper()
            if asin not in requested or asin in found:
                continue
            category = categories.get(category_id, {"category_id": category_id})
            found[asin] = category_seed_from_row(
                category,
                source_asin=asin,
                listing_url=amazon_listing_url(asin),
                product_name=str(product.get("title", "") or "").strip(),
                recommendation="Manual ASIN",
            )
    for asin in requested:
        if asin not in found:
            print(f"Manual ASIN not found in this discovery run: {asin}")
    return [found[asin] for asin in requested if asin in found]


def load_ranked_categories(rules, discovery_run_dir, exclude_keys=()):
    """Top categories by health score from the discovery report, without seeds.

    Lets the validation step look at categories the single-listing scorer never
    surfaced a seed for. Requires ``category_health_score`` in the report; a
    report without it (older discovery runs) is silently ignored.
    """

    ranking_value = str(rules.get("category_ranking", "") or "").strip()
    limit = int(to_float(rules.get("category_ranking_limit"), 0))
    if not discovery_run_dir:
        return []
    if ranking_value:
        ranking_path = Path(ranking_value)
    else:
        current_run_id = str(os.environ.get("AMZ_WEEKLY_RUN_ID", "") or "").strip()
        current_run_path = Path(rules.get("discovery_runs_root", "archive/discovery_runs")) / current_run_id / "categories.csv"
        # During an offline replay, raw Top100 JSON comes from the archived
        # source run, while category health is recomputed into the current run.
        ranking_path = current_run_path if current_run_id and current_run_path.exists() else Path(discovery_run_dir) / "categories.csv"
    ranking_rows = read_csv(ranking_path)
    if not ranking_rows:
        return []
    scored = [row for row in ranking_rows if row.get("scan_status") == "success"]
    scored.sort(
        key=lambda row: (
            not bool(str(row.get("category_health_score", "") or "").strip()),
            -to_float(row.get("category_health_score"), 0),
            str(row.get("path", "")),
        )
    )
    selected = []
    for row in scored:
        pseudo_seed = category_seed_from_row(row)
        key = category_key(pseudo_seed)
        if not key or key in exclude_keys:
            continue
        if not load_category_report(pseudo_seed, discovery_run_dir):
            continue
        selected.append((key, [pseudo_seed]))
        if limit > 0 and len(selected) >= limit:
            break
    return selected


def build_validation_rows(seeds, deep_rows, rules, discovery_run_dir=None, extra_categories=None):
    deep_by_asin = by_key(deep_rows, "source_asin") if rules.get("allow_deep_dive_fallback") else {}
    rows = []
    validation_run_id = str(os.environ.get("AMZ_WEEKLY_RUN_ID", "") or "").strip()
    if not validation_run_id and discovery_run_dir:
        validation_run_id = Path(discovery_run_dir).name
    category_limit = int(to_float(rules.get("category_limit"), 0))
    groups = group_seeds_by_category(seeds)
    if category_limit > 0:
        groups = groups[:category_limit]
    groups.extend(extra_categories or [])

    for _key, category_seeds in groups:
        representative = category_seeds[0]
        asin = representative.get("source_asin", "")
        category_report = load_category_report(representative, discovery_run_dir)
        if category_report:
            report_rows = build_rows_from_category_report(category_seeds, category_report, rules, validation_run_id)
            rows.extend(report_rows or [build_pending_row(representative, rules)])
            continue
        # No Top100 for this category: fall back per seed as before.
        for seed in category_seeds:
            seed_asin = seed.get("source_asin", "")
            research_dir = active_research_dir(seed_asin, rules)
            if research_dir:
                rows.extend(build_rows_from_research(seed, research_dir, rules))
            elif seed_asin in deep_by_asin:
                rows.append(build_row_from_deep_summary(seed, deep_by_asin[seed_asin], rules))
            else:
                rows.append(build_pending_row(seed, rules))
    recommendation_order = {
        "Shape opportunity": 0,
        "Watch shape": 1,
        "Needs category Top100": 2,
        "Reject category/form": 3,
    }
    rows.sort(
        key=lambda row: (
            recommendation_order.get(row.get("shape_recommendation"), 9),
            -to_float(row.get("shape_score"), 0),
            to_float(row.get("category_health_rank"), 999999),
            row.get("category_path", ""),
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


def category_exclusion_reason(category_path, config_path="config/category_exclusions.json"):
    path = Path(config_path)
    if not path.exists():
        return ""
    category = normalize_text(category_path)
    config = load_json(path)
    for item in config.get("path_contains", []):
        term = normalize_text(item.get("term", ""))
        if term and term in category:
            return f"excluded category: {item.get('term')}"
    return ""


def archived_shape_invalidation_reason(row):
    flags = {part.strip() for part in str(row.get("validation_flags", "")).split(";") if part.strip()}
    invalid_flag = next((flag for flag in INVALIDATING_FLAGS if flag in flags), "")
    if invalid_flag:
        return invalid_flag
    if normalize_text(row.get("product_form")) in GENERIC_FORM_NAMES:
        return "generic product form"
    reason = category_exclusion_reason(row.get("category_path", ""))
    if reason:
        return reason
    seed_title = row.get("seed_title", "")
    if BRAND_DEPENDENT_PATTERNS.search(str(seed_title or "")):
        return "brand-dependent accessory"
    candidate = {
        "product_name": seed_title or row.get("product_form", ""),
        "category": row.get("category_path", ""),
        "brand": "",
    }
    reason = hard_exclusion_reason(candidate, {}) or brand_moat_reason(candidate, {})
    if reason:
        return reason
    if infer_oversize_risk(candidate["product_name"], candidate["category"], "", 0) >= 65:
        return "oversize risk"
    if infer_compliance_risk(candidate["product_name"], candidate["category"], "", 0) >= 80:
        return "compliance risk"
    if infer_fragile_risk(candidate["product_name"], candidate["category"], "", 0) >= 80:
        return "fragile risk"
    return ""


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
        same_run = str(prior.get("archive_last_run_id", "")) == str(run_id)
        merged = {**prior, **row}
        merged.update(
            {
                "shape_archive_key": key,
                "archive_first_seen": prior.get("archive_first_seen") or now,
                "archive_last_seen": now,
                "archive_seen_count": int(to_float(prior.get("archive_seen_count"), 0)) + (0 if same_run else 1),
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
            reason = archived_shape_invalidation_reason(row)
            if reason:
                row["archive_status"] = "invalidated_by_rule"
                note = f"invalidated_by_rule ({run_id}): {reason}"
                existing_notes = str(row.get("archive_notes", "") or "")
                if note not in existing_notes:
                    row["archive_notes"] = "; ".join(part for part in (existing_notes, note) if part)
            else:
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
    if input_path and Path(input_path).is_file():
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
        f"- 观察形态：{sum(1 for row in rows if row.get('shape_recommendation') == 'Watch shape')}",
        f"- 待补 Top100：{sum(1 for row in rows if row.get('shape_recommendation') == 'Needs category Top100')}",
        f"- 覆盖类目：{len({row.get('category_path', '') for row in rows})}",
        "",
        "## 形态排序",
        "",
        "| 种子 | 形态 | 范围 | 分数 | 结论 | 类目/评论墙 | 形态月销 | 形态评论 | 低评高销 | 新进入者成功 | 参考 ASIN | 说明 |",
        "| --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {seed} | {form} | {scope} | {score} | {rec} | {cat_reviews}/{top_reviews} | {sales} | {reviews} | {gap} | {entrant} | {refs} | {thesis} |".format(
                seed=markdown_link(row.get("seed_asin", "") or "(类目排名)", row.get("seed_listing_url", "")),
                form=escape_pipe(row.get("product_form", "")),
                scope=escape_pipe(row.get("shape_scope", "")),
                entrant="{}/{}".format(
                    int(to_float(row.get("form_new_entrant_success_count"), 0)),
                    int(to_float(row.get("form_new_entrant_count"), 0)),
                ),
                refs=escape_pipe(str(row.get("form_reference_asins", "") or "").replace("; ", "<br>")),
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
    input_path = args.input or rules.get("input", "")
    output_csv = args.output_csv or rules["output_csv"]
    output_report = args.output_report or rules["output_report"]
    archive_dir = Path(args.archive_dir or rules["archive_dir"])

    scoring_rules_path = rules.get("scoring_rules_path", "config/scoring_rules.json")
    rules["_scoring_rules"] = load_json(scoring_rules_path) if Path(scoring_rules_path).exists() else {}
    seeds = select_seed_rows(read_csv(input_path), rules) if input_path else []
    deep_rows = read_csv(rules["deep_dive_summary"])
    discovery_run_dir = resolve_discovery_run_dir(rules)
    seeds.extend(manual_asin_seeds(args.asin, discovery_run_dir))
    deduped_seeds = []
    seen_seed_keys = set()
    for seed in seeds:
        key = (category_key(seed), str(seed.get("source_asin", "") or "").upper())
        if key in seen_seed_keys:
            continue
        seen_seed_keys.add(key)
        deduped_seeds.append(seed)
    seeds = deduped_seeds
    seed_category_keys = {key for key, _ in group_seeds_by_category(seeds)}
    ranked_categories = load_ranked_categories(rules, discovery_run_dir, exclude_keys=seed_category_keys)
    rows = build_validation_rows(seeds, deep_rows, rules, discovery_run_dir, extra_categories=ranked_categories)
    write_csv(output_csv, rows, OUTPUT_FIELDS)
    write_report(output_report, rows)
    archive_result = None
    if not args.no_archive:
        archive_result = archive_run(rows, input_path, output_csv, output_report, archive_dir)

    print(f"Manual/legacy seeds: {len(seeds)} across {len(seed_category_keys)} categories")
    print(f"Health-ranked categories without seeds: {len(ranked_categories)}")
    print(f"Validated categories: {len(seed_category_keys) + len(ranked_categories)}")
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
