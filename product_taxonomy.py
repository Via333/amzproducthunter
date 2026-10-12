#!/usr/bin/env python3
"""Configurable product-form classification with generic fallbacks."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=8)
def load_taxonomy(path: str = "config/product_taxonomy.json") -> dict:
    taxonomy_path = Path(path)
    if not taxonomy_path.is_absolute():
        taxonomy_path = ROOT / taxonomy_path
    return json.loads(taxonomy_path.read_text(encoding="utf-8"))


def normalize(value: object) -> str:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def barware_title_identity(title: object) -> dict:
    """Identify measuring tools and mixed bar kits from titles, not vendor types."""
    text = normalize(title)
    head = re.split(r"\b(?:for|with|includes|including|featuring)\b", text, maxsplit=1)[0]
    jigger = re.search(r"\bjiggers?\b", text)
    kit = re.search(
        r"\b(?:cocktail shaker|cocktail|bartender|bartending|mixology|bar tool|bar tools|bar)\s+(?:sets?|kits?)\b",
        head,
    )
    shaker = re.search(r"\b(?:cocktail|boston|cobbler)\s+shakers?\b", head)
    # An included jigger does not turn a shaker kit into a measuring-tool form.
    is_kit = bool(kit and (not jigger or kit.start() < jigger.start()))
    is_kit = is_kit or bool(shaker and jigger and shaker.start() < jigger.start())
    is_kit = is_kit or bool(
        jigger and re.search(r"\bwith\s+(?:a\s+)?(?:cocktail|boston|cobbler)\s+shakers?\b", text)
    )
    if is_kit:
        return {
            "product_family": "cocktail_tool_kit", "product_family_label": "综合调酒工具套装",
            "product_form": "cocktail shaker set" if re.search(r"\bshakers?\b", text) else "bar tool set",
            "product_form_label": "综合调酒工具套装",
            "classification_status": "title_rule",
        }
    if not jigger:
        # Some drink measures omit the word jigger entirely.
        if not re.search(r"\b(?:drink measur(?:e|er)|shot measuring cup|liquor measuring cup|spirit measure)\b", text):
            return {}
    elif re.search(r"\b(?:fishing|lure|lures|jigging|rod)\b", text) and not re.search(r"\b(?:cocktail|bartending|bartender)\b", text):
        return {}
    if re.search(r"\b(?:case|holder|stand|replacement lid)\s+for\b", text):
        return {}

    styles = []
    if re.search(r"\bjapanese\b", text):
        styles.append(("japanese", "日式"))
    if re.search(r"\bbell\b", text):
        styles.append(("bell", "钟形"))
    if len(styles) > 1:
        structure, structure_label = "mixed styles", "混合款式"
    elif styles:
        structure, structure_label = styles[0]
    elif re.search(r"\bangled\b", text):
        structure, structure_label = "angled", "斜面刻度"
    elif re.search(r"\b(?:stepped|step|handle|handled)\b", text):
        structure, structure_label = "stepped or handled", "阶梯/带柄"
    elif re.search(r"\b(?:double (?!walls?\b)|double$|dual sided|two sided)\b", text):
        structure, structure_label = "double sided", "双头"
    else:
        structure, structure_label = "structure unspecified", "结构待细分"

    # A copper/gold finish is not evidence of a solid-copper measuring cup.
    material, material_label = "material unspecified", "材质待确认"
    for pattern, key, label in [
        (r"\b(?:plastic|acrylic)\b", "plastic", "塑料"),
        (r"\b(?:crystal|glass cocktail|glass jigger)\b", "glass", "玻璃"),
        (r"\b(?:stainless|304ss)\b", "stainless steel", "不锈钢"),
        (r"\b100\s+(?:percent\s+)?copper\b", "copper", "铜"),
    ]:
        if re.search(pattern, text):
            material, material_label = key, label
            break
    return {
        "product_family": "cocktail_jigger", "product_family_label": "调酒量杯",
        "product_form": f"cocktail jigger - {material} - {structure}",
        "product_form_label": f"调酒量杯 · {material_label} · {structure_label}",
        "classification_status": "needs_specification" if "unspecified" in (material + structure) else "title_rule",
    }


def first_match_entry(text: str, entries: list[dict]) -> dict:
    for entry in entries:
        any_match = any(normalize(term) in text for term in entry.get("terms", []) if normalize(term))
        required_terms = [normalize(term) for term in entry.get("all_terms", []) if normalize(term)]
        all_match = bool(required_terms) and all(term in text for term in required_terms)
        if any_match or all_match:
            return entry
    return {}


def first_match(text: str, entries: list[dict]) -> str:
    return str(first_match_entry(text, entries).get("label") or "").strip()


def is_broad_product_type(product_type: object, taxonomy_path: str = "config/product_taxonomy.json") -> bool:
    """Department-level or placeholder Amazon types (OUTDOOR_LIVING, ABIS_*) say nothing about use."""
    text = normalize(product_type)
    taxonomy = load_taxonomy(taxonomy_path)
    broad = {normalize(value) for key in ("generic_forms", "generic_product_types", "broad_product_types")
             for value in taxonomy.get(key, [])}
    return not text or text in broad | {"product", "unknown", "other"} or text.startswith("abis ")


def category_leaf(category: object) -> str:
    parts = re.split(r"\s*(?:>|/|›)\s*", str(category or ""))
    return normalize(parts[-1] if parts else "")


def generic_base(title: str, category: str, product_type: str, taxonomy: dict) -> str:
    product_type_text = normalize(product_type)
    generic_product_types = {normalize(value) for value in taxonomy.get("generic_product_types", [])}
    if product_type_text and product_type_text not in {"product", "unknown", "other"} | generic_product_types:
        return product_type_text
    title_tokens = normalize(title).split()
    terms = taxonomy.get("generic_product_terms", [])
    for index, token in enumerate(title_tokens):
        if token in terms:
            previous = title_tokens[index - 1] if index > 0 else ""
            if previous and previous not in taxonomy.get("stopwords", []) and not previous.isdigit():
                return f"{previous} {token}"
            return token
    leaf = category_leaf(category)
    if leaf:
        return " ".join(leaf.split()[-3:])
    stopwords = set(taxonomy.get("stopwords", []))
    useful = [token for token in title_tokens if token not in stopwords and not token.isdigit()]
    return " ".join(useful[:3]) or "unknown"


def classify_product_form(
    title: object,
    category: object = "",
    product_type: object = "",
    taxonomy_path: str = "config/product_taxonomy.json",
    brand: object = "",
    category_brands: object = None,
) -> str:
    # Lazy import keeps the shared title identity independent of provider fallbacks.
    from product_family import product_identity
    from title_product_names import product_name_entries

    identity = product_identity(title)
    name_families = {entry["key"] for entry in product_name_entries()} | {"cocktail_jigger", "cocktail_tool_kit"}
    if identity["classification_status"] != "unresolved" and identity["product_family"] in name_families:
        return identity["product_form"]
    taxonomy = load_taxonomy(taxonomy_path)
    title_text = str(title or "")
    product_type_text = str(product_type or "")
    electricity_negated = bool(
        re.search(r"\b(?:no|without)\s+(?:electricity|electric|power)(?:\s+needed|\s+required)?\b", title_text, re.IGNORECASE)
    )
    if electricity_negated and re.search(r"\b(?:electric|powered)\b", product_type_text, re.IGNORECASE):
        product_type_text = ""
    signal_title = re.sub(
        r"\b(?:no|without)\s+(?:electricity|electric|power)(?:\s+needed|\s+required)?\b",
        "",
        title_text,
        flags=re.IGNORECASE,
    )
    normalized_title = normalize(signal_title)
    brands = [brand, *(category_brands or [])]
    for brand_name in sorted({normalize(value) for value in brands if normalize(value)}, key=len, reverse=True):
        normalized_title = re.sub(rf"\b{re.escape(brand_name)}\b", " ", normalized_title)
    normalized_title = re.sub(r"\s+", " ", normalized_title).strip()
    title_identity = barware_title_identity(normalized_title)
    if title_identity:
        return title_identity["product_form"]
    text = normalize(" ".join(str(value or "") for value in (normalized_title, product_type_text)))
    override = first_match(normalized_title, taxonomy.get("overrides", [])) or first_match(text, taxonomy.get("overrides", []))
    if override:
        return override
    modifier_entry = first_match_entry(text, taxonomy.get("modifiers", []))
    modifier = str(modifier_entry.get("label") or "").strip() if modifier_entry.get("include_in_key", True) else ""
    base = generic_base(normalized_title, str(category or ""), product_type_text, taxonomy)
    generic_product_types = {normalize(value) for value in taxonomy.get("generic_product_types", [])}
    product_type_is_specific = bool(
        normalize(product_type_text)
        and normalize(product_type_text) not in {"product", "unknown", "other"} | generic_product_types
    )
    if modifier and not product_type_is_specific and modifier not in base:
        return f"{modifier} {base}".strip()
    return base or "unknown"
