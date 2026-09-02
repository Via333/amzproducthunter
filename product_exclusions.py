#!/usr/bin/env python3
"""Single source of truth for product-level text exclusions."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = "config/product_exclusions.json"


@lru_cache(maxsize=8)
def load_product_exclusions(path: str = DEFAULT_CONFIG_PATH) -> dict:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    return json.loads(config_path.read_text(encoding="utf-8"))


def normalize_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def contains_term(value: object, terms: list[object]) -> str:
    normalized_value = normalize_text(value)
    for raw_term in terms:
        term = normalize_text(raw_term)
        if term and term in normalized_value:
            return str(raw_term)
    return ""


def has_valid_category(category: object) -> bool:
    text = normalize_text(category)
    return bool(text and text not in {"unknown", "none", "null", "n a", "na"})


def extract_brand(row: dict) -> str:
    brand = str(row.get("brand", "") or "").strip()
    if brand:
        return brand
    notes = str(row.get("notes", "") or "")
    match = re.search(r"(?:^|[;,]\s*)brand\s+([^;,]+)", notes, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def starts_with_brand(title: object, brand: object) -> bool:
    title_norm = normalize_text(title)
    brand_norm = normalize_text(brand)
    return bool(brand_norm and (title_norm == brand_norm or title_norm.startswith(f"{brand_norm} ")))


def hard_exclusion_reason(
    row: dict,
    config_path: str = DEFAULT_CONFIG_PATH,
    require_valid_category: bool | None = None,
) -> str:
    config = load_product_exclusions(config_path)
    require_category = config.get("require_valid_category", False) if require_valid_category is None else require_valid_category
    if require_category and not has_valid_category(row.get("category", "")):
        return "missing or invalid category evidence"
    title_match = contains_term(row.get("product_name", ""), config.get("title_contains", []))
    if title_match:
        return f"excluded title term: {title_match}"
    category_match = contains_term(row.get("category", ""), config.get("category_contains", []))
    if category_match:
        return f"excluded category: {category_match}"
    brand_match = contains_term(extract_brand(row), config.get("brand_contains", []))
    if brand_match:
        return f"excluded brand: {brand_match}"
    return ""


def brand_moat_reason(row: dict, config_path: str = DEFAULT_CONFIG_PATH) -> str:
    config = load_product_exclusions(config_path).get("brand_moat", {})
    if not config.get("enabled", False):
        return ""
    title = row.get("product_name", "")
    brand = extract_brand(row)
    for blocked in config.get("brands", []):
        if brand and normalize_text(brand) == normalize_text(blocked):
            return f"brand moat: {blocked}"
        if starts_with_brand(title, blocked):
            return f"brand moat: {blocked}"
    return ""


def configured_terms(name: str, config_path: str = DEFAULT_CONFIG_PATH) -> list[str]:
    return [str(value).lower() for value in load_product_exclusions(config_path).get(name, []) if str(value).strip()]
