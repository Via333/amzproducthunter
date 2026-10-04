#!/usr/bin/env python3
"""Supplier quote template and contribution/cash-flow feasibility calculation."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import median
from typing import Any, Callable


QUOTE_FIELDS = [
    "supplier",
    "quote_status",
    "quoted_at",
    "unit_cost",
    "moq",
    "sample_cost",
    "packaging_per_unit",
    "freight_per_unit",
    "duty_per_unit",
    "fba_fee_per_unit",
    "lead_time_days",
    "production_days",
    "payment_terms",
    "carton_dimensions",
    "units_per_carton",
    "certifications",
    "customization",
    "notes",
    "source_platform",
    "source_method",
    "source_product_id",
    "source_url",
    "source_sku_id",
    "source_sku_name",
    "product_title",
    "match_status",
    "match_reason",
    "price_scope",
    "target_specification",
    "source_currency",
    "source_unit_price",
    "source_pack_count",
    "normalized_unit_cost_cny",
    "cny_per_usd",
]

VALID_1688_QUOTE_STATUSES = {"benchmark", "confirmed"}
VALID_1688_MATCH_STATUSES = {"same_product", "same_product_component"}
VALID_1688_PRICE_SCOPES = {"exact_sku", "confirmed_unit"}


def number(value: object) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except ValueError:
        return 0.0


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def ensure_quote_template(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.DictWriter(handle, fieldnames=QUOTE_FIELDS).writeheader()


def quote_rows_from_1688(
    rows: list[dict[str, Any]],
    *,
    cny_per_usd: float,
    match_reason: str,
    source_method: str = "image_search",
    pack_count_getter: Callable[[dict[str, Any]], int] | None = None,
) -> list[dict[str, Any]]:
    """Normalize filtered 1688 same-product results into auditable quote rows.

    Callers must filter the source rows to the same product/form before using this
    helper. A 1688 price is treated as the cost of one supplier sellable unit; a
    multi-piece set therefore remains one Amazon sellable unit and is not divided
    by the number of pieces in the title.
    """
    output = []
    seen: set[str] = set()
    for row in rows:
        price_cny = number(row.get("price"))
        product_id = str(row.get("product_id") or "").strip()
        url = str(row.get("url") or "").strip()
        dedupe_key = product_id or url
        if not price_cny or not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        if product_id:
            url = f"https://detail.1688.com/offer/{product_id}.html"
        source_pack_count = pack_count_getter(row) if pack_count_getter else 1
        output.append({
            "supplier": str(row.get("store_name") or product_id),
            "quote_status": "benchmark",
            "quoted_at": str(row.get("online_date") or ""),
            "unit_cost": round(price_cny / cny_per_usd, 4),
            "moq": number(row.get("min_order_quantity")) or None,
            "notes": "1688 页面展示价；正式下单前须确认目标规格、阶梯价和包装。",
            "source_platform": "1688",
            "source_method": source_method,
            "source_product_id": product_id,
            "source_url": url,
            "source_sku_id": "",
            "source_sku_name": "",
            "product_title": str(row.get("title") or ""),
            "match_status": "candidate",
            "match_reason": match_reason,
            "price_scope": "listing_display",
            "target_specification": "",
            "source_currency": "CNY",
            "source_unit_price": round(price_cny, 4),
            "source_pack_count": source_pack_count,
            "normalized_unit_cost_cny": round(price_cny, 4),
            "cny_per_usd": cny_per_usd,
        })
    return output


def valid_1688_quotes(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return deduplicated 1688 rows approved as same-product cost evidence."""
    valid = []
    seen: set[str] = set()
    for row in rows:
        platform = str(row.get("source_platform") or "").strip().lower()
        quote_status = str(row.get("quote_status") or "").strip().lower()
        match_status = str(row.get("match_status") or "").strip().lower()
        price_scope = str(row.get("price_scope") or "").strip().lower()
        cost_cny = number(row.get("normalized_unit_cost_cny"))
        dedupe_key = str(row.get("source_product_id") or row.get("source_url") or "").strip()
        if (
            platform != "1688"
            or quote_status not in VALID_1688_QUOTE_STATUSES
            or match_status not in VALID_1688_MATCH_STATUSES
            or price_scope not in VALID_1688_PRICE_SCOPES
            or cost_cny <= 0
            or not dedupe_key
            or dedupe_key in seen
        ):
            continue
        seen.add(dedupe_key)
        valid.append(row)
    return valid


