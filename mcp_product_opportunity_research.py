#!/usr/bin/env python3
"""Build one archived product research dataset from Sorftime MCP and cached Top100 data."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from business_feasibility import QUOTE_FIELDS, quote_rows_from_1688, write_business_feasibility
from category_shape_validation import product_form_from_category_product
from demand_analysis import DEMAND_FIELDS, analyze_demand
from market_structure import analyze_market
from product_opportunity_research import (
    FORM_FIELDS,
    IMAGE_FIELDS,
    KEYWORD_FIELDS,
    PRODUCT_FIELDS,
    REVIEW_FIELDS,
    REVIEW_TARGET_FIELDS,
    apply_visual_labels,
    attach_review_counts,
    build_form_rows,
    closure,
    feature_tags,
    material_with_evidence,
    pack_count,
    prepare_image_assets,
    style,
    use_case,
    write_csv,
)
from sorftime_mcp_client import SorftimeMcpClient
from research_required_checks import checks_markdown, read_products, refresh_required_checks
from product_family import product_identity
from supplier_screening import collect_supplier_screening


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one product research run with Sorftime MCP.")
    parser.add_argument("--asin", required=True)
    parser.add_argument("--site", default="US")
    parser.add_argument("--rules", default="config/opportunity_research_rules.json")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--review-limit", type=int, default=5)
    parser.add_argument("--rebuild-cached", action="store_true", help="Rebuild analysis from saved sources without data calls.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_data(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", "").replace("$", "").strip() or default)
    except (TypeError, ValueError):
        return default


def embedded_number(value: Any, *, percent: bool = False) -> float:
    matches = re.findall(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    if not matches:
        return 0.0
    number = float(matches[-1])
    return number / 100 if percent else number


def first_image(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        images = json.loads(text)
    except json.JSONDecodeError:
        return text
    return str(images[0]) if isinstance(images, list) and images else ""


def duster_supplier_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep disposable duster refill listings and reject handles/complete kits."""
    matches = []
    for row in rows:
        title = str(row.get("title") or "")
        is_duster = any(term in title for term in ("除尘掸", "静电掸", "除尘刷头", "掸头", "除尘纸"))
        is_refill = any(term in title for term in ("替换", "补充", "刷头", "掸头", "一次性"))
        is_kit = any(term in title for term in ("伸缩杆", "手柄", "整套", "套装含杆"))
        if is_duster and is_refill and not is_kit:
            matches.append(row)
    return matches


def research_profile(product: dict[str, Any]) -> str:
    """Return the small set of profiles that need product-specific validation."""

    title = str(product.get("title") or "").lower()
    form = str(product.get("product_form") or deep_product_form(product) or "").lower()
    if "duster refill" in form or ("duster" in title and "refill" in title):
        return "duster_refill"
    if form == "measuring cup" or any(term in title for term in ("jigger", "measuring shot", "liquor measure")):
        return "bar_measuring_tool"
    if form in {"landing fishing net", "cast fishing net"}:
        return form.replace(" ", "_")
    if "rolling pin" in form or "rolling pin" in title or "dough roller" in title:
        return "rolling_pin"
    return "generic"


