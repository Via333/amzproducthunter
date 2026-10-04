"""Bounded product-name extraction from the item being sold in a title."""

import json
import re
from functools import lru_cache
from pathlib import Path

from product_taxonomy import normalize


@lru_cache(maxsize=1)
def product_name_entries():
    path = Path(__file__).resolve().parent / "config/title_product_names.json"
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


@lru_cache(maxsize=1)
def plural_words():
    # Only inflect vocabulary words; stripping a trailing s corrupts stainless/glass.
    nouns = set("brush box basket net pin stopper strainer scraper cutter stamp mat holster splitter duster mold mould tool cap lid holder rack bag tip faucet gasket seal filter cartridge glove cloth towel sponge beater mixer nozzle ring form spout pourer marker charm drawer canister stencil sieve sheet liner pan tray needle coupler washer sleeve pump roller part accessory".split())
    nouns.update(alias.split()[-1] for entry in product_name_entries() for alias in entry["aliases"])
    plurals = {}
    for noun in nouns:
        if noun.endswith("s"):
            continue
        suffix = noun + "es" if noun.endswith(("sh", "ch", "x")) else noun + "s"
        if noun.endswith("y") and noun[-2] not in "aeiou":
            suffix = noun[:-1] + "ies"
        plurals[suffix] = noun
    return {**plurals, "knives": "knife", "leaves": "leaf", "sieves": "sieve",
            "presses": "press", "glasses": "glass", "bodies": "body"}


def normalize_product_name(value):
    words = plural_words()
    return " ".join(words.get(token, token) for token in normalize(str(value or "").replace("&", " and ")).split())


def title_subject(title):
    # Usage/compatibility and included accessories are not independent title subjects.
    return re.split(r"\b(?:for|with|includes?|including|featuring|compatible(?:\s+with)?|fits?|suitable|ideal|perfect)\b",
                    str(title or ""), maxsplit=1, flags=re.IGNORECASE)[0]


@lru_cache(maxsize=256)
def alias_pattern(aliases):
    values = sorted({normalize_product_name(a) for a in aliases}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(a) for a in values) + r")\b")


def find_title_product(title, family_rules):
    """Resolve synonyms and primary/accessory nouns without trusting provider types."""
    text = normalize_product_name(title)
    subject = title_subject(title)
    entries = product_name_entries()
    # Preserve punctuation until the primary item is found; later clauses often list uses.
    for clause in re.split(r"[,;|]", subject):
        head = normalize_product_name(clause)
        matches = []
        for index, entry in enumerate(entries):
            if entry.get("context") and not re.search(r"\b(?:" + entry["context"] + r")\b", text):
                continue
            match = alias_pattern(tuple(entry["aliases"])).search(head)
            if not match:
                match = next((m for pattern in entry.get("patterns", []) if (m := re.search(pattern, head))), None)
            if match:
                matches.append((match.start(), -match.end(), index, entry, match))
        for index, (key, label, pattern) in enumerate(family_rules, start=len(entries)):
            match = re.search(pattern, head)
            if not match or (key == "duster" and re.search(r"\b(?:sugar|flour|baking|cocoa|powdered)\b", text)):
                continue
            matches.append((match.start(), -match.end(), index, {"key": key, "label": label}, match))
        if not matches:
            continue
        matches.sort(key=lambda item: item[:3])
        chosen = matches[0]
        # In "Garbage Disposal Rubber Sink Seal", the host modifies the seal.
        for candidate in matches:
            between = head[chosen[4].end():candidate[4].start()]
            overlaps = candidate[4].start() < chosen[4].end()
            if ((candidate[3].get("accessory") or overlaps) and candidate[4].end() > chosen[4].end()
                    and not re.search(r"\b(?:and|plus)\b", between)):
                chosen = candidate
        entry = chosen[3]
        # Unknown accessory nouns must not silently turn into their host product.
        tail = head[chosen[4].end():]
        if not entry.get("accessory") and re.match(r"\s+(?:(?:replacement|storage|carrying|protective)\s+)?(?:case|cover|holder|part|lid|adapter)\b", tail):
            return {"unresolved_accessory": True}
        refinements = [v for v in entry.get("refinements", []) if re.search(v["pattern"], text)]
        if len(refinements) == 1:
            entry = {k: v for k, v in refinements[0].items() if k != "pattern"}
        return {**entry, "matched_name": chosen[4].group(), "subject": head}
    return {}
