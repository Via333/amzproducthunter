"""Scope-bound human feedback. A SKU rejection is not a category rejection."""
import csv
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sqlite3

from research_workbench import opportunity_keys

PROTECTED = {"invalidated_by_rule", "suppressed_by_user", "rejected_by_research",
             "ip_review_required", "seasonal_confirmed"}


@lru_cache(maxsize=8)
def load_feedback(root):
    root = Path(root)
    holds = []
    for path in sorted((root / "research").glob("*/risk_review.json")):
        data = json.loads(path.read_text())
        if data.get("site", "US") != "US":
            continue
        for key, state in (("patent", "ip_review_required"), ("certification", "rejected_by_research"),
                           ("seasonality", "seasonal_confirmed")):
            check = data.get(key, {})
            if check.get("clearance") != "blocked" and check.get("risk") != "high":
                continue
            scope = data.get("scope", {})
            asins = set(scope.get("asins", [])) | {data.get("asin") or path.parent.name}
            # Only explicit shape scope propagates beyond the referenced SKUs.
            holds.append({"asins": asins, "shape": scope.get("shape_key", ""),
                          "state": state, "source": str(path.relative_to(root))})
    for path in sorted((root / "research").glob("*/required_checks.json")):
        data = json.loads(path.read_text())
        if data.get("site", "US") != "US":
            continue
        for key, state in (("patent", "ip_review_required"), ("certification", "rejected_by_research"),
                           ("seasonality", "seasonal_confirmed")):
            check = data.get("checks", {}).get(key, {})
            if check.get("review_status") != "reviewed":
                continue
            if check.get("clearance") == "blocked" or check.get("risk") == "high":
                holds.append({"asins": {data.get("asin") or path.parent.name}, "shape": "",
                              "state": state, "source": str(path.relative_to(root))})
    index = root / "archive/product_research_index.csv"
    if index.exists():
        with index.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("archive_status") == "rejected":
                    holds.append({"asins": {row.get("asin") or row.get("source_asin")}, "shape": "",
                                  "state": "rejected_by_research", "source": "archive/product_research_index.csv"})
    dbpath = root / ".local/research-workbench.sqlite3"
    dismissals = []
    if dbpath.exists():
        with sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True) as db:
            dismissals = list(db.execute("SELECT kind,item_key FROM dismissals WHERE discarded=1"))
    return holds, dismissals


def feedback_state(row, root=None):
    root = Path(root or os.environ.get("AMZ_SOURCE_ROOT") or Path(__file__).resolve().parent)
    holds, dismissals = load_feedback(str(root.resolve()))
    asins = set(re.findall(r"\b[A-Z0-9]{10}\b", " ".join(str(row.get(k, "")) for k in (
        "seed_asin", "seed_asins", "form_reference_asins", "form_member_asins"))))
    keys = opportunity_keys(row)
    matched = [h for h in holds if (h["shape"] and h["shape"] == keys["shape"]) or asins.intersection(h["asins"])]
    if matched:
        matched.sort(key=lambda h: h["state"] != "ip_review_required")
        return matched[0]["state"], matched[0]["source"]
    for kind, key in dismissals:
        if (kind == "asin" and key in asins) or (kind != "asin" and keys.get(kind) == key):
            return "suppressed_by_user", "workbench:" + kind + ":" + key
    return "", ""
