"""Current-snapshot, title-first comparisons across discovery categories.

Source validation/archives remain evidence snapshots. This derived view never
adds their aggregate sales or mixes historical scan runs.
"""

import json
from collections import defaultdict
from pathlib import Path

from category_shape_validation import (
    build_rows_from_products, extract_category_report_products,
    load_category_report, normalized_category_product, product_exclusion_reason,
    resolve_discovery_run_dir, validation_category_exclusion_reason,
)


def market_identity(row):
    family = row.get("product_family", "")
    if family in {"cocktail_jigger", "cocktail_tool_kit"}:
        return "barware", "调酒器具"
    if family.startswith("unresolved:") or not family:
        return "unresolved", "用途待确认"
    return family, row.get("product_family_label") or row.get("product_form", "")


def merge_product_observations(observations, rules, snapshot_id):
    """Input is one scan's (source metadata, normalized product) observations."""
    by_asin = defaultdict(list)
    for source, product in observations:
        if source.get("snapshot_id", snapshot_id) != snapshot_id:
            raise ValueError("Product comparisons cannot mix scan snapshots")
        asin = str(product.get("asin", "")).strip().upper()
        if asin:
            by_asin[asin].append((source, {**product, "asin": asin}))
    products = []
    provenance = {}
    metric_conflicts = set()
    for asin, entries in sorted(by_asin.items()):
        # Prefer explicit title identity, then stable source order, never best sales.
        entries.sort(key=lambda entry: (
            entry[1].get("classification_status") == "unresolved",
            entry[1].get("classification_status") == "needs_specification",
            str(entry[0]["id"]), json.dumps(entry[1], sort_keys=True),
        ))
        chosen = dict(entries[0][1])
        identities = {p.get("product_family") for _, p in entries
                      if p.get("classification_status") != "unresolved"}
        if len(identities) > 1:
            chosen.update(product_family="unresolved:conflicting_titles",
                          product_family_label="用途待确认", product_form="conflicting titles",
                          product_form_label="来源标题用途冲突", classification_status="unresolved")
        reasons = sorted({reason for _, p in entries
                          if (reason := product_exclusion_reason(p, rules.get("_scoring_rules", {})))})
        if reasons:
            chosen["source_exclusion_reason"] = "; ".join(reasons)
        if len({tuple(p.get(k) for k in ("monthly_sales", "reviews", "price"))
                for _, p in entries}) > 1:
            metric_conflicts.add(asin)
        provenance[asin] = {str(s["id"]): s for s, _ in entries}
        products.append(chosen)

    families = defaultdict(list)
    for product in products:
        families[product["product_family"]].append(product)
    output = []
    for family, members in sorted(families.items()):
        sources = {key: value for p in members for key, value in provenance[p["asin"]].items()}
        source_list = [sources[key] for key in sorted(sources)]
        first = source_list[0]
        seed = {"source_category_id": first["id"], "source_category_name": first["name"],
                "category": first["path"]}
        rows = build_rows_from_products([seed], members, rules, snapshot_id)
        market_id, market_label = market_identity(members[0])
        for row in rows:
            form_members = [p for p in members if p["product_form"] == row["product_form"]]
            form_sources = {key: value for p in form_members for key, value in provenance[p["asin"]].items()}
            row.update(
                market_id=market_id, market_label=market_label,
                source_categories=[form_sources[key] for key in sorted(form_sources)],
                family_sources=source_list, snapshot_id=snapshot_id,
                family_duplicate_count=sum(len(provenance[p["asin"]]) - 1 for p in members),
                family_conflict_count=sum(p["asin"] in metric_conflicts for p in members),
            )
            output.append(row)
    return output


def build_current_product_groups(root, validation_rows):
    root = Path(root)
    rules = json.loads((root / "config/category_shape_validation_rules.json").read_text())
    rules["_scoring_rules"] = json.loads((root / rules.get("scoring_rules_path", "config/scoring_rules.json")).read_text())
    rules["discovery_runs_root"] = str(root / rules.get("discovery_runs_root", "archive/discovery_runs"))
    rules["category_exclusions_path"] = str(root / rules.get("category_exclusions_path", "config/category_exclusions.json"))
    run_ids = {row.get("validation_run_id") for row in validation_rows if row.get("validation_run_id")}
    if len(run_ids) > 1:
        return validation_rows
    if run_ids:
        rules["discovery_run_id"] = next(iter(run_ids))
    run_dir = resolve_discovery_run_dir(rules)
    if not run_dir:
        return validation_rows
    categories = {}
    for row in validation_rows:
        if row.get("source_category_id") and row.get("data_quality") == "category_top100":
            categories.setdefault(str(row["source_category_id"]), row)
    observations, loaded, excluded = [], set(), set()
    for node_id, row in sorted(categories.items()):
        if validation_category_exclusion_reason(row.get("category_path", ""), rules):
            excluded.add(node_id)
            continue
        raw = extract_category_report_products(load_category_report(row, run_dir))
        if not raw:
            continue
        loaded.add(node_id)
        source = {"id": node_id, "name": row.get("source_category_name") or node_id,
                  "path": row.get("category_path", ""), "snapshot_id": run_dir.name}
        brands = [p.get("brand", "") for p in raw]
        # One ASIN observation per source, independent of provider row order.
        per_source = {}
        for product in sorted(raw, key=lambda p: json.dumps(p, sort_keys=True)):
            normalized = normalized_category_product(product, source["path"], brands)
            per_source.setdefault(normalized["asin"].upper(), normalized)
        observations.extend((source, p) for p in per_source.values())
    merged = merge_product_observations(observations, rules, run_dir.name)
    # Missing raw evidence stays source-specific instead of merging summary rows.
    for row in validation_rows:
        if str(row.get("source_category_id")) not in loaded | excluded:
            merged.append({**row, "market_id": "source:" + str(row.get("source_category_id") or row.get("category_path")),
                           "market_label": row.get("source_category_name") or row.get("category_path", ""),
                           "comparison_status": "source_only"})
    return merged
