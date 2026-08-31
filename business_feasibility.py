#!/usr/bin/env python3
"""Supplier quote template and contribution/cash-flow feasibility calculation."""

from __future__ import annotations

import csv
import json
from pathlib import Path


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
    "notes"
]


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


def best_confirmed_quote(rows: list[dict[str, str]]) -> dict[str, str] | None:
    confirmed = [row for row in rows if row.get("quote_status", "").lower() == "confirmed" and number(row.get("unit_cost")) > 0]
    return min(confirmed, key=lambda row: number(row.get("unit_cost"))) if confirmed else None


def build_business_feasibility(research_dir: Path, rules_path: Path) -> dict:
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    products = read_csv(research_dir / "top_products.csv")
    seed = next((row for row in products if row.get("competitor_type") == "seed"), products[0] if products else {})
    price = number(seed.get("price"))
    quote_path = research_dir / "supplier_quotes.csv"
    ensure_quote_template(quote_path)
    quote = best_confirmed_quote(read_csv(quote_path))
    source = "confirmed_supplier_quote" if quote else "system_estimate"
    unit_cost = number(quote.get("unit_cost")) if quote else price * rules["default_cost_rate"]
    packaging = number(quote.get("packaging_per_unit")) if quote else 0
    freight = number(quote.get("freight_per_unit")) if quote else price * rules["default_shipping_rate"]
    duty = number(quote.get("duty_per_unit")) if quote else 0
    fba_fee = number(quote.get("fba_fee_per_unit")) if quote else max(rules["minimum_fba_fee"], price * rules["default_fba_fee_rate"])
    referral = price * rules["referral_fee_rate"]
    returns = price * rules["assumed_return_rate"] * rules["assumed_return_loss_rate"]
    coupon = price * rules["assumed_coupon_rate"]
    storage = price * rules["assumed_storage_rate"]
    pre_ad_profit = price - unit_cost - packaging - freight - duty - fba_fee - referral - returns - coupon - storage
    ad_cost = price * rules["target_ad_cost_rate"]
    contribution_profit = pre_ad_profit - ad_cost
    contribution_margin = contribution_profit / price if price else 0
    moq = number(quote.get("moq")) if quote else 0
    lead_time = number(quote.get("lead_time_days")) if quote else 0
    production_days = number(quote.get("production_days")) if quote else 0
    cash_cycle = lead_time + production_days if quote else None
    initial_cash = (unit_cost + packaging) * moq + number(quote.get("sample_cost")) if quote else None
    status = "supplier_validated" if quote else "needs_supplier_quote"
    return {
        "status": status,
        "data_source": source,
        "target_price": round(price, 2),
        "unit_cost": round(unit_cost, 2),
        "landed_non_ad_cost": round(price - pre_ad_profit, 2),
        "pre_ad_profit": round(pre_ad_profit, 2),
        "break_even_acos": round(max(0, pre_ad_profit / price), 4) if price else 0,
        "target_ad_cost_rate": rules["target_ad_cost_rate"],
        "contribution_profit": round(contribution_profit, 2),
        "contribution_margin": round(contribution_margin, 4),
        "meets_margin_target": contribution_margin >= rules["minimum_contribution_margin"],
        "moq": moq or None,
        "initial_inventory_cash": round(initial_cash, 2) if initial_cash is not None else None,
        "cash_cycle_days": round(cash_cycle) if cash_cycle is not None else None,
        "cash_cycle_within_target": cash_cycle <= rules["maximum_cash_cycle_days"] if cash_cycle is not None else None,
        "missing_evidence": [] if quote else ["supplier quote", "MOQ", "lead time", "packaging", "freight", "certifications"],
    }


def write_business_feasibility(research_dir: Path, rules_path: Path) -> dict:
    result = build_business_feasibility(research_dir, rules_path)
    output = research_dir / "business_feasibility.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
