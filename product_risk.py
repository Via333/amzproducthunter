#!/usr/bin/env python3
"""Shared title/category risk signals used by discovery and scoring."""

from __future__ import annotations

import re

from product_exclusions import configured_terms


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
    "salt and pepper tools": r"\b(?:salt|pepper|spice)\s+(?:mill|mills|grinder|grinders|shaker|shakers|dispenser|dispensers)\b",
    "cookware": r"\b(?:camping\s+)?(?:cookware|cooking\s+set|cook\s+pot|pots?\s*(?:,|&|and)\s*pans?)\b",
    "tableware": r"\b(?:dishes?\s*&\s*utensils|dinnerware|silverware|flatware|paper\s+plates?|plastic\s+(?:plates?|bowls?)|camping\s+(?:plates?|bowls?))\b",
    "food preparation tool": r"\b(?:potato|french\s+fry)\s+(?:cutter|slicer)\b",
    "garlic preparation tool": r"\bgarlic\s+(?:press|presses|grinder|grinders|cutter|cutters)\b",
    "beverage contact": r"\b(?:(?:wine|liquor)\s+(?:bottle\s+)?pourers?|ice\s+buckets?)\b",
    "edible material": r"\bedible\b",
    "combustion appliance": r"\b(?:camping\s+(?:stove|stoves|grill|grills)|gas\s+stove|charcoal\s+grill|fire\s+grill)\b",
    "weapon accessory": r"\b(?:concealed\s+carry|belly\s+band\s+holster|shoulder\s+holster)\b",
    "medical bathroom aid": r"\b(?:raised\s+toilet\s+seats?|toilet\s+seat\s+risers?|toilet\s+seat\s+risers?\s+for\s+seniors|handicap\s+bathroom\s+safety)\b",
    "food contact": r"\bfood\s+contact\b",
}

HARD_OVERSIZE_COMBINATIONS = [
    r"\b(?:extra[ -]?large|oversized|jumbo)\b.*\b(?:storage\s+)?(?:box|bin|container|hamper|chest)\b",
    r"\b(?:storage\s+)?(?:box|bin|container|hamper|chest)\b.*\b(?:extra[ -]?large|oversized|jumbo)\b",
    r"\blarge\b.*\b(?:storage\s+)?(?:boxes|bins|baskets|containers|hampers|box|bin|basket|container|hamper|blanket\s+baskets?)\b",
    r"\b(?:storage\s+)?(?:boxes|bins|baskets|containers|hampers|box|bin|basket|container|hamper|blanket\s+baskets?)\b.*\blarge\b",
    r"\b(?:large\s+capacity|heavy[ -]?duty\s+rolling|rolling)\b.*\b(?:storage\s+(?:rack|organizer)|ball\s+cart|sports\s+equipment\s+organizer)\b",
    r"\b(?:storage\s+(?:rack|organizer)|ball\s+cart|sports\s+equipment\s+organizer)\b.*\b(?:with\s+wheels|rolling|large\s+capacity)\b",
    r"\b(?:[4-9]|[1-9]\d)[ -]?(?:pack|count|pcs?)\b.*\b(?:storage|closet|classroom)\b.*\b(?:box|bin|basket|cube)s?\b",
    r"\bpack\s+of\s+(?:[4-9]|[1-9]\d)\b.*\b(?:storage\s+)?(?:box|bin|basket|container)s?\b",
    r"\b(?:storage\s+)?(?:box|bin|basket|container)s?\b.*\bpack\s+of\s+(?:[4-9]|[1-9]\d)\b",
    r"\bset\s+of\s+\(?(?:[4-9]|[1-9]\d)\)?\b.*\b(?:storage\s+)?(?:box|bin|basket|container)s?\b",
    r"\bwheeled\b.*\b(?:box|bin|container|stacker|hamper)\b",
    r"\b(?:spin\s+mop|mop\s+system)\b.*\b(?:bucket|wringer)\b",
    r"\b(?:bucket|wringer)\b.*\b(?:spin\s+mop|mop\s+system)\b",
    r"\bmops?\s*(?:and|&)\s*bucket\s+sets?\b",
    r"\bmop\s+and\s+bucket\s+(?:set|system)\b",
    r"\b(?:broom|mop|brush|scrubber)\b.*\b(?:long\s+handle|pole)\b",
    r"\b(?:long\s+handle|pole)\b.*\b(?:broom|mop|brush|scrubber)\b",
    r"\b(?:outdoor|corn|push|angle|floor)\s+broom\b",
    r"\b(?:sponge|string|dust|floor)\s+mop\b",
    r"\btelescoping\s+brush\b",
    r"\blarge\b.*\b(?:ice\s+)?(?:bucket|tub|basin)\b",
]


def combined_text(title: object, category: object = "", brand: object = "") -> str:
    return " ".join(str(value or "") for value in (title, category, brand)).lower()


def fragile_signal(title: object, category: object = "", brand: object = "") -> tuple[int, str]:
    text = combined_text(title, category, brand)
    for term in configured_terms("fragile_terms"):
        if re.search(rf"\b{re.escape(term)}\b", text):
            return 90, term
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
    for term in configured_terms("compliance_terms"):
        if term in text:
            return 90, term
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
    for term in configured_terms("oversize_hard_terms"):
        if term in text:
            return 90, term
    for term in configured_terms("oversize_soft_terms"):
        if term in text:
            return 70, term
    for match in re.finditer(r"\b(\d{1,2}(?:\.\d+)?)\s*(?:gallon|gal)\b", text):
        capacity = float(match.group(1))
        if capacity >= 3:
            return 90, f"{capacity:g} gallon capacity"
        if capacity >= 2:
            return 70, f"{capacity:g} gallon capacity"
    for match in re.finditer(r"\b(\d{1,3})\s*(?:qt|quart)s?\b", text):
        capacity = int(match.group(1))
        if capacity >= 50:
            return 90, f"{capacity} quart capacity"
        if capacity >= 20:
            return 70, f"{capacity} quart capacity"
        if capacity >= 10 and re.search(r"\b(?:bucket|tub|basin)\b", text):
            return 70, f"{capacity} quart bucket capacity"
    for match in re.finditer(r"\b(\d{1,3})\s*(?:l|liter|litre)s?\b", text):
        capacity = int(match.group(1))
        if capacity >= 40:
            return 90, f"{capacity} liter capacity"
        if capacity >= 25:
            return 70, f"{capacity} liter capacity"
    for match in re.finditer(r"\b(\d{2,3}(?:\.\d+)?)\s*(?:in|inch|inches)\b", text):
        dimension = float(match.group(1))
        if dimension >= 24:
            return 70, f"{dimension:g} inch dimension"
    for match in re.finditer(r"\b(\d{2,3}(?:\.\d+)?)\s*[\"“”″]", text):
        dimension = float(match.group(1))
        if dimension >= 24:
            return 70, f"{dimension:g} inch dimension"
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