def supplier_matches(
    rows: list[dict[str, Any]],
    seed: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """Keep traceable image-search candidates without claiming an exact SKU match."""

    profile = research_profile(seed)
    if profile == "duster_refill":
        return (
            duster_supplier_matches(rows),
            "以 Amazon 主图搜索；标题匹配一次性除尘掸替换头并排除手柄/整套。",
        )
    if profile == "bar_measuring_tool":
        terms = ("量酒器", "量酒杯", "调酒量杯", "鸡尾酒量杯", "盎司杯", "jigger")
        matched = [row for row in rows if any(term in str(row.get("title") or "").lower() for term in terms)]
        return (
            matched,
            "以 Amazon 主图搜索；标题须明确匹配调酒量酒器/双头量杯，仍需核对容量、材质、件数和具体 SKU 价。",
        )
    if profile == "rolling_pin":
        terms = ("擀面杖", "擀面棍", "面粉棍", "rolling pin", "dough roller")
        matched = [row for row in rows if any(term in str(row.get("title") or "").lower() for term in terms)]
        return matched, "标题匹配擀面杖，仅作为询价线索；须继续核对具体 SKU 的长度、直径、材质牌号、结构与件数。"
    if profile in {"landing_fishing_net", "cast_fishing_net"}:
        terms = ("抄网", "捞鱼网") if profile == "landing_fishing_net" else ("手抛网", "撒网", "美式飞盘网")
        excluded = ("乒乓", "捕鸟", "网兜替换", "仅网头", "不含杆")
        if profile == "cast_fishing_net":
            excluded += ("飞盘手抛", "轮胎", "钢圈", "铅坠", "纯铅")
        matched = [row for row in rows if any(term in str(row.get("title") or "") for term in terms)
                   and not any(term in str(row.get("title") or "") for term in excluded)]
        return matched, "图片搜索的同形态线索；未核对尺寸、网目、坠重/材质、锁扣和包装，不是有效同 SKU 报价。"
    return [], "图片搜索结果尚未完成同形态标题核验，不作为同款成本证据。"


def latest_cached_category_report(node_id: str) -> Path | None:
    matches = list(ROOT.glob(f"archive/discovery_runs/*/raw_category_reports/{node_id}.json"))
    matches.extend(ROOT.glob(f"archive/cache/category_reports/{node_id}.json"))
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def category_report(client: SorftimeMcpClient, node_id: str, site: str) -> tuple[dict[str, Any], Path]:
    cached = latest_cached_category_report(node_id)
    if cached:
        return load_json(cached), cached
    result = client.call_tool("category_report", {"node_id": node_id, "amz_site": site})
    report = result if isinstance(result, dict) else {"data": result}
    cache_path = ROOT / "archive" / "cache" / "category_reports" / f"{node_id}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, cache_path


def local_category_node_id(
    asin: str,
    candidate_path: Path | None = None,
    validation_path: Path | None = None,
) -> str:
    """Recover a minimum-category node from existing discovery evidence."""

    target = asin.strip().upper()
    candidate_path = candidate_path or ROOT / "data" / "discovered_candidates.csv"
    validation_path = validation_path or ROOT / "data" / "category_shape_validation.csv"

    for row in read_products(candidate_path):
        row_asin = str(row.get("source_asin") or row.get("asin") or "").strip().upper()
        node_id = str(row.get("source_category_id") or "").strip()
        if row_asin == target and node_id:
            return node_id

    matches: list[tuple[float, str]] = []
    for row in read_products(validation_path):
        references = {
            token.split(":", 1)[0].strip().upper()
            for token in str(row.get("form_reference_asins") or row.get("reference_asins") or "").split(";")
            if token.strip()
        }
        seed_asin = str(row.get("seed_asin") or "").strip().upper()
        node_id = str(row.get("source_category_id") or "").strip()
        if node_id and target in references | {seed_asin}:
            matches.append((to_float(row.get("shape_score")), node_id))
    return max(matches, default=(0.0, ""))[1]


def deep_product_form(product: dict[str, Any]) -> str:
    title = str(product.get("title") or "").lower()
    brand = str(product.get("brand") or "").lower()
    broad_form = product_form_from_category_product(product)
    if broad_form != "duster refill":
        return product_identity(title, form=broad_form)["product_form"]
    if "reusable" in title or "washable" in title:
        return "reusable duster refill"
    if any(term in title for term in ("handle", "extension pole", "extendable", "starter kit")):
        return "duster refill kit"
    if brand == "swiffer":
        return "brand duster refill"
    return "compatible disposable refill"


def raw_row(
    seed_asin: str,
    product: dict[str, Any],
    category_name: str,
    target_form: str,
) -> dict[str, Any]:
    asin = str(product.get("asin") or "")
    title = str(product.get("title") or "")
    form = deep_product_form(product)
    material, material_evidence, detail_material, detail_evidence = material_with_evidence(title, "")
    if "microfiber" in title.lower() or "microfibre" in title.lower():
        material = "microfiber"
        material_evidence = "title:microfiber"
    identity = product_identity(title, category_name, form)
    direct = form == target_form and identity["classification_status"] not in {"unresolved", "needs_specification"}
    return {
        "source_asin": seed_asin,
        "source": "category_top100",
        "listing_url": f"https://www.amazon.com/dp/{asin}",
        "product_type": product.get("product_category", ""),
        "relevance_score": 100 if asin == seed_asin else (90 if direct else 65),
        "competitor_type": "seed" if asin == seed_asin else ("direct" if direct else "keyword"),
        "product_form": form,
        **{key: value for key, value in identity.items()
           if key != "product_form"},
        "material": material,
        "material_evidence": material_evidence,
        "detail_material": detail_material,
        "detail_evidence": detail_evidence,
        "pack_count": pack_count(title),
        "closure": closure(title),
        "style": style(title),
        "use_case": use_case(title),
        "feature_tags": feature_tags(title),
        "asin": asin,
        "parent_asin": "",
        "title": title,
        "brand": product.get("brand", ""),
        "price": round(to_float(product.get("price")), 2),
        "monthly_sales": round(to_float(product.get("monthly_sales_volume"))),
        "reviews": round(to_float(product.get("review_count"))),
        "rating": round(to_float(product.get("star_rating")), 1),
        "seller_address": product.get("seller_origin", ""),
        "is_fba": "FBA" in str(product.get("delivery_type") or ""),
        "variation_count": 0,
        "listing_date": product.get("online_date", ""),
        "bsr_category": f"{category_name} / {product.get('category_rank', '')}",
        "main_image_url": "",
        "image_file": "",
        "visual_product_form": "",
        "visual_material_signal": "",
        "visual_pack_count": "",
        "visual_closure": "",
        "visual_style": "",
        "visual_notes": "",
    }


def enrich_row(row: dict[str, Any], detail: dict[str, Any]) -> None:
    if not detail:
        return
    row["parent_asin"] = detail.get("parent_asin", row.get("parent_asin", ""))
    row["main_image_url"] = first_image(detail.get("main_image"))
    row["variation_count"] = detail.get("variation_count", row.get("variation_count", 0))
    row["listing_date"] = detail.get("online_date", row.get("listing_date", ""))
    row["is_fba"] = str(detail.get("delivery_type", "")).upper().endswith("FBA")
    row["seller_address"] = detail.get("seller_name", row.get("seller_address", ""))
    if row.get("asin") == detail.get("asin"):
        row["title"] = detail.get("title") or row.get("title", "")
        row["price"] = detail.get("price", row.get("price", 0))
        row["monthly_sales"] = detail.get("monthly_sales_volume", row.get("monthly_sales", 0))
        row["reviews"] = detail.get("review_count", row.get("reviews", 0))
        row["rating"] = detail.get("star_rating", row.get("rating", 0))


def select_review_rows(rows: list[dict[str, Any]], seed_asin: str, limit: int) -> list[dict[str, Any]]:
    seed = next(row for row in rows if row.get("asin") == seed_asin)
    direct = [row for row in rows if row.get("competitor_type") == "direct" and row.get("asin") != seed_asin]
    adjacent = [row for row in rows if row.get("competitor_type") == "keyword"]
    seed_family = product_identity(seed.get("title", ""), form=seed.get("product_form", ""))["product_family"]
    adjacent = [row for row in adjacent if not seed_family.startswith("unresolved:") and product_identity(row.get("title", ""), form=row.get("product_form", ""))["product_family"] == seed_family]
    selected = [seed]
    seen = {seed_asin}

    def add(row: dict[str, Any] | None) -> None:
        if row and row.get("asin") not in seen and len(selected) < limit:
            selected.append(row)
            seen.add(str(row.get("asin")))

    add(max(direct, key=lambda row: to_float(row.get("monthly_sales")), default=None))
    # Reserve review coverage for alternatives before spending the whole budget on one form.
    alternatives = sorted(adjacent, key=lambda row: -to_float(row.get("monthly_sales")))
    seen_forms = set()
    for row in alternatives:
        form = row.get("product_form")
        if form not in seen_forms and len(seen_forms) < 2:
            add(row)
            seen_forms.add(form)
    add(min((row for row in direct if to_float(row.get("monthly_sales")) >= 500), key=lambda row: to_float(row.get("reviews")), default=None))
    seed_pack = to_float(seed.get("pack_count"))
    add(min(direct, key=lambda row: (abs(to_float(row.get("pack_count")) - seed_pack), -to_float(row.get("monthly_sales"))), default=None))
    add(min(direct, key=lambda row: (to_float(row.get("rating"), 5), -to_float(row.get("monthly_sales"))), default=None))
    for form in dict.fromkeys(str(row.get("product_form") or "") for row in adjacent):
        add(max((row for row in adjacent if row.get("product_form") == form), key=lambda row: to_float(row.get("monthly_sales")), default=None))
    for row in sorted(direct, key=lambda item: (-to_float(item.get("monthly_sales")), to_float(item.get("reviews")))):
        add(row)
    return selected[:limit]


def review_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "asin": row["asin"],
            "listing_url": row["listing_url"],
            "title": row["title"],
            "competitor_type": row["competitor_type"],
            "product_form": row["product_form"],
            "monthly_sales": row["monthly_sales"],
            "reviews": row["reviews"],
            "rating": row["rating"],
            "review_rows_collected": 0,
        }
        for row in rows
    ]


