#!/usr/bin/env python3
"""Evidence-backed demand classification from Amazon review rows."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean


DEMAND_FIELDS = [
    "product_form",
    "demand_type",
    "theme",
    "sentiment",
    "mention_count",
    "verified_count",
    "avg_rating",
    "user_profiles",
    "use_scenes",
    "example_asins",
    "latest_review_date",
    "evidence_excerpt",
]


def number(value: object) -> float:
    try:
        return float(str(value or "0"))
    except ValueError:
        return 0.0


def matching_labels(text: str, entries: list[dict]) -> list[str]:
    return [entry["label"] for entry in entries if any(term in text for term in entry.get("terms", []))]


def demand_type(text: str, rating: float, config: dict) -> str:
    if any(term in text for term in config.get("fulfillment_terms", [])):
        return "fulfillment_noise"
    if any(term in text for term in config.get("reverse_terms", [])):
        return "reverse"
    if rating >= 4 and any(term in text for term in config.get("delight_terms", [])):
        return "delight"
    if rating <= 3 and any(term in text for term in config.get("basic_terms", [])):
        return "basic"
    if any(term in text for term in config.get("expected_terms", [])):
        return "expected"
    return "uncategorized"


def analyze_demand(review_rows: list[dict], product_rows: list[dict], config_path: Path) -> list[dict]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    form_by_asin = {row.get("asin", ""): row.get("product_form", "unknown") for row in product_rows}
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for review in review_rows:
        text = " ".join(
            str(review.get(field) or "").lower() for field in ("review_title", "review_text")
        )
        rating = number(review.get("rating"))
        themes = matching_labels(text, config.get("themes", [])) or ["other"]
        dtype = demand_type(text, rating, config)
        sentiment = "positive" if rating >= 4 else "negative" if rating and rating <= 3 else "neutral"
        form = form_by_asin.get(review.get("asin") or review.get("review_target_asin"), "unknown")
        profiles = matching_labels(text, config.get("profiles", []))
        scenes = matching_labels(text, config.get("scenes", []))
        enriched = {**review, "profiles": profiles, "scenes": scenes, "text": text, "rating_value": rating}
        for theme in themes:
            groups[(form, dtype, theme, sentiment)].append(enriched)

    output = []
    for (form, dtype, theme, sentiment), items in groups.items():
        ratings = [item["rating_value"] for item in items if item["rating_value"] > 0]
        profiles = sorted({value for item in items for value in item["profiles"]})
        scenes = sorted({value for item in items for value in item["scenes"]})
        asins = sorted({str(item.get("asin") or item.get("review_target_asin") or "") for item in items})
        dates = sorted(str(item.get("review_date") or "") for item in items if item.get("review_date"))
        excerpt = next((str(item.get("review_text") or item.get("review_title") or "").strip() for item in items), "")
        output.append(
            {
                "product_form": form,
                "demand_type": dtype,
                "theme": theme,
                "sentiment": sentiment,
                "mention_count": len(items),
                "verified_count": sum(str(item.get("verified_purchase", "")).lower() in {"1", "true", "yes"} for item in items),
                "avg_rating": round(mean(ratings), 2) if ratings else 0,
                "user_profiles": "; ".join(profiles),
                "use_scenes": "; ".join(scenes),
                "example_asins": "; ".join(value for value in asins if value)[:160],
                "latest_review_date": dates[-1] if dates else "",
                "evidence_excerpt": excerpt[:240],
            }
        )
    output.sort(key=lambda row: (-int(row["mention_count"]), row["product_form"], row["theme"]))
    return output
