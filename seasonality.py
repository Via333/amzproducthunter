"""Holiday seasonality read from listing titles. No provider calls.

A Top100 snapshot taken in September or October counts Halloween and Christmas
decorations at their peak, so their monthly sales say little about the rest of
the year. Only titles themed for a holiday count: a holiday named in the title
subject or directly before a decoration noun. Gift positioning ("Christmas
gifts for women") and all-season bundles naming several holidays do not.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from product_taxonomy import normalize
from title_product_names import title_subject


ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=4)
def load_seasonality_rules(path: str = "config/seasonality_rules.json") -> dict:
    rules_path = Path(path)
    if not rules_path.is_absolute():
        rules_path = ROOT / rules_path
    return json.loads(rules_path.read_text(encoding="utf-8"))


def alternation(terms) -> str:
    values = sorted({normalize(term) for term in terms if normalize(term)}, key=len, reverse=True)
    return "|".join(re.escape(value) for value in values)


@lru_cache(maxsize=4)
def compiled_rules(path: str = "config/seasonality_rules.json") -> tuple:
    rules = load_seasonality_rules(path)
    holidays = [(key, value.get("season", key), re.compile(rf"\b(?:{alternation(value.get('terms', []))})\b"))
                for key, value in rules.get("holidays", {}).items()]
    themed = re.compile(rf"^\s*(?:{alternation(rules.get('themed_nouns', []))})\b")
    gifts = alternation(rules.get("gift_words", []))
    gift_after = re.compile(rf"^(?:\s+\w+){{0,3}}?\s+(?:{gifts})\b")
    gift_before = re.compile(rf"\b(?:{gifts})\s+(?:for|idea|ideas)\b")
    return holidays, themed, gift_after, gift_before, int(rules.get("all_season_min_seasons", 3))


def holiday_theme(title: object, path: str = "config/seasonality_rules.json") -> str:
    """Return the holiday a listing is themed for, or '' when it is not holiday-specific."""

    holidays, themed, gift_after, gift_before, all_season_min = compiled_rules(path)
    text = normalize(title)
    subject = normalize(title_subject(title))
    seasons, themed_for = set(), []
    for key, season, pattern in holidays:
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        seasons.add(season)
        for match in matches:
            after = text[match.end():]
            if gift_after.match(after) or gift_before.search(text[max(0, match.start() - 40):match.start()]):
                continue
            # The normalized subject is a prefix of the normalized title.
            if match.end() <= len(subject) or themed.match(after):
                themed_for.append(key)
                break
    # "Valentine St. Patrick Easter 4th of July Halloween Christmas outfits" sell all year;
    # "Halloween Thanksgiving Christmas" covers are still a fourth-quarter product.
    if len(seasons) >= all_season_min:
        return ""
    return themed_for[0] if themed_for else ""


def holiday_label(key: str, path: str = "config/seasonality_rules.json") -> str:
    return load_seasonality_rules(path).get("holidays", {}).get(key, {}).get("label", key)