def review_pain_tags(text: str, profile: str = "generic") -> str:
    lowered = text.lower()
    definitions = {
        "measurement_accuracy": ["accurate", "accuracy", "measure correctly", "measurement", "ounce", "milliliter", " ml", " oz"],
        "marking_readability": ["marking", "etched", "engraved", "numbers", "hard to read", "darker", "reversed"],
        "material_quality": ["stainless", "steel", "rust", "quality", "flimsy", "bend", "sharp edge"],
        "stability_handling": ["stand flat", "stable", "tip over", "grip", "hold liquid", "pour"],
        "cleaning": ["dishwasher", "wash", "clean", "stain"],
        "size_fit": ["size", "small", "large", "fit"],
        "value": ["value", "price", "cheap", "expensive", "affordable"],
        "durability": ["durable", "tear", "rip", "break", "indestructible"],
    }
    if profile == "duster_refill":
        definitions.update({
            "dust_retention": ["dust", "trap", "attract", "lock in"],
            "fit": ["fit", "handle", "secure", "lock"],
            "fiber_shedding": ["shed", "fiber", "fall apart", "fluffy"],
            "scent": ["scent", "smell", "unscented", "odor"],
        })
    return "; ".join(label for label, terms in definitions.items() if any(term in lowered for term in terms))


