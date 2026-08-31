#!/usr/bin/env python3
"""Shared title/category risk signals used by discovery and scoring."""

from __future__ import annotations

import re


HARD_FRAGILE_PATTERNS = {
    "glass": r"\bglass\b",
    "ceramic": r"\bceramic\b",
    "porcelain": r"\bporcelain\b",
    "crystal": r"\bcrystal\b",
    "stoneware": r"\bstoneware\b",
    "pottery": r"\bpottery\b",
    "terracotta": r"\bterra\s*cotta\b|\bterracotta\b",
    "earthenware": r"\bearthenware\b",
    "mirror": r"\bmirror(?:ed|s)?\b",
}

HARD_FOOD_CONTACT_PATTERNS = {
    "food storage": r"\bfood\s+(?:storage\s+)?(?:bag|bags|box|boxes|container|containers|jar|jars|wrap|wraps)\b",
    "bread storage": r"\bbread\s+(?:storage\s+)?(?:bag|bags|box|boxes|container|containers|keeper|keepers)\b",
    "produce storage": r"\bproduce\s+(?:storage\s+)?(?:bag|bags|container|containers)\b",
    "snack storage": r"\b(?:snack|sandwich|freezer)\s+(?:storage\s+)?(?:bag|bags|box|boxes|container|containers)\b",
    "food jar": r"\b(?:cookie|sweets|candy|sugar|flour|cereal)\s+(?:storage\s+)?(?:jar|jars|container|containers|canister|canisters)\b",
    "beeswax food storage": r"\bbeeswax\s+(?:bread\s+)?(?:bag|bags|wrap|wraps)\b",
    "food contact": r"\bfood\s+contact\b",
}

HARD_OVERSIZE_COMBINATIONS = [
    r"\b(?:extra[ -]?large|oversized|jumbo)\b.*\b(?:storage\s+)?(?:box|bin|container|hamper|chest)\b",
    r"\b(?:storage\s+)?(?:box|bin|container|hamper|chest)\b.*\b(?:extra[ -]?large|oversized|jumbo)\b",
    r"\bwheeled\b.*\b(?:box|bin|container|stacker|hamper)\b",
]


def combined_text(title: object, category: object = "", brand: object = "") -> str:
    return " ".join(str(value or "") for value in (title, category, brand)).lower()


def fragile_signal(title: object, category: object = "", brand: object = "") -> tuple[int, str]:
    text = combined_text(title, category, brand)
    for label, pattern in HARD_FRAGILE_PATTERNS.items():
        if re.search(pattern, text):
            return 90, label
    return 0, ""


def infer_fragile_risk(
    title: object,
    category: object = "",
    brand: object = "",
    default: float = 20,
) -> float:
    risk, _ = fragile_signal(title, category, brand)
    return max(float(default or 0), float(risk))


def food_contact_signal(title: object, category: object = "", brand: object = "") -> tuple[int, str]:
    text = combined_text(title, category, brand)
    for label, pattern in HARD_FOOD_CONTACT_PATTERNS.items():
        if re.search(pattern, text):
            return 90, label
    return 0, ""


def infer_compliance_risk(
    title: object,
    category: object = "",
    brand: object = "",
    default: float = 25,
) -> float:
    risk, _ = food_contact_signal(title, category, brand)
    return max(float(default or 0), float(risk))


def oversize_signal(title: object, category: object = "", brand: object = "") -> tuple[int, str]:
    text = combined_text(title, category, brand)
    for match in re.finditer(r"\b(\d{1,3})\s*(?:qt|quart)s?\b", text):
        capacity = int(match.group(1))
        if capacity >= 50:
            return 90, f"{capacity} quart capacity"
        if capacity >= 30:
            return 70, f"{capacity} quart capacity"
    for pattern in HARD_OVERSIZE_COMBINATIONS:
        if re.search(pattern, text):
            return 90, "large storage form"
    return 0, ""


def infer_oversize_risk(
    title: object,
    category: object = "",
    brand: object = "",
    default: float = 20,
) -> float:
    risk, _ = oversize_signal(title, category, brand)
    return max(float(default or 0), float(risk))


def has_valid_category(category: object) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", str(category or "").lower()).strip()
    return bool(text and text not in {"unknown", "none", "null", "n a", "na"})
