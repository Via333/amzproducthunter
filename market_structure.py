#!/usr/bin/env python3
"""Reusable category and product-form market structure metrics."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import median


def number(value: object) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except ValueError:
        return 0.0


def percentile(values: list[float], quantile: float) -> float:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return 0.0
    position = (len(clean) - 1) * min(1.0, max(0.0, quantile))
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    fraction = position - lower
    return clean[lower] * (1 - fraction) + clean[upper] * fraction


def parse_date(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        timestamp = int(text)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
        except (OSError, ValueError):
            return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[start][1]:
            end += 1
        average_rank = (start + end) / 2 + 1
        for index in range(start, end + 1):
            ranks[ordered[index][0]] = average_rank
        start = end + 1
    return ranks


def correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 3 or len(left) != len(right):
        return 0.0
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right)
    )
    return numerator / denominator if denominator else 0.0


def sales_share(sorted_sales: list[float], count: int) -> float:
    total = sum(sorted_sales)
    return sum(sorted_sales[:count]) / total if total else 0.0


def price_band_rows(rows: list[dict]) -> list[dict[str, object]]:
    bands = [
        ("under_15", 0, 15),
        ("15_to_25", 15, 25),
        ("25_to_40", 25, 40),
        ("40_to_60", 40, 60),
        ("60_plus", 60, float("inf")),
    ]
    total_sales = sum(number(row.get("monthly_sales")) for row in rows)
    output = []
    for label, low, high in bands:
        items = [row for row in rows if low <= number(row.get("price")) < high]
        sales = sum(number(row.get("monthly_sales")) for row in items)
        reviews = [number(row.get("reviews")) for row in items]
        output.append(
            {
                "price_band": label,
                "listing_count": len(items),
                "monthly_sales": round(sales),
                "sales_share": round(sales / total_sales, 4) if total_sales else 0,
                "median_reviews": round(median(reviews)) if reviews else 0,
            }
        )
    return output


def analyze_market(rows: list[dict], now: datetime | None = None) -> dict[str, object]:
    usable = [row for row in rows if row.get("asin")]
    now = now or datetime.now()
    sales = sorted((number(row.get("monthly_sales")) for row in usable), reverse=True)
    prices = [number(row.get("price")) for row in usable if number(row.get("price")) > 0]
    review_sales_pairs = [
        (number(row.get("reviews")), number(row.get("monthly_sales")))
        for row in usable
        if number(row.get("reviews")) >= 0 and number(row.get("monthly_sales")) > 0
    ]
    listing_dates = [parse_date(row.get("listing_date")) for row in usable]
    listing_dates = [value for value in listing_dates if value]
    new_12m = sum(1 for value in listing_dates if (now - value).days <= 365)
    new_24m = sum(1 for value in listing_dates if (now - value).days <= 730)
    review_ranks = rank([pair[0] for pair in review_sales_pairs])
    sales_ranks = rank([pair[1] for pair in review_sales_pairs])
    return {
        "sample_count": len(usable),
        "total_monthly_sales": round(sum(sales)),
        "median_monthly_sales": round(median(sales)) if sales else 0,
        "top10_sales_share": round(sales_share(sales, 10), 4),
        "top20_sales_share": round(sales_share(sales, 20), 4),
        "top50_sales_share": round(sales_share(sales, 50), 4),
        "review_sales_spearman": round(correlation(review_ranks, sales_ranks), 3),
        "price_p25": round(percentile(prices, 0.25), 2),
        "price_median": round(percentile(prices, 0.5), 2),
        "price_p75": round(percentile(prices, 0.75), 2),
        "listing_date_coverage": round(len(listing_dates) / len(usable), 4) if usable else 0,
        "new_listing_share_12m": round(new_12m / len(listing_dates), 4) if listing_dates else 0,
        "new_listing_share_24m": round(new_24m / len(listing_dates), 4) if listing_dates else 0,
        "price_bands": price_band_rows(usable),
    }
