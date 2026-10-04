"""Revisit the functional product group from local category/keyword evidence."""

import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

from category_shape_validation import (
    new_entrant_metrics, normalized_category_product, product_exclusion_reason, to_float,
)
from product_opportunity_research import pack_count
from product_exclusions import has_valid_category


def read_json(path):
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def records(value):
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict) and row.get("asin")]
    if isinstance(value, dict):
        for key in ("result", "data", "top100_products", "products"):
            if key in value:
                return records(value[key])
    return []


def optional_number(value):
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def listing_date(value):
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def category_evidence(row, category_path, source):
    entries = []

    def add(value, field):
        if isinstance(value, str) and has_valid_category(value) and not value.strip().isdigit():
            entries.append({**source, "category_path": value.strip(), "field": field})

    for field in ("bsr_category", "category_path"):
        add(row.get(field), field)
    # Keyword results carry [name, node_id, rank, date], not a category string.
    top = row.get("top_category")
    parent = top[0] if isinstance(top, list) and top and isinstance(top[0], str) else ""
    for item in row.get("subcategory", []) or []:
        if isinstance(item, list) and item and isinstance(item[0], str) and has_valid_category(item[0]):
            add(" > ".join(filter(None, (parent, item[0]))), "subcategory")
    rank = re.fullmatch(r"Category:\s*(.+?),\s*Rank:\s*[\d,]+", str(row.get("category_rank") or ""), flags=re.I)
    if rank:
        add(rank.group(1), "category_rank")
    # Report context belongs only to its Top100 members, never all keyword hits.
    add(category_path, "source_category")
    return entries


def normalize_landscape_product(row, category_path, source):
    categories = category_evidence(row, category_path, source)
    category_path = categories[0]["category_path"] if categories else ""
    product = normalized_category_product(row, category_path)
    product["category_evidence"] = categories
    product["parent_asin"] = str(row.get("parent_asin") or "")
    product["source_exclusion_reasons"] = [row["source_exclusion_reason"]] if row.get("source_exclusion_reason") else []
    # The keyword endpoint uses singular "sale"; category/detail use "sales".
    product["monthly_sales"] = next((value for key in (
        "monthly_sales_volume", "monthly_sale_volume", "monthly_sales"
    ) if (value := optional_number(row.get(key))) is not None), None)
    product["price"] = optional_number(row.get("price"))
    product["online_date"] = listing_date(row.get("online_date")) or listing_date(row.get("listing_date"))
    product["metric_sources"] = {
        field: dict(source) for field in ("monthly_sales", "price", "online_date")
        if product[field] is not None and product[field] != ""
    }
    product["pack_count"] = row.get("visual_pack_count") or row.get("pack_count") or pack_count(row.get("title", ""))
    return product


def merge_landscape_product(existing, incoming, prefer_existing_metrics=False):
    merged = {**existing, **incoming}
    merged["bsr_category"] = incoming.get("bsr_category") or existing.get("bsr_category", "")
    # Retain all observed category paths so a later source cannot erase risk evidence.
    merged["category_evidence"] = []
    for entry in existing.get("category_evidence", []) + incoming.get("category_evidence", []):
        if entry not in merged["category_evidence"]:
            merged["category_evidence"].append(entry)
    merged["source_exclusion_reasons"] = sorted(set(
        existing.get("source_exclusion_reasons", []) + incoming.get("source_exclusion_reasons", [])
    ))
    merged["brand"] = incoming.get("brand") or existing.get("brand", "")
    provenance = {}
    for field in ("monthly_sales", "price", "online_date"):
        old = existing.get(field)
        new = incoming.get(field)
        old_source = existing.get("metric_sources", {}).get(field, {})
        new_source = incoming.get("metric_sources", {}).get(field, {})
        old_date = listing_date(old_source.get("retrieved_on"))
        new_date = listing_date(new_source.get("retrieved_on"))
        keep_old = old is not None and old != "" and (
            prefer_existing_metrics or new is None or new == ""
            or (old_date and new_date and old_date > new_date)
        )
        merged[field] = old if keep_old else new
        if keep_old or new is not None and new != "":
            provenance[field] = old_source if keep_old else new_source
    merged["metric_sources"] = provenance
    return merged


