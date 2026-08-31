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


def first_match(text: str, entries: list[dict]) -> str:
    for entry in entries:
        if any(normalize(term) in text for term in entry.get("terms", []) if normalize(term)):
            return str(entry.get("label") or "").strip()
    return ""


def category_leaf(category: object) -> str:
    parts = re.split(r"\s*(?:>|/|›)\s*", str(category or ""))
    return normalize(parts[-1] if parts else "")


def generic_base(title: str, category: str, product_type: str, taxonomy: dict) -> str:
    product_type_text = normalize(product_type)
    if product_type_text and product_type_text not in {"product", "unknown", "other"}:
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
) -> str:
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
    text = normalize(" ".join(str(value or "") for value in (signal_title, category, product_type_text)))
    override = first_match(text, taxonomy.get("overrides", []))
    if override:
        return override
    modifier = first_match(text, taxonomy.get("modifiers", []))
    base = generic_base(title_text, str(category or ""), product_type_text, taxonomy)
    if modifier and modifier not in base:
        return f"{modifier} {base}".strip()
    return base or "unknown"