def traceable_1688_candidates(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return deduplicated 1688 rows that still need exact-SKU verification."""
    candidates = []
    seen: set[str] = set()
    for row in rows:
        dedupe_key = str(row.get("source_product_id") or row.get("source_url") or "").strip()
        if (
            str(row.get("source_platform") or "").strip().lower() != "1688"
            or str(row.get("match_status") or "").strip().lower() in {"rejected", "material_mismatch", "dimension_mismatch", "unavailable"}
            or number(row.get("normalized_unit_cost_cny")) <= 0
            or not dedupe_key
            or dedupe_key in seen
        ):
            continue
        seen.add(dedupe_key)
        candidates.append(row)
    return candidates


def quote_urls(rows: list[dict[str, str]]) -> list[str]:
    return [
        f"https://detail.1688.com/offer/{row['source_product_id']}.html"
        if str(row.get("source_product_id") or "").strip()
        else str(row.get("source_url") or "")
        for row in rows
    ]


def nested_number(payload: dict[str, Any], *path: str) -> float:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return 0.0
        value = value.get(key)
    return number(value)


def actual_fba_fee(research_dir: Path, quote_rows: list[dict[str, str]]) -> float:
    quote_fees = [number(row.get("fba_fee_per_unit")) for row in quote_rows]
    quote_fees = [value for value in quote_fees if value > 0]
    if quote_fees:
        return median(quote_fees)
    for filename, path in (
        ("deep_analysis.json", ("facts", "fba_fee")),
        ("research_summary.json", ("product", "fba_fee")),
    ):
        file_path = research_dir / filename
        if not file_path.exists():
            continue
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        value = nested_number(payload, *path)
        if value > 0:
            return value
    return 0.0


def build_business_feasibility(research_dir: Path, rules_path: Path) -> dict:
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    products = read_csv(research_dir / "top_products.csv")
    seed = next((row for row in products if row.get("competitor_type") == "seed"), products[0] if products else {})
    price = number(seed.get("price"))
    quote_path = research_dir / "supplier_quotes.csv"
    ensure_quote_template(quote_path)
    all_quotes = read_csv(quote_path)
    candidates = traceable_1688_candidates(all_quotes)
    quotes = valid_1688_quotes(all_quotes)
    confirmed_quotes = [row for row in quotes if str(row.get("quote_status") or "").strip().lower() == "confirmed"]
    minimum_samples = int(rules.get("minimum_1688_same_product_samples", 3))
    quote_count = len(quotes)
    cost_values_cny = [number(row.get("normalized_unit_cost_cny")) for row in quotes]
    has_cost_benchmark = quote_count >= minimum_samples
    fx_values = [number(row.get("cny_per_usd")) for row in quotes if number(row.get("cny_per_usd")) > 0]
    cny_per_usd = median(fx_values) if fx_values else number(rules.get("planning_cny_per_usd", 7.0))
    procurement_cost_cny = median(cost_values_cny) if has_cost_benchmark else None
    unit_cost = procurement_cost_cny / cny_per_usd if procurement_cost_cny and cny_per_usd else None

    # Non-procurement costs remain explicit planning assumptions until a formal
    # supplier/freight quote replaces them. Procurement itself never falls back
    # to a percentage of the Amazon selling price.
    packaging_values = [number(row.get("packaging_per_unit")) for row in confirmed_quotes]
    packaging_values = [value for value in packaging_values if value > 0]
    freight_values = [number(row.get("freight_per_unit")) for row in confirmed_quotes]
    freight_values = [value for value in freight_values if value > 0]
    duty_values = [number(row.get("duty_per_unit")) for row in confirmed_quotes]
    duty_values = [value for value in duty_values if value > 0]
    packaging = median(packaging_values) if packaging_values else price * number(rules.get("default_packaging_rate", 0))
    freight = median(freight_values) if freight_values else price * rules["default_shipping_rate"]
    duty = median(duty_values) if duty_values else 0
    fba_actual = actual_fba_fee(research_dir, quotes)
    fba_fee = fba_actual or max(rules["minimum_fba_fee"], price * rules["default_fba_fee_rate"])
    referral = price * rules["referral_fee_rate"]
    returns = price * rules["assumed_return_rate"] * rules["assumed_return_loss_rate"]
    coupon = price * rules["assumed_coupon_rate"]
    storage = price * rules["assumed_storage_rate"]
    if unit_cost is not None:
        pre_ad_profit = price - unit_cost - packaging - freight - duty - fba_fee - referral - returns - coupon - storage
        ad_cost = price * rules["target_ad_cost_rate"]
        contribution_profit = pre_ad_profit - ad_cost
        contribution_margin = contribution_profit / price if price else 0
    else:
        pre_ad_profit = contribution_profit = contribution_margin = None

    moqs = [number(row.get("moq")) for row in confirmed_quotes if number(row.get("moq")) > 0]
    lead_times = [number(row.get("lead_time_days")) for row in confirmed_quotes if number(row.get("lead_time_days")) > 0]
    production_days_values = [number(row.get("production_days")) for row in confirmed_quotes if number(row.get("production_days")) > 0]
    sample_costs = [number(row.get("sample_cost")) for row in confirmed_quotes if number(row.get("sample_cost")) > 0]
    moq = median(moqs) if moqs else 0
    lead_time = median(lead_times) if lead_times else 0
    production_days = median(production_days_values) if production_days_values else 0
    cash_cycle = lead_time + production_days if lead_time or production_days else None
    initial_cash = (unit_cost + packaging) * moq + (median(sample_costs) if sample_costs else 0) if unit_cost is not None and moq else None
    status = "1688_cost_benchmarked" if has_cost_benchmark else "needs_1688_same_product_quotes"
    missing_evidence = []
    if not has_cost_benchmark:
        missing_evidence.append(f"至少 {minimum_samples} 条 1688 同款同规格 SKU/确认报价")
    if not packaging_values:
        missing_evidence.append("包装实价")
    if not freight_values:
        missing_evidence.append("头程实价")
    if not lead_times:
        missing_evidence.append("交期")
    if not moqs:
        missing_evidence.append("确认 MOQ")
    if not confirmed_quotes or not any(str(row.get("certifications") or "").strip() for row in confirmed_quotes):
        missing_evidence.append("所需认证/测试")
    return {
        "status": status,
        "data_source": "1688_same_product_median" if has_cost_benchmark else "missing_1688_same_product_median",
        "profitability_verified": has_cost_benchmark,
        "profitability_level": "cost_benchmark" if has_cost_benchmark else "unverified",
        "procurement_basis": "1688 同款去重报价中位数",
        "quote_sample_count": quote_count,
        "candidate_quote_count": len(candidates),
        "minimum_quote_samples": minimum_samples,
        "procurement_cost_cny": round(procurement_cost_cny, 2) if procurement_cost_cny is not None else None,
        "procurement_price_range_cny": [round(min(cost_values_cny), 2), round(max(cost_values_cny), 2)] if has_cost_benchmark else None,
        "cny_per_usd": round(cny_per_usd, 4) if cny_per_usd else None,
        "quote_urls": quote_urls(quotes),
        "candidate_quote_urls": quote_urls(candidates),
        "target_price": round(price, 2),
        "unit_cost": round(unit_cost, 2) if unit_cost is not None else None,
        "packaging_cost": round(packaging, 2) if unit_cost is not None else None,
        "freight_cost": round(freight, 2) if unit_cost is not None else None,
        "fba_fee": round(fba_fee, 2) if unit_cost is not None else None,
        "landed_non_ad_cost": round(price - pre_ad_profit, 2) if pre_ad_profit is not None else None,
        "pre_ad_profit": round(pre_ad_profit, 2) if pre_ad_profit is not None else None,
        "break_even_acos": round(max(0, pre_ad_profit / price), 4) if price and pre_ad_profit is not None else None,
        "target_ad_cost_rate": rules["target_ad_cost_rate"],
        "contribution_profit": round(contribution_profit, 2) if contribution_profit is not None else None,
        "contribution_margin": round(contribution_margin, 4) if contribution_margin is not None else None,
        "meets_margin_target": contribution_margin >= rules["minimum_contribution_margin"] if contribution_margin is not None else None,
        "moq": moq or None,
        "initial_inventory_cash": round(initial_cash, 2) if initial_cash is not None else None,
        "cash_cycle_days": round(cash_cycle) if cash_cycle is not None else None,
        "cash_cycle_within_target": cash_cycle <= rules["maximum_cash_cycle_days"] if cash_cycle is not None else None,
        "assumptions": {
            "packaging": "quote_median" if packaging_values else "planning_rate",
            "freight": "quote_median" if freight_values else "planning_rate",
            "fba_fee": "product_detail" if fba_actual else "planning_rate",
            "referral_fee_rate": rules["referral_fee_rate"],
            "ad_cost_rate": rules["target_ad_cost_rate"],
            "return_rate": rules["assumed_return_rate"],
        },
        "missing_evidence": missing_evidence,
    }


def write_business_feasibility(research_dir: Path, rules_path: Path) -> dict:
    result = build_business_feasibility(research_dir, rules_path)
    output = research_dir / "business_feasibility.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