def assess_landscape_product(product, scoring_rules):
    categories = {entry["category_path"] for entry in product.get("category_evidence", [])
                  if has_valid_category(entry.get("category_path"))}
    if has_valid_category(product.get("bsr_category")):
        categories.add(product["bsr_category"])
    # Missing evidence is a separate state, not a bypass of title/brand/risk checks.
    reasons = {reason for reason in product.get("source_exclusion_reasons", [])
               if reason != "missing or invalid category evidence"}
    pending = []
    title_reason = product_exclusion_reason({**product, "bsr_category": ""}, scoring_rules,
                                           require_valid_category=False)
    for category in sorted(categories) or [""]:
        reason = product_exclusion_reason({**product, "bsr_category": category}, scoring_rules,
                                          require_valid_category=False)
        if reason == "oversize risk" and not title_reason:
            pending.append("类目提示大件，但标题未确认产品为大件；需核对实际尺寸和包装")
            continue
        if (reason.startswith("excluded product rule: weapon_related ")
                and product.get("product_family") in {"drain_stopper", "drain_strainer", "drain_linkage"}
                and re.search(r"\bbullet\s+core\b", product.get("title", ""), flags=re.I)):
            without_component = {**product, "bsr_category": category,
                                 "title": re.sub(r"\bbullet\s+core\b", "", product["title"], flags=re.I)}
            if not product_exclusion_reason(without_component, scoring_rules, require_valid_category=False):
                pending.append("排水配件标题中的 Bullet Core 命中武器词规则，用途冲突待核")
                continue
        if reason:
            reasons.add(reason)
    if reasons:
        return {"screening_status": "excluded", "excluded_reason": "; ".join(sorted(reasons)), "pending_reason": ""}
    if not categories:
        pending.append("缺少有效类目信息")
    if pending:
        return {"screening_status": "needs_evidence", "excluded_reason": "",
                "pending_reason": "；".join(sorted(set(pending))) + "；暂不计入通过基础筛选样本"}
    return {"screening_status": "eligible", "excluded_reason": "", "pending_reason": ""}


def analyze_landscape(products, seed_asin, scoring_rules=None, now=None):
    by_asin = {str(p.get("asin", "")).upper(): p for p in products if p.get("asin")}
    seed = by_asin.get(seed_asin.upper(), {})
    family = seed.get("product_family", "")
    resolved = bool(family and not family.startswith("unresolved:"))
    relevant = [p for p in by_asin.values() if resolved and p.get("product_family") == family]
    groups = defaultdict(list)
    for row in relevant:
        groups[row["product_form"]].append(row)
    total_sales = sum(to_float(p.get("monthly_sales")) for p in relevant)
    forms = []
    for form, members in groups.items():
        assessments = {p["asin"]: assess_landscape_product(p, scoring_rules or {}) for p in members}
        eligible = [p for p in members if assessments[p["asin"]]["screening_status"] == "eligible"]
        pending_count = sum(a["screening_status"] == "needs_evidence" for a in assessments.values())
        excluded_count = sum(a["screening_status"] == "excluded" for a in assessments.values())
        entrants = new_entrant_metrics(eligible, {}, now=now)
        sales = sum(to_float(p.get("monthly_sales")) for p in members)
        packages = defaultdict(list)
        for p in members:
            packages[str(p.get("pack_count") or pack_count(p.get("title", "")))].append(p)
        packs = [{
            "quantity": quantity, "count": len(items),
            "monthly_sales": sum(to_float(p.get("monthly_sales")) for p in items),
            "median_price": median([to_float(p.get("price")) for p in items if to_float(p.get("price")) > 0] or [0]),
        } for quantity, items in packages.items()]
        if not eligible and pending_count:
            next_check = f"{pending_count} 个样本待补证据或核对规则命中，{excluded_count} 个明确排除；暂不判断该形态是否可做。"
        elif not eligible:
            next_check = "当前样本均命中产品排除规则，仅保留市场背景。"
        elif "unspecified" in form or members[0].get("classification_status") == "unresolved":
            next_check = "先核对主图、材质和结构，不把未确认规格当成独立机会。"
        elif len(eligible) < 3:
            next_check = "同形态有效样本不足，先补核心关键词样本。"
        elif entrants["new_entrant_success_count"]:
            next_check = "有新品销售信号；比较规格、套装组合和评论痛点，再核验同款成本与风险。"
        else:
            next_check = "尚无新品达标证据；复核上架时间、主要替代形态与差异化需求。"
        if eligible and pending_count:
            next_check += f"另有 {pending_count} 个样本待补证据或核对规则命中，不计入通过基础筛选样本。"
        forms.append({
            "form": form, "label": members[0].get("product_form_label") or form,
            "is_target": form == seed.get("product_form"), "count": len(members),
            "eligible_count": len(eligible), "monthly_sales": sales,
            "pending_count": pending_count, "excluded_count": excluded_count,
            "sales_share": sales / total_sales if total_sales else 0,
            "median_monthly_sales": median([to_float(p.get("monthly_sales")) for p in members]),
            "median_reviews": median([to_float(p.get("reviews")) for p in members]),
            "median_price": median([to_float(p.get("price")) for p in members if to_float(p.get("price")) > 0] or [0]),
            "new_entrants": entrants, "packs": packs,
            "exclusion_reasons": dict(Counter(a["excluded_reason"] for a in assessments.values() if a["excluded_reason"])),
            "next_check": next_check,
            "products": [{"asin": p["asin"], "title": p.get("title", ""),
                          "brand": p.get("brand", ""), "parent_asin": p.get("parent_asin", ""),
                          "reviews": p.get("reviews"), "pack_count": p.get("pack_count"),
                          "monthly_sales": optional_number(p.get("monthly_sales")),
                          "price": optional_number(p.get("price")),
                          "online_date": listing_date(p.get("online_date")),
                          "metric_sources": p.get("metric_sources", {}),
                          "category_evidence": p.get("category_evidence", []),
                          **assessments[p["asin"]]}
                         for p in sorted(members, key=lambda p: -to_float(p.get("monthly_sales")))],
        })
    forms.sort(key=lambda f: -f["monthly_sales"])
    return {"seed_asin": seed_asin, "family": family,
            "family_label": seed.get("product_family_label", "用途待确认"),
            "status": "sample_comparison" if resolved else "needs_classification",
            "source_product_count": len(by_asin), "related_product_count": len(relevant),
            "other_or_unresolved_count": len(by_asin) - len(relevant), "forms": forms,
            "scope_note": "销量份额仅以本次同用途样本为分母，不代表全市场；套装按一个 Listing 可售单元统计，未折算单件价格或需求。",
            "decision_note": "形态对比不等于通过选品；成本、季节性、专利和适用合规仍须独立核验。"}


