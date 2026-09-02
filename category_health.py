#!/usr/bin/env python3
"""Category-level health score for new-product discovery.

Computed once per scanned category from the raw Top100 report, so the weekly
scan can rank *all* categories it paid an MCP call for, instead of only looking
at the handful a single-listing seed happened to come from.

The score answers "would a new product from a small seller have a chance here":
recent launches that already sell, low review walls, no dominant brand, no
sales concentration, and a price band a small seller can work in.
"""

from __future__ import annotations

from category_shape_validation import (
    category_market_metrics,
    normalized_category_product,
    to_float,
)


CATEGORY_HEALTH_FIELDS = [
    "category_total_sales",
    "category_median_reviews",
    "category_top10_median_reviews",
    "category_top_brand",
    "category_top_brand_share",
    "category_top10_sales_share",
    "category_low_review_high_sales_count",
    "category_new_listing_share_12m",
    "category_cn_hk_seller_share",
    "category_fba_share",
    "category_price_median",
    "category_health_score",
    "category_health_flags",
    "category_health_rank",
]


DEFAULT_HEALTH_RULES = {
    "price_min": 12,
    "price_max": 80,
    "review_wall_median": 1000,
    "top10_review_wall": 1500,
    "top_brand_share": 0.35,
    "top10_sales_share": 0.7,
    "full_new_listing_share": 0.3,
    "full_low_review_high_sales": 6,
    "concentration_min_count": 20,
}


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def price_fit_score(price_median, price_min, price_max):
    if price_median <= 0:
        return 50.0
    if price_min <= price_median <= price_max:
        return 100.0
    if price_median < price_min:
        return clamp(price_median / price_min * 100)
    return clamp(100 - (price_median - price_max) / price_max * 100)


def category_health_from_records(records, category_path="", rules=None):
    """Return health metrics for one category from its raw Top100 records."""

    rules = {**DEFAULT_HEALTH_RULES, **(rules or {})}
    products = [normalized_category_product(item, category_path) for item in records if isinstance(item, dict)]
    products = [product for product in products if product.get("asin")]
    if not products:
        return {}
    metrics = category_market_metrics(products)

    median_reviews = to_float(metrics.get("category_median_reviews"), 0)
    top10_reviews = to_float(metrics.get("category_top10_median_reviews"), 0)
    top_brand_share = to_float(metrics.get("category_top_brand_share"), 0)
    top10_sales_share = to_float(metrics.get("category_top10_sales_share"), 0)
    new_share = to_float(metrics.get("category_new_listing_share_12m"), 0)
    low_gap = to_float(metrics.get("category_low_review_high_sales_count"), 0)
    price_median = to_float(metrics.get("category_price_median"), 0)
    dated = any(product.get("online_date") for product in products)

    entrant_score = clamp(new_share / rules["full_new_listing_share"] * 100) if dated else 50.0
    access_score = clamp(100 - median_reviews / rules["review_wall_median"] * 100)
    gap_score = clamp(low_gap / rules["full_low_review_high_sales"] * 100)
    brand_score = clamp(100 - top_brand_share * 100)
    # Top10 share is only meaningful once the sample is large enough; with a
    # handful of products the top 10 trivially own everything.
    enough_for_concentration = len(products) >= rules["concentration_min_count"]
    concentration_score = (
        clamp(100 - max(0.0, top10_sales_share - 0.4) / 0.4 * 100) if enough_for_concentration else 100.0
    )
    price_score = price_fit_score(price_median, rules["price_min"], rules["price_max"])
    health = round(
        entrant_score * 0.25
        + access_score * 0.20
        + gap_score * 0.20
        + brand_score * 0.15
        + concentration_score * 0.10
        + price_score * 0.10,
        1,
    )

    flags = []
    if not dated:
        flags.append("no listing-age data")
    if median_reviews >= rules["review_wall_median"]:
        flags.append("category review wall")
    if top10_reviews >= rules["top10_review_wall"]:
        flags.append("top10 review wall")
    if top_brand_share >= rules["top_brand_share"]:
        flags.append("brand concentration")
    if enough_for_concentration and top10_sales_share >= rules["top10_sales_share"]:
        flags.append("sales concentration")
    if price_median and (price_median < rules["price_min"] or price_median > rules["price_max"]):
        flags.append("price band outside target")

    return {
        "category_total_sales": metrics.get("category_total_sales", 0),
        "category_median_reviews": metrics.get("category_median_reviews", 0),
        "category_top10_median_reviews": metrics.get("category_top10_median_reviews", 0),
        "category_top_brand": metrics.get("category_top_brand", ""),
        "category_top_brand_share": metrics.get("category_top_brand_share", 0),
        "category_top10_sales_share": metrics.get("category_top10_sales_share", 0),
        "category_low_review_high_sales_count": metrics.get("category_low_review_high_sales_count", 0),
        "category_new_listing_share_12m": metrics.get("category_new_listing_share_12m", 0),
        "category_cn_hk_seller_share": metrics.get("category_cn_hk_seller_share", 0),
        "category_fba_share": metrics.get("category_fba_share", 0),
        "category_price_median": metrics.get("category_price_median", 0),
        "category_health_score": health,
        "category_health_flags": "; ".join(flags),
    }


def rank_categories_by_health(categories):
    """Assign category_health_rank (1 = best) across successfully scanned categories."""

    scored = [
        category
        for category in categories
        if category.get("scan_status") == "success" and category.get("category_health_score") not in (None, "")
    ]
    scored.sort(key=lambda category: -to_float(category.get("category_health_score"), 0))
    for rank, category in enumerate(scored, start=1):
        category["category_health_rank"] = rank
    return scored