def collect_reviews(
    client: SorftimeMcpClient,
    rows: list[dict[str, Any]],
    site: str,
    profile: str = "generic",
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    form_by_asin = {str(row["asin"]): str(row["product_form"]) for row in rows}
    for target in rows:
        asin = str(target["asin"])
        result = client.call_tool("product_reviews", {"asin": asin, "amz_site": site, "review_type": "Both"})
        items = as_data(result)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            rating = to_float(item.get("star_rating"))
            collected.append(
                {
                    "review_target_asin": asin,
                    "asin": asin,
                    "listing_url": f"https://www.amazon.com/dp/{asin}",
                    "rating": rating,
                    "review_title": item.get("title", ""),
                    "review_text": content,
                    "review_date": item.get("review_date", ""),
                    "asin_property": item.get("variant_attribute", ""),
                    "review_link": "",
                    "pain_point_tags": review_pain_tags(f"{item.get('title', '')} {content}", profile),
                    "verified_purchase": "",
                    "helpful": 0,
                }
            )
    return collected


def keyword_rows(items: Any) -> list[dict[str, Any]]:
    rows = []
    for rank, item in enumerate(items if isinstance(items, list) else [], start=1):
        rows.append(
            {
                "keyword": item.get("keyword", ""),
                "search_volume": item.get("monthly_search_volume", 0),
                "rank": rank,
                "show_share": item.get("exposure_position", ""),
                "clicks_90d": "",
                "top3_asins": item.get("latest_organic_position", ""),
                "image_asin_count": item.get("recommended_bid", ""),
            }
        )
    return rows


def parse_month_values(items: Any) -> list[dict[str, Any]]:
    parsed = []
    for item in items if isinstance(items, list) else []:
        match = re.match(r"(.+?)=(.+)", str(item))
        if match:
            parsed.append({"month": match.group(1), "value": to_float(match.group(2))})
    return parsed


def shape_snapshot(form_rows: list[dict[str, Any]], seed_form: str) -> dict[str, Any]:
    return next((row for row in form_rows if row.get("product_form") == seed_form), {})


def relativize_image_paths(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        path_text = str(row.get("image_file") or "")
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_absolute():
            continue
        try:
            row["image_file"] = str(path.relative_to(ROOT))
        except ValueError:
            pass


def broad_refill_snapshot(raw_products: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in raw_products if product_form_from_category_product(row) == "duster refill"]
    sales = [to_float(row.get("monthly_sales_volume")) for row in rows]
    reviews = [to_float(row.get("review_count")) for row in rows]
    total_category_sales = sum(to_float(row.get("monthly_sales_volume")) for row in raw_products)
    return {
        "name": "duster refill",
        "count": len(rows),
        "total_monthly_sales": round(sum(sales)),
        "sales_share": round(sum(sales) / total_category_sales, 4) if total_category_sales else 0,
        "median_reviews": round(median(reviews)) if reviews else 0,
        "low_review_high_sales_count": sum(
            1
            for row in rows
            if to_float(row.get("review_count")) <= 300 and to_float(row.get("monthly_sales_volume")) >= 500
        ),
    }


def form_snapshot(raw_products: list[dict[str, Any]], product_form: str) -> dict[str, Any]:
    rows = [row for row in raw_products if deep_product_form(row) == product_form]
    sales = [to_float(row.get("monthly_sales_volume")) for row in rows]
    reviews = [to_float(row.get("review_count")) for row in rows]
    total_category_sales = sum(to_float(row.get("monthly_sales_volume")) for row in raw_products)
    return {
        "name": product_form,
        "count": len(rows),
        "total_monthly_sales": round(sum(sales)),
        "sales_share": round(sum(sales) / total_category_sales, 4) if total_category_sales else 0,
        "median_reviews": round(median(reviews)) if reviews else 0,
        "low_review_high_sales_count": sum(
            1
            for row in rows
            if to_float(row.get("review_count")) <= 300 and to_float(row.get("monthly_sales_volume")) >= 500
        ),
    }


def make_summary(
    seed: dict[str, Any],
    detail: dict[str, Any],
    category_stats: dict[str, Any],
    seed_shape: dict[str, Any],
    trends: dict[str, Any],
    variations: list[dict[str, Any]],
    customers_say: dict[str, Any],
    keywords: list[dict[str, Any]],
    raw_products: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
    category_source: str,
    tool_calls: int,
) -> dict[str, Any]:
    price = to_float(seed.get("price"))
    fba_fee = to_float(detail.get("fba_fee"))
    referral_fee = price * 0.15
    ad_rate = 0.15
    target_margin = 0.20
    max_landed_cost = max(0.0, price - fba_fee - referral_fee - price * ad_rate - price * target_margin)
    no_landed_cost_room = bool(price and max_landed_cost <= 0)
    seed_form = str(seed.get("product_form") or "unknown")
    profile = research_profile(seed)
    details = customers_say.get("details") if isinstance(customers_say, dict) else []
    details = [item for item in (details or []) if isinstance(item, dict)]
    brand = str(seed.get("brand") or "").strip().lower()
    trademark_terms = [
        row for row in keywords
        if brand and len(brand) >= 4 and brand in str(row.get("keyword", "")).lower()
    ]
    seed_pack = pack_count(str(seed.get("title") or ""))
    same_pack = [
        row
        for row in raw_products
        if str(row.get("asin") or "") != str(seed.get("asin") or "")
        and pack_count(str(row.get("title") or "")) == seed_pack
        and deep_product_form(row) == seed_form
    ]
    benchmark = max(same_pack, key=lambda row: to_float(row.get("monthly_sales_volume")), default={})
    if profile == "duster_refill":
        broad_shape = broad_refill_snapshot(raw_products)
    else:
        broad_shape = {
            "name": seed_form,
            "count": seed_shape.get("count", 0),
            "total_monthly_sales": seed_shape.get("total_monthly_sales", 0),
            "sales_share": seed_shape.get("sales_share", 0),
            "median_reviews": seed_shape.get("median_reviews", 0),
            "low_review_high_sales_count": seed_shape.get("low_review_high_sales_count", 0),
        }
    monthly_sales = parse_month_values(trends.get("sales", []))
    prices = parse_month_values(trends.get("price", []))
    growth = 0.0
    if len(monthly_sales) >= 2 and monthly_sales[-2]["value"]:
        growth = monthly_sales[-1]["value"] / monthly_sales[-2]["value"] - 1
    volume = 1.0
    dimensions = [to_float(value) for value in str(detail.get("package_size_cm") or "").split("*") if value]
    if len(dimensions) == 3:
        volume = dimensions[0] * dimensions[1] * dimensions[2] / 1000
    shape_sales = to_float(broad_shape.get("total_monthly_sales"))
    low_review_high_sales = int(to_float(broad_shape.get("low_review_high_sales_count")))
    form_count = int(to_float(broad_shape.get("count")))
    top3_brand_share = embedded_number(category_stats.get("top3_brands_sales_volume_share"), percent=True)
    fba_share = fba_fee / price if price else 0
    review_tags = Counter(
        tag.strip()
        for row in review_rows
        for tag in str(row.get("pain_point_tags") or "").split(";")
        if tag.strip()
    )
    top_review_tags = review_tags.most_common(3)

    score = 35
    score += 12 if shape_sales >= 10000 else (7 if shape_sales >= 3000 else 0)
    score += 8 if low_review_high_sales >= 2 else (4 if low_review_high_sales == 1 else 0)
    score += 10 if to_float(seed.get("monthly_sales")) >= 1000 else (5 if to_float(seed.get("monthly_sales")) >= 300 else 0)
    score += 5 if 0 < to_float(seed.get("reviews")) <= 300 else 0
    score += 3 if to_float(seed.get("rating")) >= 4.3 else 0
    score += 5 if form_count >= 5 else 0
    score -= 10 if fba_share >= 0.40 else (6 if fba_share >= 0.30 else 0)
    score -= 8 if price and price < 12 else 0
    score -= 8 if top3_brand_share >= 0.60 else 0
    score -= 5 if len(trademark_terms) >= 8 else 0
    score -= 3 if profile == "bar_measuring_tool" else 0
    score = max(0, min(100, score))

    if score >= 70:
        decision = "进入供应商验证，不直接立项"
    elif score >= 55:
        decision = "有条件进入供应商验证，不直接立项"
    else:
        decision = "暂不进入供应商验证"

    if no_landed_cost_room:
        margin_warning = f"售价 ${price:.2f} 低于 FBA ${fba_fee:.2f}，尚未计采购成本就已亏损"
    elif price:
        margin_warning = f"售价 ${price:.2f}、FBA 占比 {fba_share:.1%}，倒推完税到仓成本上限仅 ${max_landed_cost:.2f}"
    else:
        margin_warning = "售价或 FBA 数据不足"
    decision_reason = (
        f"{seed_form} 形态有 {shape_sales:,.0f} 件/月需求和 {low_review_high_sales} 个低评高销样本；"
        f"但{margin_warning}，利润必须以 1688 同规格报价复核。"
    )
    if profile == "bar_measuring_tool":
        decision_reason += " 该产品直接接触饮品，还需验证食品接触材质和标签文件。"

    if monthly_sales:
        trend_signal = (
            f"销量趋势从 {monthly_sales[0]['month']} 的 {monthly_sales[0]['value']:,.0f} 件变化到 "
            f"{monthly_sales[-1]['month']} 的 {monthly_sales[-1]['value']:,.0f} 件/月。"
        )
    else:
        trend_signal = "销量趋势待补。"
    if low_review_high_sales:
        gap_signal = f"同形态有 {low_review_high_sales} 个评论不高于 300、月销不低于 500 的样本，新链接并非完全没有入口。"
    else:
        gap_signal = "同形态未发现低评论高销量样本，进入壁垒需要谨慎复核。"
    benchmark_signal = (
        f"同为 {seed_pack} 件装的参考竞品 {benchmark.get('asin', '')} 月销 "
        f"{to_float(benchmark.get('monthly_sales_volume')):,.0f}、评论 {to_float(benchmark.get('review_count')):,.0f}。"
        if benchmark else "同件数、同形态的有效对标样本待补。"
    )
    review_signal = (
        "已读取评论的高频验证主题为：" + "、".join(f"{name} {count}" for name, count in top_review_tags) + "。"
        if top_review_tags else "评论主题样本不足，不能据此确认差异化。"
    )

    signals = [
        trend_signal,
        f"{seed_form} 在 Top100 中有 {form_count} 个样本，合计月销 {shape_sales:,.0f}，销量占比 {to_float(broad_shape.get('sales_share')):.1%}。",
        gap_signal,
        f"目标 ASIN 月销 {to_float(seed.get('monthly_sales')):,.0f}、评论 {to_float(seed.get('reviews')):,.0f}，已形成新品销量证据。",
        benchmark_signal,
        "评论中的重点验收主题为：" + "、".join(f"{name} {count}" for name, count in top_review_tags) + "。" if top_review_tags else "评论样本不足，痛点排序仍待补。",
    ]
    risks = [
        f"FBA 费 ${fba_fee:.2f}，占售价 {fba_share:.1%}；当前低售价下成本容错很小。" if price else "FBA 费占比待补。",
        f"同形态价格中位数 ${to_float(seed_shape.get('median_price')):.2f}，目标售价 ${price:.2f}；需要验证提价后转化是否仍成立。",
        f"类目 Top3 品牌销量占比 {top3_brand_share:.1%}。" if top3_brand_share else "类目品牌集中度数据待补。",
        review_signal,
        "Sorftime 评论接口只提供当前可用正文，仍需覆盖头部、低评高销和低星竞品后再定产品规格。",
    ]

    if profile == "bar_measuring_tool":
        risks.append("调酒量杯直接接触饮品，需核对不锈钢牌号、迁移/食品接触声明、涂层和标签要求。")
        entry_strategy = [
            "先比较单只 1/2 oz 双头量杯与两只组合装；当前两件套已有销量，但不能只复制件数。",
            "把内外刻度的对比度、读取方向和容量准确性做成可量化卖点，避免评论中出现刻度过浅、反向或难读。",
            "要求供应商确认 304 不锈钢、壁厚、单只重量、卷边和底部平整度，并做洗碗机、盐雾/锈蚀和跌落测试。",
            "用实测倒液、站立稳定性和四档容量校准视频证明差异，而不是继续堆叠泛化的 premium 文案。",
            f"先解决成本结构：完税到仓成本高于 ${max_landed_cost:.2f} 时，不以当前 ${price:.2f} 售价立项。",
        ]
        validation_gates = [
            f"目标可售单元完税到仓成本不高于 ${max_landed_cost:.2f}（按 15% 广告费和 20% 贡献毛利倒推）。",
            "完成至少 3 家 1688 同款同规格报价，逐项确认件数、304 不锈钢、壁厚、重量、容量和包装尺寸。",
            "用标准量筒验证全部 oz/ml 刻度，容量误差和刻度读取结果必须达到预设验收标准。",
            "完成洗碗机循环、盐雾/锈蚀、卷边毛刺、跌落和站立稳定性测试。",
            "取得适用于目标站点的食品接触材质声明或测试文件，并确认包装标签要求。",
        ]
    elif profile == "duster_refill":
        risks.append("兼容性商标、刷头脱落和纤维掉落仍是核心产品责任风险。")
        entry_strategy = [
            "按件数分层验证试用装、主力装和家庭补充装，但不得只靠大包装竞争。",
            "把防掉毛、静电吸附、插柄牢固度和不散尘作为样品硬指标。",
            "压缩包装体积并复核 FBA 档位，包装优化不能牺牲纤维蓬松度。",
            "兼容性说明、包装视觉和广告素材不得让消费者误认为品牌原厂。",
        ]
        validation_gates = [
            f"目标可售单元完税到仓成本不高于 ${max_landed_cost:.2f}。",
            "完成至少 3 家同规格报价，确认纤维克重、插柄尺寸公差和包装尺寸。",
            "完成装卸适配、抖落和标准粉尘吸附对比测试。",
            "审核兼容性商标在标题、包装和广告中的用法。",
        ]
    elif profile in {"landing_fishing_net", "cast_fishing_net"}:
        landing = profile == "landing_fishing_net"
        entry_strategy = [
            "先用成熟同形态竞品验证旺淡季，按淡季销量和交期控制首单，不能外推新品旺季爬坡。",
            "以防挂钩网面、湿态浮力和单手开合可靠性验证差异；浮网不等于救生设备。" if landing else "验证网目、沉降速度、张网圆度和收网顺畅度；把半径/直径、坠材质及适用水域写清。",
            "拆解样品确认锁扣和铰链，再做美国有效权利要求和外观对比。" if landing else "拆解中心网盘与拉绳，核对双线/内嵌管回收结构专利；不照搬专利宣传或品牌视觉。",
            f"先取得同 SKU 报价：当前假设下到仓成本上限 ${max_landed_cost:.2f}，未含退货、仓储和促销。",
        ]
        validation_gates = [
            "季节性、专利与品牌集中度、声明认证三项均须有来源、日期及未决事项；没有证据不得放行。",
            f"至少 3 条有效同 SKU 报价，并核对到仓成本是否低于 ${max_landed_cost:.2f}。",
            "锁扣开合循环、湿态承载、伸缩防滑、浮力和盐水腐蚀测试；另查包装是否跨 FBA 档位。" if landing else "核实 4 ft 半径子体的网目、配重和包装；验证坠料成分、PVC 涂层、拉绳拉力及水中沉降。",
            "保留 BOM/材质及批次追溯、适用规则评估、必要检测和安全警示；核对美国目标州使用规定。",
        ]
    else:
        entry_strategy = [
            f"围绕 {seed_form} 的高频评论问题定义材料、尺寸、结构和包装验收标准，不做仅换颜色的同质化版本。",
            "同时比较单件与主流组合装，确认件数变化是否带来真实客单价和利润提升。",
            "用可复现实测证明核心性能，并把测试结果转成 Listing 图片和视频证据。",
            (
                f"先核对 ${price:.2f} 是否为真实可持续成交价；当前售价低于 FBA ${fba_fee:.2f}，在提价后利润成立前暂停采购。"
                if no_landed_cost_room
                else f"完税到仓成本高于 ${max_landed_cost:.2f} 时，不以当前售价立项。"
            ),
        ]
        validation_gates = [
            (
                "先确认可持续售价；售价必须在计入 FBA、佣金、广告、退货、采购和物流后仍达到目标贡献毛利。"
                if no_landed_cost_room
                else f"目标可售单元完税到仓成本不高于 ${max_landed_cost:.2f}。"
            ),
            "完成至少 3 家 1688 同款同规格报价并确认 MOQ、交期、材料和包装尺寸。",
            "把高频评论问题转成样品测试，结果不得明显弱于主流竞品。",
            "复核目标站点适用的产品安全、材料、标签和运输要求。",
        ]

    return {
        "generated_on": date.today().isoformat(),
        "decision": decision,
        "decision_score": score,
        "decision_reason": decision_reason,
        "research_profile": profile,
        "product": {
            "asin": seed.get("asin"),
            "title": seed.get("title"),
            "price": price,
            "monthly_sales": seed.get("monthly_sales"),
            "reviews": seed.get("reviews"),
            "rating": seed.get("rating"),
            "fba_fee": fba_fee,
            "fba_fee_share": fba_fee / price if price else 0,
            "package_size_cm": detail.get("package_size_cm", ""),
            "package_volume_l": round(volume, 2),
            "weight_g": detail.get("weight_g", 0),
            "online_date": detail.get("online_date", ""),
            "variation_count": detail.get("variation_count", 0),
        },
        "shape": broad_shape,
        "exact_subform": {
            "name": seed.get("product_form"),
            "count": seed_shape.get("count", 0),
            "total_monthly_sales": seed_shape.get("total_monthly_sales", 0),
            "sales_share": seed_shape.get("sales_share", 0),
            "median_reviews": seed_shape.get("median_reviews", 0),
            "low_review_high_sales_count": seed_shape.get("low_review_high_sales_count", 0),
        },
        "same_pack_benchmark": {
            "asin": benchmark.get("asin", ""),
            "title": benchmark.get("title", ""),
            "price": benchmark.get("price", 0),
            "monthly_sales": benchmark.get("monthly_sales_volume", 0),
            "reviews": benchmark.get("review_count", 0),
            "fba_fee": benchmark.get("fba_fee", 0),
            "package_size": benchmark.get("package_size", ""),
        },
        "category": {
            "node_id": detail.get("node_id", ""),
            "top100_monthly_sales": embedded_number(category_stats.get("top100_monthly_sales_volume")),
            "average_price": embedded_number(category_stats.get("average_price")),
            "median_price": embedded_number(category_stats.get("median_price")),
            "first_brand": str(category_stats.get("first_brand", "")).split(":", 1)[-1].strip(),
            "first_brand_share": round(
                embedded_number(category_stats.get("first_brand_sales_volume"))
                / embedded_number(category_stats.get("top100_monthly_sales_volume")),
                4,
            ) if embedded_number(category_stats.get("top100_monthly_sales_volume")) else 0,
            "top3_brand_share": embedded_number(category_stats.get("top3_brands_sales_volume_share"), percent=True),
            "amazon_owned_share": embedded_number(category_stats.get("amazon_owned_sales_volume_share"), percent=True),
            "high_review_sales_share": embedded_number(category_stats.get("high_reviews_sales_volume_share"), percent=True),
            "return_rate": embedded_number(category_stats.get("return_rate"), percent=True),
        },
        "trend": {"monthly_sales": monthly_sales, "price": prices, "latest_mom_growth": round(growth, 4)},
        "variations": variations,
        "customer_voice": {"summary": customers_say.get("customer_say", ""), "topics": details or []},
        "unit_economics_gate": {
            "assumed_referral_rate": 0.15,
            "assumed_ad_rate": ad_rate,
            "target_contribution_margin": target_margin,
            "max_landed_cost": round(max_landed_cost, 2),
            "note": (
                "当前售价连 FBA 与销售费用都无法覆盖，没有可用的到仓成本空间；先核对售价或提价，不能按 $0.00 成本立项。"
                if no_landed_cost_room
                else "未含退货、仓储和促销；供应商完税到仓成本超过此值时不建议进入。"
            ),
        },
        "signals": signals,
        "risks": risks,
        "entry_strategy": entry_strategy,
        "validation_gates": validation_gates,
        "provenance": {"category_source": category_source, "mcp_tool_calls": tool_calls},
    }


def write_report(
    path: Path,
    summary: dict[str, Any],
    forms: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    business: dict[str, Any],
    supplier_quotes: list[dict[str, Any]] | None = None,
) -> None:
    product = summary["product"]
    shape = summary["shape"]
    exact = summary["exact_subform"]
    unit = summary["unit_economics_gate"]
    score = summary["decision_score"]
    score_label = f"{score}/100" if isinstance(score, (int, float)) else str(score)
    lines = [
        f"# {product['asin']} 单品深度研究",
        "",
        f"**结论：{summary['decision']}（{score_label}）**",
        "",
        summary["decision_reason"],
        "",
        "## 产品快照",
        "",
        f"- 售价：${product['price']:.2f}；月销：{to_float(product['monthly_sales']):,.0f}；评论：{to_float(product['reviews']):,.0f}；评分：{to_float(product['rating']):.1f}",
        f"- FBA 费：${product['fba_fee']:.2f}（售价占比 {product['fba_fee_share']:.1%}）",
        f"- 包装：{product['package_size_cm']} cm，约 {product['package_volume_l']:.2f} L，重量 {to_float(product['weight_g']):,.0f} g",
        f"- 上架：{product['online_date']}；父体变体：{int(to_float(product['variation_count']))} 个",
        "",
        "## 形态与类目判断",
        "",
    ]
    if shape["name"] == exact["name"]:
        lines.extend([
            f"目标形态 **{shape['name']}**：{shape.get('scope', 'Top100')} 中 {int(to_float(shape['count']))} 个，形态总月销 {to_float(shape['total_monthly_sales']):,.0f}，样本内销量占比 {to_float(shape['sales_share']):.1%}，评论中位 {to_float(shape['median_reviews']):,.0f}，低评高销 {int(to_float(shape['low_review_high_sales_count']))} 个。",
            "",
        ])
    else:
        lines.extend([
            f"广义形态 **{shape['name']}**：Top100 中 {int(to_float(shape['count']))} 个，形态总月销 {to_float(shape['total_monthly_sales']):,.0f}，销量占比 {to_float(shape['sales_share']):.1%}，评论中位 {to_float(shape['median_reviews']):,.0f}，低评高销 {int(to_float(shape['low_review_high_sales_count']))} 个。",
            f"目标精确子形态 **{exact['name']}**：{int(to_float(exact['count']))} 个，月销 {to_float(exact['total_monthly_sales']):,.0f}，评论中位 {to_float(exact['median_reviews']):,.0f}。",
            "",
        ])
    lines.extend([
        "| 细分形态 | 数量 | 月销合计 | 销量占比 | 评论中位 | 低评高销 |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in forms:
        lines.append(
            f"| {row.get('product_form', '')} | {int(to_float(row.get('count')))} | {to_float(row.get('total_monthly_sales')):,.0f} | {to_float(row.get('sales_share')):.1%} | {to_float(row.get('median_reviews')):,.0f} | {int(to_float(row.get('low_review_high_sales_count')))} |"
        )
    if summary.get("required_checks"):
        lines.extend(checks_markdown(summary["required_checks"]))
    if summary.get("new_entrant_analysis"):
        from new_entrant_research import entrants_markdown
        lines.extend(entrants_markdown(summary["new_entrant_analysis"]))
    lines.extend(["", "## 成立信号", ""])
    lines.extend(f"- {item}" for item in summary["signals"])
    lines.extend(["", "## 核心风险", ""])
    lines.extend(f"- {item}" for item in summary["risks"])
    lines.extend(["", "## 切入方案", ""])
    lines.extend(f"- {item}" for item in summary["entry_strategy"])
    lines.extend(["", "## 利润核算", ""])
    if business.get("profitability_verified"):
        lines.extend([
            f"- 1688 同款有效样本：{business.get('quote_sample_count', 0)} 条；采购价中位数：¥{to_float(business.get('procurement_cost_cny')):.2f}。",
            f"- 折算采购成本：${to_float(business.get('unit_cost')):.2f}/可售单元；贡献利润：${to_float(business.get('contribution_profit')):.2f}；贡献利润率：{to_float(business.get('contribution_margin')):.1%}。",
            "- 包装、头程、广告和退货中未取得实价的部分仍为规划假设。",
            "",
        ])
    else:
        lines.extend([
            f"- **利润未验证：已找到 {business.get('candidate_quote_count', 0)} 条 1688 候选，但同款同规格 SKU/确认报价不足 3 条。**",
            "- 商品最低展示价不计入采购成本，不能据此立项。",
            "",
        ])
    if supplier_quotes:
        lines.extend(["## 1688 检索与规格核对", "",
                      "以下包含未确认和已排除的 SKU，报价不等于有效同款成本。", "",
                      "| 供应商 | SKU / 规格 | 元/件 | 核对结果 |", "|---|---|---:|---|"])
        def clean(value: object) -> str:
            return str(value or "").replace("|", "/").replace("\n", " ")

        for quote in supplier_quotes:
            lines.append(f"| [{clean(quote.get('supplier'))}]({quote.get('source_url', '')}) | {clean(quote.get('source_sku_name')) or '未返回有效 SKU'} | {clean(quote.get('source_unit_price')) or '-'} | {clean(quote.get('match_reason'))} |")
        lines.append("")
    lines.extend(["## 利润门槛", ""])
    if to_float(unit.get("max_landed_cost")) <= 0 and to_float(product.get("price")) > 0:
        lines.extend([
            f"按售价 ${product['price']:.2f} 和 FBA ${product['fba_fee']:.2f}，**当前尚未计采购成本即已亏损，没有可用的完税到仓成本空间**。",
            "",
            unit["note"],
            "",
        ])
    else:
        lines.extend([
            f"按售价 ${product['price']:.2f}、FBA ${product['fba_fee']:.2f}、15% referral、15% 广告和 20% 目标贡献毛利倒推，**目标可售单元完税到仓成本上限约 ${unit['max_landed_cost']:.2f}**。",
            "",
            unit["note"],
            "",
        ])
    lines.extend(["## 继续验证条件", ""])
    lines.extend(f"- {item}" for item in summary["validation_gates"])
    lines.extend(["", "## 评论样本", "", "| ASIN | 形态 | 月销 | Listing 评论 | 已读取 |", "|---|---|---:|---:|---:|"])
    for row in targets:
        lines.append(
            f"| [{row['asin']}]({row['listing_url']}) | {row['product_form']} | {to_float(row['monthly_sales']):,.0f} | {to_float(row['reviews']):,.0f} | {int(to_float(row['review_rows_collected']))} |"
        )
    lines.extend(
        [
            "",
            "## 数据说明",
            "",
            f"- 类目来源：`{summary['provenance']['category_source']}`",
            f"- 主研究采集记录：Sorftime MCP {summary['provenance']['mcp_tool_calls']} 次",
            f"- 后续 1688 补查记录：{summary['provenance'].get('supplemental_sourcing_tool_calls', 0)} 次；不包含在主研究计数内。",
            "- 评论接口只返回近一年可用正文，数量不等于 Listing 总评论数。",
            "- 成本上限为倒推门槛，不是供应商报价。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def rebuild_cached(output_dir: Path, report_path: Path, rules: dict, site: str) -> None:
    sources = load_json(output_dir / "research_sources.json")
    old = load_json(output_dir / "research_summary.json")
    detail = dict(sources["detail"])
    if sources.get("site") != site or detail.get("asin") != output_dir.name:
        raise ValueError("Cached research ASIN/site does not match requested scope")
    node_id = str(detail.get("node_id") or "").strip() or local_category_node_id(str(detail["asin"]))
    if node_id:
        detail["node_id"] = node_id
    products = read_products(output_dir / "top_products.csv")
    seed = next(row for row in products if row.get("asin") == detail["asin"])
    enrich_row(seed, detail)
    target_form = deep_product_form(detail)
    for row in products:
        row["product_form"] = deep_product_form({
            "title": row.get("title", ""),
            "brand": row.get("brand", ""),
            "product_category": row.get("product_type", ""),
        })
        is_seed = row.get("asin") == detail["asin"]
        identity = product_identity(row.get("title", ""), form=row["product_form"])
        row.update({key: value for key, value in identity.items() if key != "product_form"})
        is_direct = row["product_form"] == target_form and identity["classification_status"] not in {"unresolved", "needs_specification"}
        row["competitor_type"] = "seed" if is_seed else ("direct" if is_direct else "keyword")
        row["relevance_score"] = 100 if is_seed else (90 if is_direct else 65)
    write_csv(output_dir / "top_products.csv", products, PRODUCT_FIELDS)
    forms = build_form_rows(products, rules)
    write_csv(output_dir / "product_forms.csv", forms, FORM_FIELDS)
    market = analyze_market(products)
    (output_dir / "market_structure.json").write_text(
        json.dumps(market, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(
        output_dir / "price_bands.csv",
        market.get("price_bands", []),
        ["price_band", "listing_count", "monthly_sales", "sales_share", "median_reviews"],
    )
    reviews = read_products(output_dir / "reviews.csv")
    demand = analyze_demand(reviews, products, ROOT / rules["demand_taxonomy"])
    write_csv(output_dir / "demand_analysis.csv", demand, DEMAND_FIELDS)
    product_by_asin = {str(row.get("asin") or ""): row for row in products}
    targets = read_products(output_dir / "review_targets.csv")
    for target in targets:
        product = product_by_asin.get(str(target.get("asin") or ""))
        if product:
            target["product_form"] = product.get("product_form", "")
            target["competitor_type"] = product.get("competitor_type", "")
    write_csv(output_dir / "review_targets.csv", targets, REVIEW_TARGET_FIELDS)
    matched, reason = supplier_matches(sources.get("supplier_candidates", []), seed)
    # Preserve supplier-verified quotes; only refresh unreviewed image-search leads.
    existing = read_products(output_dir / "supplier_quotes.csv")
    if not any(row.get("match_status") != "candidate" or row.get("price_scope") != "listing_display" for row in existing):
        quotes = quote_rows_from_1688(matched, cny_per_usd=to_float(rules.get("planning_cny_per_usd"), 7), match_reason=reason)
        write_csv(output_dir / "supplier_quotes.csv", quotes, QUOTE_FIELDS)
    business = write_business_feasibility(output_dir, ROOT / rules["business_feasibility_rules"])
    report_data = as_data(load_json(ROOT / sources["category_source"]))
    summary = make_summary(
        seed, detail, report_data.get("category_stats_report", {}),
        shape_snapshot(forms, str(seed["product_form"])),
        {"sales": sources.get("product_sales_trend", []), "price": sources.get("product_price_trend", [])},
        old.get("variations", []),
        {"customer_say": old.get("customer_voice", {}).get("summary", ""), "details": old.get("customer_voice", {}).get("topics", [])},
        read_products(output_dir / "keywords.csv"), report_data.get("top100_products", []),
        reviews, sources["category_source"],
        old.get("provenance", {}).get("mcp_tool_calls", 0),
    )
    summary["provenance"]["data_retrieved_on"] = sources.get("retrieved_on", "")
    lookup_path = output_dir / "supplier_lookup_sources.json"
    if lookup_path.exists():
        lookup = load_json(lookup_path)
        if lookup.get("asin") == detail["asin"] and lookup.get("site") == site:
            summary["provenance"]["supplemental_sourcing_tool_calls"] = lookup.get("tool_call_count", 0)
    (output_dir / "research_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    refresh_required_checks(output_dir)
    write_report(report_path, load_json(output_dir / "research_summary.json"), forms, targets, business,
                 read_products(output_dir / "supplier_quotes.csv"))
    print(f"Rebuilt cached research: {detail['asin']}; new MCP calls: 0")


def main() -> None:
    args = parse_args()
    asin = args.asin.strip().upper()
    rules = load_json(ROOT / args.rules)
    output_dir = ROOT / (args.output_dir or f"research/{asin}")
    report_path = ROOT / (args.report or f"reports/product_opportunity_research_{asin}.md")
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.rebuild_cached:
        rebuild_cached(output_dir, report_path, rules, args.site)
        return

    client = SorftimeMcpClient()
    detail = as_data(client.call_tool("product_detail", {"asin": asin, "amz_site": args.site}))
    if not isinstance(detail, dict) or not detail.get("asin"):
        raise SystemExit(f"No product detail returned for {asin}")
    target_form = deep_product_form(detail)
    seed_probe = {"title": detail.get("title", ""), "product_form": target_form}
    image_url = first_image(detail.get("main_image"))
    supplier_candidates = as_data(client.call_tool(
        "ali1688_product_search_from_image",
        {"image_url": image_url, "page": 1},
    )) if image_url else []
    matched_suppliers, supplier_match_reason = supplier_matches(
        supplier_candidates if isinstance(supplier_candidates, list) else [],
        seed_probe,
    )
    supplier_quotes = quote_rows_from_1688(
        matched_suppliers,
        cny_per_usd=to_float(rules.get("planning_cny_per_usd"), 7.0),
        match_reason=supplier_match_reason,
    )
    node_id = str(detail.get("node_id") or "").strip() or local_category_node_id(asin)
    if not node_id:
        raise SystemExit(
            f"No minimum-category node returned for {asin}, and no matching local discovery evidence was found"
        )
    detail["node_id"] = node_id
    report, report_source = category_report(client, node_id, args.site)
    report_data = as_data(report)
    if not isinstance(report_data, dict):
        raise SystemExit(f"Invalid category report for node {node_id}")
    raw_products = report_data.get("top100_products") or []
    category_stats = report_data.get("category_stats_report") or {}
    category_name = str(detail.get("subcategory") or "").split(" (Rank:")[0] or str(detail.get("category") or "")
    report_seed = next(
        (item for item in raw_products if isinstance(item, dict) and str(item.get("asin") or "").upper() == asin),
        None,
    )
    if report_seed:
        target_form = deep_product_form(report_seed)
    product_rows = [raw_row(asin, item, category_name, target_form) for item in raw_products if isinstance(item, dict)]
    if not any(row.get("asin") == asin for row in product_rows):
        product_rows.insert(0, raw_row(asin, detail, category_name, target_form))
    seed = next(row for row in product_rows if row.get("asin") == asin)
    seed["competitor_type"] = "seed"
    seed["relevance_score"] = 100
    enrich_row(seed, detail)

    selected_rows = select_review_rows(product_rows, asin, args.review_limit)
    for row in selected_rows:
        if row.get("asin") == asin:
            continue
        competitor_detail = as_data(client.call_tool("product_detail", {"asin": row["asin"], "amz_site": args.site}))
        if isinstance(competitor_detail, dict):
            enrich_row(row, competitor_detail)

    product_rows.sort(
        key=lambda row: (
            0 if row.get("asin") == asin else (1 if row.get("product_form") == target_form else 2),
            -to_float(row.get("monthly_sales")),
        )
    )

    image_rows, contact_sheet = prepare_image_assets(product_rows, output_dir, rules)
    relativize_image_paths(product_rows)
    relativize_image_paths(image_rows)
    apply_visual_labels(product_rows)
    form_rows = build_form_rows(product_rows, rules)
    market = analyze_market(product_rows)
    targets = review_targets(selected_rows)
    reviews = collect_reviews(client, selected_rows, args.site, research_profile(seed))
    attach_review_counts(targets, reviews)
    demand = analyze_demand(reviews, product_rows, ROOT / rules["demand_taxonomy"])
    traffic = as_data(client.call_tool("product_traffic_terms", {"asin": asin, "amz_site": args.site, "page": 1}))
    customers_say = as_data(client.call_tool("product_customers_say", {"asin": asin, "site": args.site}))
    variations = as_data(client.call_tool("product_variations", {"asin": asin, "amz_site": args.site, "page": 1}))
    sales_trend = as_data(client.call_tool("product_trend", {"asin": asin, "amz_site": args.site, "product_trend_type": "SalesVolume"}))
    price_trend = as_data(client.call_tool("product_trend", {"asin": asin, "amz_site": args.site, "product_trend_type": "Price"}))
    category_trend_path = ROOT / "archive" / "cache" / "category_trends" / f"{args.site}_{node_id}_{date.today().isoformat()}.json"
    if category_trend_path.exists():
        category_sales_trend = load_json(category_trend_path).get("data", [])
    else:
        category_sales_trend = as_data(client.call_tool("category_trend", {"node_id": node_id, "amz_site": args.site, "trend_index": "SalesCount"}))
        category_trend_path.parent.mkdir(parents=True, exist_ok=True)
        category_trend_path.write_text(json.dumps({"data": category_sales_trend}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "research_sources.json").write_text(json.dumps({
        "retrieved_on": date.today().isoformat(), "site": args.site,
        "detail": detail, "product_sales_trend": sales_trend,
        "product_price_trend": price_trend, "category_sales_trend": category_sales_trend,
        "category_source": str(report_source.relative_to(ROOT)),
        "category_trend_source": str(category_trend_path.relative_to(ROOT)),
        "supplier_candidates": supplier_candidates,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    keywords = keyword_rows(traffic)

    write_csv(output_dir / "top_products.csv", product_rows, PRODUCT_FIELDS)
    write_csv(output_dir / "product_forms.csv", form_rows, FORM_FIELDS)
    write_csv(output_dir / "keywords.csv", keywords, KEYWORD_FIELDS)
    write_csv(output_dir / "reviews.csv", reviews, REVIEW_FIELDS)
    write_csv(output_dir / "review_targets.csv", targets, REVIEW_TARGET_FIELDS)
    write_csv(output_dir / "image_review_queue.csv", image_rows, IMAGE_FIELDS)
    write_csv(output_dir / "visual_labels.csv", image_rows, IMAGE_FIELDS)
    write_csv(output_dir / "demand_analysis.csv", demand, DEMAND_FIELDS)
    write_csv(output_dir / "supplier_quotes.csv", supplier_quotes, QUOTE_FIELDS)
    collect_supplier_screening(output_dir, client, max_calls=3)
    (output_dir / "market_structure.json").write_text(json.dumps(market, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(
        output_dir / "price_bands.csv",
        market.get("price_bands", []),
        ["price_band", "listing_count", "monthly_sales", "sales_share", "median_reviews"],
    )
    business = write_business_feasibility(output_dir, ROOT / rules["business_feasibility_rules"])

    seed_shape = shape_snapshot(form_rows, str(seed.get("product_form") or ""))
    summary = make_summary(
        seed,
        detail,
        category_stats,
        seed_shape,
        {"sales": sales_trend, "price": price_trend},
        variations if isinstance(variations, list) else [],
        customers_say if isinstance(customers_say, dict) else {},
        keywords,
        raw_products,
        reviews,
        str(report_source.relative_to(ROOT)),
        client.tool_call_count,
    )
    (output_dir / "research_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    refresh_required_checks(output_dir)
    summary = load_json(output_dir / "research_summary.json")
    write_report(report_path, summary, form_rows, targets, business, supplier_quotes)

    print(f"Research products: {len(product_rows)}")
    print(f"Review targets: {len(targets)}; review rows: {len(reviews)}")
    print(f"MCP tool calls: {client.tool_call_count}")
    print(f"Contact sheet: {contact_sheet or '-'}")
    print(f"Report: {report_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