def build_landscape(root: Path, research_dir: Path):
    sources = read_json(research_dir / "research_sources.json")
    summary = read_json(research_dir / "research_summary.json")
    collected, evidence = [], []
    category_source = str(sources.get("category_source") or summary.get("provenance", {}).get("category_source") or "")
    category_path = root / category_source
    if category_source and category_path.is_file() and category_path.resolve().is_relative_to(root.resolve()):
        payload = read_json(category_path)
        rows = records(payload)
        data = payload.get("data", payload.get("result", payload))
        stats = data.get("category_stats_report", {}) if isinstance(data, dict) else {}
        source = {"kind": "category_top100", "path": category_source, "count": len(rows),
                  "category_path": stats.get("category_name") or "", "node_id": stats.get("node_id") or ""}
        evidence.append(source)
        collected.extend((row, source) for row in rows)
    # Only explicit MCP keyword-product results count as keyword coverage, not traffic terms.
    for path in sorted(research_dir.glob("search_*.json")):
        payload = read_json(path)
        if payload.get("tool") != "product_search_from_name":
            continue
        if payload.get("arguments", {}).get("amz_site", "US") != sources.get("site", "US"):
            continue
        rows = records(payload)
        source = {"kind": "keyword_search", "path": str(path.relative_to(root)),
                  "keyword": payload.get("arguments", {}).get("name", ""), "count": len(rows),
                  "retrieved_on": payload.get("retrieved_on", "")}
        evidence.append(source)
        collected.extend((row, source) for row in rows)
    normalized = {}
    for row, source in collected:
        p = normalize_landscape_product(row, source.get("category_path", ""), source)
        normalized[p["asin"]] = merge_landscape_product(normalized.get(p["asin"], {}), p)
    products_path = research_dir / "top_products.csv"
    if products_path.is_file():
        with products_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                p = normalize_landscape_product({
                    **row, "product_category": row.get("product_type", ""),
                    "monthly_sales_volume": row.get("monthly_sales"), "review_count": row.get("reviews"),
                    "star_rating": row.get("rating"), "online_date": row.get("listing_date"),
                }, row.get("bsr_category", ""), {
                    "kind": "research_samples", "path": str(products_path.relative_to(root)),
                    "retrieved_on": sources.get("retrieved_on") or summary.get("provenance", {}).get("data_retrieved_on", ""),
                })
                if p["asin"]:
                    # Keep original endpoint measurements ahead of lossy CSV defaults.
                    normalized[p["asin"]] = merge_landscape_product(normalized.get(p["asin"], {}), p, True)
    result = analyze_landscape(list(normalized.values()), research_dir.name, read_json(root / "config/scoring_rules.json"))
    result.update({"sources": evidence, "analysis_date": datetime.now().strftime("%Y-%m-%d"),
                   "keyword_coverage": "recorded" if any(s["kind"] == "keyword_search" for s in evidence) else "not_recorded",
                   "coverage_status": "cached_source_review" if evidence else "research_samples_only"})
    return result
