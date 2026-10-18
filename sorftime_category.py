"""Sorftime category Top100 adapter.

Since 2026-10-13 Sorftime's ``category_report`` tool returns market statistics
only; the Top100 product list moved to ``category_products`` with different
field names. Raw category reports are still stored in the pre-change shape,
``{"data": {"top100_products": [...], "category_stats_report": {...}}}``, so
discovery, replay, shape validation and research keep reading one format.

Fields the new tool no longer returns (``product_category``, ``seller_origin``,
``package_size``, ``gross_profit``) are left empty rather than guessed.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

FORMAT = "sorftime-category-products-2026-10"

# Numeric fields documented as "Value -1: no data for that period".
_NO_DATA_FIELDS = (
    "price", "list_price", "coupon", "monthly_sales_volume", "monthly_sales_amount",
    "yearly_sales_volume", "fba_fee", "fbm_delivery_fee",
)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number == -1 else number


def _unwrap(result: Any) -> Any:
    """Return the payload under ``data`` when the tool wraps it with ``doc``."""
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result


def _days_since(text: str, today: date | None = None) -> int | None:
    try:
        launched = datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return max(0, ((today or date.today()) - launched).days)


def _sub_category_rank(item: dict[str, Any], node_id: str) -> str:
    entries = [entry for entry in item.get("sub_category") or [] if isinstance(entry, (list, tuple)) and len(entry) >= 3]
    match = next((entry for entry in entries if str(entry[1]) == str(node_id)), entries[0] if entries else None)
    if not match:
        return ""
    return f"Category: {match[0]}, Rank: {match[2]}"


def normalize_category_product(item: dict[str, Any], node_id: str = "", today: date | None = None) -> dict[str, Any]:
    """Map one ``category_products`` row to the field names the pipeline reads."""
    row = dict(item)
    for field in _NO_DATA_FIELDS:
        if field in row:
            row[field] = _number(row[field])
    images = item.get("main_image")
    images = [str(url) for url in images if url] if isinstance(images, list) else ([str(images)] if images else [])
    row["main_images"] = images
    row["main_image"] = images[0] if images else ""
    row["review_count"] = item.get("review_count", item.get("rating_count"))
    row["star_rating"] = item.get("star_rating", item.get("ratings"))
    row["seller"] = item.get("seller") or item.get("buybox_seller") or ""
    fba_fee, fbm_fee = row.get("fba_fee") or 0, row.get("fbm_delivery_fee") or 0
    row["delivery_type"] = item.get("delivery_type") or ("FBA" if fba_fee > 0 else "FBM" if fbm_fee > 0 else "")
    row["category_rank"] = item.get("category_rank") or _sub_category_rank(item, node_id)
    if row.get("online_date") and "days_listed" not in item:
        row["days_listed"] = _days_since(row["online_date"], today)
    row.setdefault("product_category", "")
    row.setdefault("seller_origin", "")
    return row


def normalize_category_stats(result: Any) -> dict[str, Any]:
    """Map ``category_report`` statistics to the pre-change stats keys, keeping the new fields too."""
    data = _unwrap(result)
    if not isinstance(data, dict) or "top100_products" in data:
        return {}
    stats = dict(data)
    if "top3_brand_sales_volume_share" in data:
        stats.setdefault("top3_brands_sales_volume_share", data["top3_brand_sales_volume_share"])
    for label, key in (("brand", "brands_list"), ("seller", "sellers_list")):
        rows = [row for row in data.get(key) or [] if isinstance(row, (list, tuple)) and len(row) >= 7]
        for place, row in zip(("first", "second", "third"), rows):
            stats.setdefault(f"{place}_{label}", row[0])
            stats.setdefault(f"{place}_{label}_sales_volume", row[6])
    prices = data.get("category_products_price") or []
    if prices:
        stats.setdefault("average_price", prices[0])
    return stats


def category_products_list(result: Any) -> list[dict[str, Any]]:
    data = _unwrap(result)
    if isinstance(data, dict):
        data = data.get("top100_products") or data.get("products") or []
    return [item for item in data or [] if isinstance(item, dict)]


def build_category_report(node_id: str, products_result: Any, stats_result: Any = None,
                          today: date | None = None) -> dict[str, Any]:
    products = [normalize_category_product(item, node_id, today) for item in category_products_list(products_result)]
    products = [item for item in products if item.get("asin")]
    return {
        "data": {
            "top100_products": products,
            "category_stats_report": normalize_category_stats(stats_result) if stats_result is not None else {},
        },
        "source": {"format": FORMAT, "node_id": str(node_id),
                   "tools": ["category_products"] + (["category_report"] if stats_result is not None else [])},
    }


def fetch_category_report(client: Any, node_id: str, site: str = "US", with_stats: bool = False) -> dict[str, Any]:
    """Fetch the Top100 (one call) and optionally the market statistics (one more call)."""
    arguments = {"node_id": str(node_id), "amz_site": site}
    products = client.call_tool("category_products", arguments)
    stats = client.call_tool("category_report", arguments) if with_stats else None
    return build_category_report(str(node_id), products, stats)


def report_has_products(report: Any) -> bool:
    return bool(category_products_list(report))


def report_has_stats(report: Any) -> bool:
    data = _unwrap(report)
    return isinstance(data, dict) and bool(data.get("category_stats_report"))
