#!/usr/bin/env python3
"""Build a static dashboard for the Amazon selection workflow."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

from product_risk import has_valid_category, infer_compliance_risk, infer_fragile_risk, infer_oversize_risk


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
OUTPUT = WEB_DIR / "index.html"


def read_csv(relative_path: str) -> list[dict[str, str]]:
    path = ROOT / relative_path
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_csv_reference(path_text: str) -> list[dict[str, str]]:
    if not path_text:
        return []
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_text(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_json(relative_path: str) -> dict:
    path = ROOT / relative_path
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        text = str(value).replace("$", "").replace("%", "").replace(",", "").strip()
        if not text:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def as_percent(value: object) -> float:
    number = to_float(value)
    if number <= 1:
        return number * 100
    return number


def normalize_url(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key, "").strip()
        if value:
            return value
    asin = row.get("source_asin") or row.get("asin") or row.get("competitor_asin")
    if asin:
        return f"https://www.amazon.com/dp/{asin}"
    return ""


def web_path(path_text: str) -> str:
    if not path_text:
        return ""
    path = Path(path_text)
    if path.is_absolute():
        try:
            path = path.relative_to(ROOT)
        except ValueError:
            return path.as_posix()
    return f"../{path.as_posix()}"


def short_title(title: str, limit: int = 118) -> str:
    title = " ".join((title or "").split())
    if len(title) <= limit:
        return title
    return f"{title[: limit - 1].rstrip()}..."


def tag_list(rows: list[dict[str, str]], key: str) -> list[str]:
    values = sorted({row.get(key, "").strip() for row in rows if row.get(key, "").strip()})
    return values


def top_counts(rows: list[dict[str, str]], key: str, limit: int = 6) -> list[dict[str, object]]:
    counts = Counter(row.get(key, "").strip() for row in rows if row.get(key, "").strip())
    return [{"name": name, "count": count} for name, count in counts.most_common(limit)]


def normalize_selection(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        normalized.append(
            {
                "rank": index,
                "asin": row.get("source_asin", ""),
                "title": row.get("product_name", ""),
                "title_short": short_title(row.get("product_name", "")),
                "category": row.get("category", ""),
                "strategy": row.get("source_strategy", ""),
                "price": to_float(row.get("target_price")),
                "score": to_float(row.get("opportunity_score")),
                "recommendation": row.get("recommendation", ""),
                "margin": as_percent(row.get("gross_margin")),
                "unit_profit": to_float(row.get("gross_profit_per_unit")),
                "monthly_profit": to_float(row.get("monthly_gross_profit")),
                "monthly_sales": to_float(row.get("est_monthly_sales")),
                "reviews": to_float(row.get("avg_review_count")),
                "rating": to_float(row.get("avg_rating")),
                "evidence_confidence": to_float(row.get("evidence_confidence")),
                "evidence_grade": row.get("evidence_grade", ""),
                "profit_estimate_status": row.get("profit_estimate_status", ""),
                "data_source_summary": row.get("data_source_summary", ""),
                "flags": row.get("key_flags", ""),
                "hard_stop_reason": row.get("hard_stop_reason", ""),
                "listing_url": normalize_url(row, "listing_url"),
            }
        )
    return normalized


def normalize_category_scan_state(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized = [
        {
            "category_id": row.get("category_id", ""),
            "name": row.get("name", ""),
            "path": row.get("path", ""),
            "first_scanned_at": row.get("first_scanned_at", ""),
            "last_scanned_at": row.get("last_scanned_at", ""),
            "scan_count": to_float(row.get("scan_count")),
            "last_products_examined": to_float(row.get("last_products_examined")),
            "last_candidate_count": to_float(row.get("last_candidate_count")),
            "lifetime_products_examined": to_float(row.get("lifetime_products_examined")),
            "lifetime_candidate_count": to_float(row.get("lifetime_candidate_count")),
            "status": row.get("status", ""),
        }
        for row in rows
    ]
    normalized.sort(key=lambda row: row["last_scanned_at"], reverse=True)
    return normalized


def normalize_category_report(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    return [
        {
            "category_id": row.get("category_id", ""),
            "name": row.get("name", ""),
            "path": row.get("path", ""),
            "rotation_bucket": row.get("rotation_bucket", ""),
            "previous_last_scanned_at": row.get("previous_last_scanned_at", ""),
            "previous_scan_count": to_float(row.get("previous_scan_count")),
            "products_examined": to_float(row.get("products_examined")),
            "candidate_count": to_float(row.get("candidate_count")),
            "scan_completed_at": row.get("scan_completed_at", ""),
            "scan_status": row.get("scan_status", ""),
            "scan_error": row.get("scan_error", ""),
        }
        for row in rows
    ]


def normalize_exclusion_rules(discovery_cfg: dict, exclusion_cfg: dict) -> list[dict[str, str]]:
    rows = []
    seen = set()
    for term in discovery_cfg.get("category_filters", {}).get("exclude_name_contains", []):
        key = str(term).strip().lower()
        if key and key not in seen:
            rows.append({"term": str(term), "type": "基础排除", "reason": "不进入个人卖家类目扫描队列"})
            seen.add(key)
    for item in exclusion_cfg.get("path_contains", []):
        item = {"term": item, "type": "永久排除", "reason": "手动排除"} if isinstance(item, str) else item
        key = str(item.get("term", "")).strip().lower()
        if key and key not in seen:
            rows.append(
                {
                    "term": str(item.get("term", "")),
                    "type": str(item.get("type") or "永久排除"),
                    "reason": str(item.get("reason") or "手动排除"),
                }
            )
            seen.add(key)
    for item in exclusion_cfg.get("category_ids", []):
        item = {"category_id": item, "reason": "手动排除"} if isinstance(item, str) else item
        category_id = str(item.get("category_id", "")).strip()
        if category_id:
            rows.append(
                {
                    "term": f"Category ID {category_id}",
                    "type": str(item.get("type") or "指定类目"),
                    "reason": str(item.get("reason") or "手动排除"),
                }
            )
    return rows


def normalize_keyword_search_index(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized = [
        {
            "run_id": row.get("run_id", ""),
            "keyword": row.get("keyword", ""),
            "searched_at": row.get("searched_at", ""),
            "raw_result_count": to_float(row.get("raw_result_count")),
            "eligible_candidate_count": to_float(row.get("eligible_candidate_count")),
            "go_count": to_float(row.get("go_count")),
            "watch_count": to_float(row.get("watch_count")),
            "reject_count": to_float(row.get("reject_count")),
            "top_score": to_float(row.get("top_score")),
            "top_asin": row.get("top_asin", ""),
            "top_title": row.get("top_title", ""),
            "report_md": web_path(row.get("report_md", "")),
            "ranked_csv": web_path(row.get("ranked_csv", "")),
        }
        for row in rows
    ]
    normalized.sort(key=lambda row: row["searched_at"], reverse=True)
    return normalized


def normalize_archive(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        normalized.append(
            {
                "rank": index,
                "asin": row.get("source_asin", ""),
                "title": row.get("product_name", ""),
                "title_short": short_title(row.get("product_name", ""), 105),
                "category": row.get("category", ""),
                "strategy": row.get("source_strategy", ""),
                "listing_url": normalize_url(row, "listing_url"),
                "best_score": to_float(row.get("archive_best_score")),
                "latest_score": to_float(row.get("archive_latest_score") or row.get("opportunity_score")),
                "best_recommendation": row.get("archive_best_recommendation", ""),
                "latest_recommendation": row.get("archive_latest_recommendation") or row.get("recommendation", ""),
                "archive_status": row.get("archive_status", ""),
                "research_status": row.get("research_status", ""),
                "first_seen": row.get("archive_first_seen", ""),
                "last_seen": row.get("archive_last_seen", ""),
                "seen_count": to_float(row.get("archive_seen_count")),
                "price": to_float(row.get("target_price")),
                "unit_profit": to_float(row.get("gross_profit_per_unit")),
                "margin": as_percent(row.get("gross_margin")),
                "monthly_sales": to_float(row.get("est_monthly_sales")),
                "flags": row.get("key_flags", ""),
            }
        )
    normalized.sort(key=lambda row: (row["archive_status"] != "active_in_latest_run", -row["best_score"]))
    return normalized


def normalize_shape_validation(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        normalized.append(
            {
                "rank": index,
                "seed_asin": row.get("seed_asin", ""),
                "seed_title": row.get("seed_title", ""),
                "seed_title_short": short_title(row.get("seed_title", ""), 95),
                "seed_listing_url": normalize_url(row, "seed_listing_url"),
                "seed_score": to_float(row.get("seed_score")),
                "category_path": row.get("category_path", ""),
                "data_quality": row.get("data_quality", ""),
                "product_form": row.get("product_form", ""),
                "shape_scope": row.get("shape_scope", ""),
                "shape_score": to_float(row.get("shape_score")),
                "shape_recommendation": row.get("shape_recommendation", ""),
                "category_sample_count": to_float(row.get("category_sample_count")),
                "category_total_sales": to_float(row.get("category_total_sales")),
                "category_median_reviews": to_float(row.get("category_median_reviews")),
                "category_top10_median_reviews": to_float(row.get("category_top10_median_reviews")),
                "category_top_brand": row.get("category_top_brand", ""),
                "category_top_brand_share": as_percent(row.get("category_top_brand_share")),
                "form_count": to_float(row.get("form_count")),
                "form_avg_price": to_float(row.get("form_avg_price")),
                "form_avg_sales": to_float(row.get("form_avg_sales")),
                "form_median_reviews": to_float(row.get("form_median_reviews")),
                "form_low_review_high_sales_count": to_float(row.get("form_low_review_high_sales_count")),
                "form_top_materials": row.get("form_top_materials", ""),
                "form_top_packs": row.get("form_top_packs", ""),
                "validation_flags": row.get("validation_flags", ""),
                "opportunity_thesis": row.get("opportunity_thesis", ""),
                "next_action": row.get("next_action", ""),
                "research_page": row.get("research_page", ""),
            }
        )
    normalized.sort(
        key=lambda row: (
            row["shape_recommendation"] != "Shape opportunity",
            row["shape_recommendation"] != "Watch shape",
            -row["shape_score"],
        )
    )
    return normalized


def primary_shape_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        key = str(row.get("seed_asin") or row.get("seed_title") or row.get("category_path") or row.get("rank"))
        grouped[key].append(row)

    primary_rows: list[dict[str, object]] = []
    for group_rows in grouped.values():
        primary = next((row for row in group_rows if row.get("shape_scope") == "seed_form"), None)
        if primary is None:
            primary = next((row for row in group_rows if row.get("shape_recommendation") == "Shape opportunity"), None)
        primary_rows.append(primary or group_rows[0])

    primary_rows.sort(
        key=lambda row: (
            row.get("shape_recommendation") != "Shape opportunity",
            row.get("shape_recommendation") != "Watch shape",
            -to_float(row.get("shape_score")),
        )
    )
    return primary_rows


def normalize_shape_archive(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        normalized.append(
            {
                "rank": index,
                "seed_asin": row.get("seed_asin", ""),
                "seed_title": row.get("seed_title", ""),
                "seed_title_short": short_title(row.get("seed_title", ""), 95),
                "seed_listing_url": normalize_url(row, "seed_listing_url"),
                "category_path": row.get("category_path", ""),
                "product_form": row.get("product_form", ""),
                "shape_score": to_float(row.get("shape_score") or row.get("archive_latest_score")),
                "shape_recommendation": row.get("shape_recommendation", ""),
                "archive_status": row.get("archive_status", ""),
                "archive_best_score": to_float(row.get("archive_best_score")),
                "archive_latest_score": to_float(row.get("archive_latest_score") or row.get("shape_score")),
                "archive_first_seen": row.get("archive_first_seen", ""),
                "archive_last_seen": row.get("archive_last_seen", ""),
                "archive_seen_count": to_float(row.get("archive_seen_count")),
                "research_status": row.get("research_status", ""),
                "form_avg_sales": to_float(row.get("form_avg_sales")),
                "form_median_reviews": to_float(row.get("form_median_reviews")),
                "form_low_review_high_sales_count": to_float(row.get("form_low_review_high_sales_count")),
                "category_top_brand": row.get("category_top_brand", ""),
                "category_top_brand_share": as_percent(row.get("category_top_brand_share")),
                "opportunity_thesis": row.get("opportunity_thesis", ""),
                "next_action": row.get("next_action", ""),
                "research_page": row.get("research_page", ""),
            }
        )
    normalized.sort(key=lambda row: (row["archive_status"] != "active_in_latest_run", -row["archive_best_score"]))
    return normalized


def normalize_product_research_archive(
    rows: list[dict[str, str]], selection_lookup: dict[str, dict[str, object]] | None = None
) -> list[dict[str, object]]:
    selection_lookup = selection_lookup or {}
    normalized: list[dict[str, object]] = []
    for row in rows:
        asin = row.get("asin", "")
        selection = selection_lookup.get(asin, {})
        title = row.get("title", "")
        category = selection.get("category", "")
        compliance_risk = infer_compliance_risk(title, category)
        fragile_risk = infer_fragile_risk(title, category)
        oversize_risk = infer_oversize_risk(title, category)
        recommendation = str(selection.get("recommendation") or "")
        reason = str(selection.get("hard_stop_reason") or selection.get("flags") or "").strip()
        if oversize_risk >= 65:
            decision, reason = "已淘汰", "大件/物流风险"
        elif compliance_risk >= 80:
            decision, reason = "已淘汰", "食品接触/合规资质风险"
        elif fragile_risk >= 80:
            decision, reason = "已淘汰", "易碎/高破损风险"
        elif selection and not has_valid_category(category):
            decision, reason = "已淘汰", "类目数据缺失，Listing 需复核"
        elif recommendation == "Reject" or row.get("status") == "rejected":
            decision, reason = "已淘汰", reason or "未通过当前筛选规则"
        elif recommendation == "Go to supplier validation":
            decision, reason = "可继续", "本轮粗筛通过，待供应商验证"
        elif recommendation == "Watch or collect more data":
            decision, reason = "待复核", reason or "需补证据后再判断"
        else:
            decision, reason = "历史研究", "未纳入本轮筛选，需重新验证"
        web_page = row.get("web_page", "")
        if web_page.startswith("web/"):
            web_page = web_page[4:]
        web_page_available = bool(web_page and (WEB_DIR / web_page).is_file())
        raw_report_path = row.get("report_path", "")
        report_available = bool(raw_report_path and (ROOT / raw_report_path).is_file())
        report_path = raw_report_path
        if report_path and not report_path.startswith("../"):
            report_path = f"../{report_path}"
        normalized.append(
            {
                "asin": asin,
                "title": title,
                "title_short": short_title(title, 100),
                "listing_url": row.get("listing_url", ""),
                "first_researched": row.get("first_researched", ""),
                "last_researched": row.get("last_researched", ""),
                "research_count": to_float(row.get("research_count")),
                "research_dir": row.get("research_dir", ""),
                "report_path": report_path,
                "web_page": web_page,
                "web_page_available": web_page_available,
                "report_available": report_available,
                "products_count": to_float(row.get("products_count")),
                "forms_count": to_float(row.get("forms_count")),
                "reviews_count": to_float(row.get("reviews_count")),
                "review_targets_count": to_float(row.get("review_targets_count")),
                "top_form": row.get("top_form", ""),
                "status": row.get("status", ""),
                "notes": row.get("notes", ""),
                "decision": decision,
                "decision_reason": reason,
            }
        )
    return normalized


def normalize_deep_summary(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for row in rows:
        normalized.append(
            {
                "asin": row.get("source_asin", ""),
                "title": row.get("product_name", ""),
                "title_short": short_title(row.get("product_name", ""), 110),
                "listing_url": normalize_url(row, "candidate_listing_url"),
                "category": row.get("category", ""),
                "selection_score": to_float(row.get("opportunity_score")),
                "deep_score": to_float(row.get("deep_dive_score")),
                "recommendation": row.get("deep_dive_recommendation", ""),
                "price": to_float(row.get("candidate_price")),
                "margin": as_percent(row.get("candidate_margin")),
                "competitor_count": to_float(row.get("competitor_count")),
                "direct_count": to_float(row.get("direct_competitor_count")),
                "keyword_count": to_float(row.get("keyword_competitor_count")),
                "noise_count": to_float(row.get("noise_competitor_count")),
                "avg_competitor_sales": to_float(row.get("avg_competitor_sales")),
                "median_reviews": to_float(row.get("median_competitor_reviews")),
                "low_review_high_sales_count": to_float(row.get("low_review_high_sales_count")),
                "top_brand": row.get("top_brand", ""),
                "top_brand_share": as_percent(row.get("top_brand_share")),
                "cn_hk_seller_share": as_percent(row.get("cn_hk_seller_share")),
                "fba_share": as_percent(row.get("fba_share")),
                "keywords": row.get("top_keywords", ""),
                "flags": row.get("deep_dive_flags", ""),
            }
        )
    return normalized


def normalize_competitors(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for row in rows:
        normalized.append(
            {
                "source_asin": row.get("source_asin", ""),
                "source_title": row.get("candidate_name", ""),
                "asin": row.get("competitor_asin", ""),
                "title": row.get("title", ""),
                "title_short": short_title(row.get("title", ""), 95),
                "listing_url": normalize_url(row, "competitor_listing_url"),
                "type": row.get("competitor_type", ""),
                "relevance": to_float(row.get("relevance_score")),
                "brand": row.get("brand", ""),
                "price": to_float(row.get("price")),
                "monthly_sales": to_float(row.get("monthly_sales")),
                "reviews": to_float(row.get("reviews")),
                "rating": to_float(row.get("rating")),
                "seller_address": row.get("seller_address", ""),
                "is_fba": row.get("is_fba", ""),
                "variation_count": to_float(row.get("variation_count")),
                "evidence_keywords": row.get("evidence_keywords", ""),
                "relevance_reasons": row.get("relevance_reasons", ""),
            }
        )
    return normalized


def normalize_keywords(rows: list[dict[str, str]], limit: int | None = None) -> list[dict[str, object]]:
    selected = rows[:limit] if limit else rows
    normalized: list[dict[str, object]] = []
    for row in selected:
        normalized.append(
            {
                "source_asin": row.get("source_asin", ""),
                "product": row.get("product_name", ""),
                "keyword": row.get("keyword", ""),
                "search_volume": to_float(row.get("search_volume")),
                "rank": to_float(row.get("rank")),
                "show_share": to_float(row.get("show_share")),
                "raw_score": to_float(row.get("raw_score")),
                "position": row.get("position", ""),
                "top3_asins": row.get("top3_asins", ""),
                "clicks_90d": to_float(row.get("clicks_90d")),
                "image_asin_count": to_float(row.get("image_asin_count")),
            }
        )
    return normalized


def normalize_forms(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for row in rows:
        normalized.append(
            {
                "form": row.get("product_form", ""),
                "count": to_float(row.get("count")),
                "direct_count": to_float(row.get("direct_count")),
                "keyword_count": to_float(row.get("keyword_count")),
                "avg_price": to_float(row.get("avg_price")),
                "avg_monthly_sales": to_float(row.get("avg_monthly_sales")),
                "median_reviews": to_float(row.get("median_reviews")),
                "avg_rating": to_float(row.get("avg_rating")),
                "low_review_high_sales_count": to_float(row.get("low_review_high_sales_count")),
                "materials": row.get("top_materials", ""),
                "pack_counts": row.get("top_pack_counts", ""),
                "styles": row.get("top_styles", ""),
                "note": row.get("opportunity_note", ""),
            }
        )
    return normalized


def normalize_top_products(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for row in rows:
        normalized.append(
            {
                "asin": row.get("asin", ""),
                "source": row.get("source", ""),
                "title": row.get("title", ""),
                "title_short": short_title(row.get("title", ""), 90),
                "listing_url": normalize_url(row, "listing_url"),
                "competitor_type": row.get("competitor_type", ""),
                "relevance": to_float(row.get("relevance_score")),
                "product_form": row.get("product_form", ""),
                "material": row.get("material", ""),
                "detail_material": row.get("detail_material", ""),
                "material_evidence": row.get("material_evidence", ""),
                "detail_evidence": row.get("detail_evidence", ""),
                "pack_count": row.get("pack_count", ""),
                "closure": row.get("closure", ""),
                "style": row.get("style", ""),
                "use_case": row.get("use_case", ""),
                "feature_tags": row.get("feature_tags", ""),
                "price": to_float(row.get("price")),
                "monthly_sales": to_float(row.get("monthly_sales")),
                "reviews": to_float(row.get("reviews")),
                "rating": to_float(row.get("rating")),
                "seller_address": row.get("seller_address", ""),
                "visual_product_form": row.get("visual_product_form", ""),
                "visual_material_signal": row.get("visual_material_signal", ""),
                "visual_pack_count": row.get("visual_pack_count", ""),
                "visual_closure": row.get("visual_closure", ""),
                "visual_style": row.get("visual_style", ""),
                "visual_notes": row.get("visual_notes", ""),
                "image": web_path(row.get("image_file", "")),
            }
        )
    return normalized


def extract_report_section(markdown: str, heading: str) -> list[str]:
    if not markdown:
        return []
    lines = markdown.splitlines()
    capture = False
    bullets: list[str] = []
    for line in lines:
        if line.strip() == f"## {heading}":
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture and line.strip().startswith("- "):
            bullets.append(line.strip()[2:])
    return bullets


def count_values(rows: list[dict[str, object]], key: str, *, split: bool = False) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        raw = str(row.get(key, "") or "").strip()
        if not raw or raw == "unknown":
            continue
        values = [raw]
        if split:
            values = [item.strip() for item in raw.split(";") if item.strip()]
        for value in values:
            if value and value != "unknown":
                counts[value] += 1
    return counts


def count_terms(rows: list[dict[str, object]], definitions: dict[str, list[str]]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        text = " ".join(
            str(row.get(key, "") or "")
            for key in [
                "title",
                "feature_tags",
                "style",
                "visual_style",
                "visual_notes",
                "closure",
                "visual_closure",
            ]
        ).lower()
        for label, terms in definitions.items():
            if any(term in text for term in terms):
                counts[label] += 1
    return counts


def count_review_terms(rows: list[dict[str, object]], definitions: dict[str, list[str]]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        text = f"{row.get('review_title', '')} {row.get('review_text', '')} {row.get('pain_point_tags', '')}".lower()
        for label, terms in definitions.items():
            if any(term in text for term in terms):
                counts[label] += 1
    return counts


def count_sizes(rows: list[dict[str, object]]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        title = str(row.get("title", "") or "").lower()
        for match in re.findall(r"\b\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\s*(?:in|inch|inches|\"|”)?", title):
            counts[re.sub(r"\s+", "", match.replace("×", "x")).replace("inches", "in").replace("inch", "in")] += 1
        if "extra large" in title or re.search(r"\bxl\b", title):
            counts["XL / extra large"] += 1
        if "large" in title and "extra large" not in title:
            counts["large"] += 1
        if "sourdough" in title:
            counts["sourdough fit"] += 1
        if "loaf" in title:
            counts["loaf fit"] += 1
    return counts


def compact_counts(counts: Counter, limit: int = 5) -> str:
    if not counts:
        return "-"
    return "；".join(f"{name} {count}" for name, count in counts.most_common(limit))


def review_form_coverage_note(review_target_rows: list[dict[str, str]]) -> str:
    covered_forms: Counter = Counter()
    for row in review_target_rows:
        form = row.get("product_form", "").strip()
        if form and to_float(row.get("review_rows_collected")) > 0:
            covered_forms[form] += 1
    if not covered_forms:
        return "当前还没有按产品形态覆盖到真实评论。"
    forms = "；".join(f"{form} {count} 个 ASIN" for form, count in covered_forms.most_common())
    return f"评论覆盖形态：{forms}。未覆盖评论的形态只能先看供给、图片和 listing 特征，不能直接当成用户需求结论。"


POSITIVE_REVIEW_TERMS = {
    "保鲜更久 / 不易变硬": ["fresh", "soft", "longer", "crust", "keeps"],
    "尺寸够大 / 适配 sourdough": ["large", "xl", "room", "fit", "sourdough", "loaf"],
    "可复用 / 减少塑料": ["reusable", "plastic", "eco", "sustainable"],
    "好看 / 适合送礼陈列": ["cute", "pretty", "design", "gift", "counter"],
    "清洗方便": ["clean", "wash"],
    "拉链 / 夹扣可用": ["zipper", "clip", "clasp"],
    "可冷冻": ["freezer", "freeze"],
}


NEGATIVE_REVIEW_TERMS = {
    "短时间变硬 / 变干": ["stale", "hard", "dry", "dried"],
    "发霉 / 潮气": ["mold", "mould", "moisture"],
    "蜂蜡层理解落差": ["wax", "woven", "inside"],
    "拉链/缝线/耐用问题": ["zipper", "stitch", "broken", "tear", "rip"],
    "尺寸不合适": ["small", "fit", "large", "size"],
    "转回塑料袋": ["plastic"],
    "产品预期落差": ["not the right", "not even close", "do not recommend"],
}


ACCESSORY_TERMS = {
    "zipper / clip": ["zipper", " zip", "clip"],
    "drawstring / roll-top": ["drawstring", "roll top", "roll-top"],
    "bowl cover": ["bowl cover"],
    "removable liner": ["removable", "detachable", "liner"],
    "sticker / label": ["sticker", "label"],
    "retail / gift box": ["gift", "box"],
}


def avg(values: list[float]) -> float:
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else 0.0


def med(values: list[float]) -> float:
    values = sorted(value for value in values if value is not None)
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def main_form_row(form_rows: list[dict[str, object]], form: str) -> dict[str, object]:
    return next((row for row in form_rows if row.get("form") == form or row.get("product_form") == form), {})


def build_single_product_insights(
    product_rows: list[dict[str, object]],
    form_rows: list[dict[str, object]],
    review_rows: list[dict[str, str]],
    review_target_rows: list[dict[str, str]],
) -> dict[str, list[dict[str, object]] | list[str]]:
    relevant = [row for row in product_rows if row.get("competitor_type") in {"seed", "direct", "keyword"}]
    seed = next((row for row in relevant if row.get("competitor_type") == "seed"), relevant[0] if relevant else {})
    seed_form = str(seed.get("product_form", ""))
    target_rows = [row for row in relevant if row.get("product_form") == seed_form] or relevant
    target_form = main_form_row(form_rows, seed_form)

    material_counts = count_values(target_rows, "material")
    visual_material_counts = count_values(target_rows, "visual_material_signal")
    pack_counts = count_values(target_rows, "pack_count")
    visual_pack_counts = count_values(target_rows, "visual_pack_count")
    closure_counts = count_values(target_rows, "visual_closure") + count_values(target_rows, "closure")
    style_counts = count_values(target_rows, "visual_style") + count_values(target_rows, "style")
    feature_counts = count_values(target_rows, "feature_tags", split=True)
    size_counts = count_sizes(target_rows)
    color_counts = count_terms(
        target_rows,
        {
            "natural / off-white": ["natural", "linen", "minimal", "white", "cream", "canvas"],
            "bee / honeycomb / yellow": ["bee", "honeycomb", "yellow"],
            "floral / botanical": ["floral", "flower", "leaf", "pine"],
            "blue pattern": ["blue"],
            "clear window": ["clear window"],
        },
    )
    accessory_counts = count_terms(
        target_rows,
        {
            "zipper / clip": ["zipper", " zip", "clip"],
            "drawstring / roll-top": ["drawstring", "roll top", "roll-top"],
            "bowl cover": ["bowl cover"],
            "removable liner": ["removable", "detachable", "liner"],
            "sticker / label": ["sticker", "label"],
            "retail / gift box": ["gift", "box"],
        },
    )

    ratings = [float(row.get("rating", 0) or 0) for row in target_rows if float(row.get("rating", 0) or 0) > 0]
    review_counts = [float(row.get("reviews", 0) or 0) for row in target_rows if float(row.get("reviews", 0) or 0) > 0]
    low_rating = [row for row in target_rows if 0 < float(row.get("rating", 0) or 0) <= 4.3]
    high_rating = [row for row in target_rows if float(row.get("rating", 0) or 0) >= 4.5]
    low_review_high_sales = [
        row
        for row in target_rows
        if float(row.get("reviews", 0) or 0) <= 300 and float(row.get("monthly_sales", 0) or 0) >= 500
    ]
    review_text_rows = [row for row in review_rows if str(row.get("review_text", "")).strip()]
    review_text_count = len(review_text_rows)
    covered_review_asins = {
        str(row.get("review_target_asin") or row.get("asin") or "")
        for row in review_text_rows
        if str(row.get("review_target_asin") or row.get("asin") or "").strip()
    }
    review_target_count = len(review_target_rows)
    positive_reviews = [row for row in review_text_rows if float(row.get("rating", 0) or 0) >= 4]
    negative_reviews = [row for row in review_text_rows if 0 < float(row.get("rating", 0) or 0) <= 3]
    pain_counts: Counter = Counter()
    for row in review_text_rows:
        for tag in str(row.get("pain_point_tags", "")).split(";"):
            tag = tag.strip()
            if tag:
                pain_counts[tag] += 1
    negative_pain_counts: Counter = Counter()
    for row in negative_reviews:
        for tag in str(row.get("pain_point_tags", "")).split(";"):
            tag = tag.strip()
            if tag:
                negative_pain_counts[tag] += 1
    positive_focus_counts = count_review_terms(
        positive_reviews,
        {
            "保鲜更久 / 不易变硬": ["fresh", "soft", "longer", "crust", "keeps"],
            "尺寸够大 / 适配 sourdough": ["large", "xl", "room", "fit", "sourdough", "loaf"],
            "可复用 / 减少塑料": ["reusable", "plastic", "eco", "sustainable"],
            "好看 / 适合送礼陈列": ["cute", "pretty", "design", "gift", "counter"],
            "清洗方便": ["clean", "wash"],
            "拉链 / 夹扣可用": ["zipper", "clip", "clasp"],
            "可冷冻": ["freezer", "freeze"],
        },
    )
    negative_issue_counts = count_review_terms(
        negative_reviews,
        {
            "短时间变硬 / 变干": ["stale", "hard", "dry", "dried"],
            "发霉 / 潮气": ["mold", "mould", "moisture"],
            "蜂蜡层理解落差": ["wax", "woven", "inside"],
            "转回塑料袋": ["plastic"],
            "产品预期落差": ["not the right", "not even close", "do not recommend"],
        },
    )
    review_form_note = review_form_coverage_note(review_target_rows)
    review_source_note = (
        f"已通过 Sorftime ProductReviewsQuery 跑了 {review_target_count} 个目标 ASIN，其中 {len(covered_review_asins)} 个返回评论；共读取 {review_text_count} 条评论正文，低星 {len(negative_reviews)} 条，高星 {len(positive_reviews)} 条。{review_form_note}"
        if review_text_count
        else "评论正文还没抓取成功，所以这里先用评分结构、评论数、listing 高频卖点和低评高销样本做判断；真实差评词频需要下一步补齐。"
    )

    dimensions = [
        {
            "title": "材质",
            "headline": "beeswax / 蜂蜡涂层是入场券，不是差异点",
            "evidence": [
                f"标题/详情材质：{compact_counts(material_counts)}",
                f"主图材质信号：{compact_counts(visual_material_counts)}",
                "大多数对手都在讲 beeswax、linen/cotton、organic，单靠材质名很难区分。",
            ],
            "opportunity": "差异要落到低气味蜂蜡、涂层均匀度、可清洗边界、食品接触合规证明和真实使用寿命。",
        },
        {
            "title": "颜色 / 视觉",
            "headline": "自然风、蜂蜜/花草元素是主流，礼品化仍有空间",
            "evidence": [
                f"视觉风格：{compact_counts(style_counts)}",
                f"颜色/图案信号：{compact_counts(color_counts)}",
                "当前主图多是棉麻底色、蜜蜂/麦穗/花草图案，少量蓝色、花卉和礼盒感表达。",
            ],
            "opportunity": "可以做 farmhouse 自然风的升级版：更统一的礼品包装、轻图案系列、厨房陈列友好的颜色，而不是只换普通印花。",
        },
        {
            "title": "多个装",
            "headline": "2-pack 已经拥挤，普通 2 件装不是机会",
            "evidence": [
                f"标题套装：{compact_counts(pack_counts)}",
                f"主图套装：{compact_counts(visual_pack_counts)}",
                f"形态表显示 {seed_form or '目标形态'} 里低评高销样本 {target_form.get('low_review_high_sales_count', 0)} 个。",
            ],
            "opportunity": "优先验证 2-size combo、3-pack、家庭装、礼品装，或 2 bags + bowl cover 这种组合，而不是再做同质 2-pack。",
        },
        {
            "title": "功能",
            "headline": "用户买的是保鲜、复用和适配 sourdough，不是单纯买袋子",
            "evidence": [
                f"卖点词：{compact_counts(feature_counts)}",
                f"使用场景：{compact_counts(count_values(target_rows, 'use_case'))}",
                "高频卖点集中在 keep fresh、reusable、XL、organic、eco/freezer 等方向。",
            ],
            "opportunity": "核心功能要讲清楚“透气但不变干”的边界，配合面包实测、使用天数、清洗方式，而不是泛泛写 keep fresh。",
        },
        {
            "title": "尺寸",
            "headline": "XL 是共识，但具体适配对象还没被讲透",
            "evidence": [
                f"尺寸/适配信号：{compact_counts(size_counts)}",
                "标题里频繁出现 XL、sourdough、loaf，但很少把圆形 boule、高吐司、三明治吐司分别讲清楚。",
            ],
            "opportunity": "做规格时应该明确 17x13、16x12 等尺寸能放什么面包，考虑圆形 sourdough + 长条 loaf 两种尺寸组合。",
        },
        {
            "title": "元素 / 配件",
            "headline": "闭合方式和配件是可做差异化的地方",
            "evidence": [
                f"闭合/配件：{compact_counts(accessory_counts)}",
                f"闭合方式：{compact_counts(closure_counts)}",
                "zipper 常见，但可拆内衬、碗罩、标签贴、礼盒等组合仍不多。",
            ],
            "opportunity": "可测试 zipper 耐用升级、可拆洗内衬、配 bowl cover、面包日期标签/冷冻标签，形成“保存套装”而不是单袋。",
        },
    ]

    rating_summary = [
        {
            "title": "评分结构",
            "headline": f"目标形态均分约 {avg(ratings):.1f}，评论门槛不高，但用户对保鲜结果很敏感",
            "evidence": [
                f"样本数：{len(target_rows)}；评论中位数：{med(review_counts):.0f}",
                f"4.5 分以上样本：{len(high_rating)}；4.3 分及以下样本：{len(low_rating)}",
                f"低评高销样本：{len(low_review_high_sales)}",
                f"Sorftime 评论正文：{review_text_count} 条",
            ],
            "opportunity": "评分不低，说明消费者认可品类；真正要避开的风险是宣传过度导致用户期待“完全不变干、不发霉”。",
        },
        {
            "title": "消费者在意的点",
            "headline": "评论里最在意的是保鲜、尺寸、可复用和厨房陈列感",
            "evidence": [
                f"好评高频主题：{compact_counts(positive_focus_counts)}",
                "保鲜：用户反复提到 fresh、soft、crust、longer。",
                "尺寸：large、XL、sourdough、loaf 是真实使用场景。",
                "价值：用户同时在意 reusable、减少 plastic、2-pack/数量和耐用性。",
            ],
            "opportunity": "详情页要用实测和场景证明这些点，例如不同面包类型、常温/冷冻、3天/5天状态，而不是只堆 keep fresh 关键词。",
        },
        {
            "title": "消费者不满意的点",
            "headline": "低星评论集中在保鲜失败和蜂蜡结构预期落差",
            "evidence": [
                f"低星样本：{len(negative_reviews)} 条；低星痛点标签：{compact_counts(negative_pain_counts)}",
                f"低星主题：{compact_counts(negative_issue_counts)}",
                "典型问题不是“不好看”，而是 3-4 天后发硬/发霉，以及用户对蜂蜡层在内侧还是织入棉布有预期落差。",
            ],
            "opportunity": "产品规格和 listing 必须讲清保存边界、蜂蜡涂层结构、适合/不适合的面包类型，并用测试图降低预期落差。",
        },
    ]

    conclusions = [
        {
            "title": "优先机会",
            "headline": "做“保鲜边界讲清楚”的升级款 beeswax bread bag",
            "evidence": "评论证明用户会为保鲜、XL、可复用和自然材质买单；低星主要来自变硬/发霉和材质理解落差。",
            "action": "方向：低气味蜂蜡 + 明确常温/冷冻/面包类型边界 + 3天/5天对比图 + 清洗说明卡。",
        },
        {
            "title": "次优机会",
            "headline": "做结构升级：让大面包更好放、更好立、更好封口",
            "evidence": "好评里有用户认可 large/XL，但也出现希望袋子更有支撑、面包能直立的信号；zipper/clip 是用户会感知的细节。",
            "action": "方向：2-size combo、半硬底/可折叠支撑、耐用 zipper/clip、日期标签或冷冻标签。",
        },
        {
            "title": "视觉机会",
            "headline": "做礼品化/厨房陈列友好的自然风系列，但不要只换印花",
            "evidence": "自然风、蜜蜂、花草图案是主流；评论里也有用户把它当作 counter display 和 hostess gift。",
            "action": "方向：统一图案系列、礼盒包装、sourdough baker gift set，并和规格/保鲜测试一起卖。",
        },
        {
            "title": "暂不建议",
            "headline": "不要直接复制普通 2-pack 拉链蜂蜡袋",
            "evidence": "2-pack、zipper、bee print 都已经常见；没有保鲜证据、结构升级或礼品化组合，会进入同质竞争。",
            "action": "进入供应商前，必须先拿到成本、食品接触材料证明、样品气味/清洗测试和 3-5 天保鲜实测。",
        },
    ]

    return {
        "dimensions": dimensions,
        "rating_summary": rating_summary,
        "conclusions": conclusions,
        "review_source_note": review_source_note,
    }


def form_entry_recommendation(
    form: str,
    form_row: dict[str, object],
    product_rows: list[dict[str, object]],
    review_rows: list[dict[str, str]],
    negative_issue_counts: Counter,
) -> str:
    note = str(form_row.get("note", ""))
    review_count = len(review_rows)
    low_count = len([row for row in review_rows if 0 < to_float(row.get("rating")) <= 3])
    if form == "beeswax bread bag":
        return "主线切入：做可解释规格的升级款，重点验证保鲜边界、蜂蜡涂层结构、尺寸适配、清洗方式、zipper/clip 耐用性。"
    if "disposable" in form or "paper" in form:
        return "相邻参考：需求强但逻辑偏一次性/烘焙包装，可借鉴 clear window、尺寸和批量包装，不建议直接替代可复用蜂蜡袋主线。"
    if "banneton" in form or "proofing" in form:
        return "相邻工具：更适合做套装/捆绑思路，例如 bread bag + proofing cloth/bowl cover，不应当作同款竞品直接对标。"
    if "wrap" in form:
        return "材料相邻：蜂蜡材质认知强，但评论墙高，适合参考蜂蜡卖点和清洗争议，不适合作为个人卖家的主攻形态。"
    if "linen" in form or "cotton" in form:
        return "小供给切入：可探索无蜂蜡/更易清洗/更透气的 cotton-linen bag，但要用评论验证保鲜是否足够强。"
    if review_count and low_count:
        return f"可继续验证：这个形态已有 {review_count} 条评论，低星主要是 {compact_counts(negative_issue_counts, 3)}。"
    return f"先观察：{note or '需要补评论和供应商验证'}。"


def form_entry_actions(form: str, reviews: list[dict[str, str]]) -> list[str]:
    has_reviews = bool(reviews)
    if form == "beeswax bread bag":
        actions = [
            "产品规格：优先验证 XL + 长条 loaf 的 2-size combo，而不是普通同款 2-pack。",
            "功能结构：把 zipper/clip 耐用性、低气味蜂蜡层、可清洗边界和保鲜天数做成样品测试项。",
            "Listing 表达：用 3 天 / 5 天实测图讲清适合的面包类型，以及不适合的潮湿/高温场景。",
        ]
    elif "disposable" in form or "paper" in form:
        actions = [
            "只当需求参考：可以借鉴 clear window、批量装、尺寸命名和烘焙场景，不建议作为可复用蜂蜡袋的直接竞品。",
            "可转化点：如果评论证明用户要“送人/售卖面包”，可以考虑 bread bag + gift label 的组合。",
            "验证重点：看消费者是否愿意为环保/可复用升级付更高客单价。",
        ]
    elif "banneton" in form or "proofing" in form:
        actions = [
            "不做同款：这是发酵工具，不是保存袋，不能把它的销量直接算进 bread bag 需求。",
            "套装思路：可验证 sourdough starter kit、proofing cloth、bowl cover 与 bread bag 的捆绑。",
            "验证重点：评论里是否反复提到发酵后保存、送礼、收纳这类跨场景需求。",
        ]
    elif "wrap" in form:
        actions = [
            "材料参考：借鉴蜂蜡、可复用、环保卖点，但避开评论墙过高的通用 food wrap 红海。",
            "差异方向：把“面包专用尺寸 + 结构封口”做清楚，而不是卖泛用蜂蜡布。",
            "验证重点：差评是否集中在粘性、清洗、气味和保鲜失败。",
        ]
    elif "linen" in form or "cotton" in form:
        actions = [
            "小供给测试：可探索无蜂蜡、更易清洗、更透气的 cotton/linen bread bag。",
            "风险点：如果评论证明保鲜弱，就只能作为短期收纳/陈列袋，不适合主打 keep fresh。",
            "验证重点：先补评论，再决定是否找供应商打样。",
        ]
    else:
        actions = [
            "先作为相邻形态观察，不直接进入供应商验证。",
            "补 5-10 个代表 ASIN 评论后，再判断用户真实需求和低星痛点。",
            "如果低评高销明显，再拆材质、规格、闭合方式和套装组合。",
        ]
    if not has_reviews:
        actions.append("当前未覆盖真实评论：这张卡里的切入方向只是候选假设，需要补评论后再定。")
    return actions


def build_form_analysis_cards(
    product_rows: list[dict[str, object]],
    form_rows: list[dict[str, object]],
    review_rows: list[dict[str, str]],
) -> list[dict[str, object]]:
    relevant = [row for row in product_rows if row.get("competitor_type") in {"seed", "direct", "keyword"}]
    rows_by_form: dict[str, list[dict[str, object]]] = {}
    asin_to_form: dict[str, str] = {}
    for row in relevant:
        form = str(row.get("product_form", "") or "unknown")
        rows_by_form.setdefault(form, []).append(row)
        asin = str(row.get("asin", "") or "")
        if asin:
            asin_to_form[asin] = form

    reviews_by_form: dict[str, list[dict[str, str]]] = {}
    for review in review_rows:
        asin = str(review.get("review_target_asin") or review.get("asin") or "")
        form = asin_to_form.get(asin)
        if form:
            reviews_by_form.setdefault(form, []).append(review)

    cards = []
    for form_row in form_rows[:8]:
        form = str(form_row.get("form") or form_row.get("product_form") or "")
        rows = rows_by_form.get(form, [])
        reviews = reviews_by_form.get(form, [])
        positive_reviews = [row for row in reviews if to_float(row.get("rating")) >= 4]
        negative_reviews = [row for row in reviews if 0 < to_float(row.get("rating")) <= 3]
        negative_pain_counts: Counter = Counter()
        for row in negative_reviews:
            for tag in str(row.get("pain_point_tags", "")).split(";"):
                tag = tag.strip()
                if tag:
                    negative_pain_counts[tag] += 1
        positive_focus_counts = count_review_terms(positive_reviews, POSITIVE_REVIEW_TERMS)
        negative_issue_counts = count_review_terms(negative_reviews, NEGATIVE_REVIEW_TERMS)
        ranked_rows = sorted(
            rows,
            key=lambda row: (
                row.get("competitor_type") != "seed",
                row.get("competitor_type") != "direct",
                -to_float(row.get("monthly_sales")),
                -to_float(row.get("relevance")),
            ),
        )
        representative = ranked_rows[:3]
        image_examples = [row for row in ranked_rows if str(row.get("image", "")).strip()][:2]
        feature_lines = [
            ("材质", compact_counts(count_values(rows, "material") + count_values(rows, "detail_material") + count_values(rows, "visual_material_signal"))),
            ("多个装", compact_counts(count_values(rows, "pack_count") + count_values(rows, "visual_pack_count"))),
            ("闭合方式", compact_counts(count_values(rows, "closure") + count_values(rows, "visual_closure"))),
            ("颜色/视觉", compact_counts(count_values(rows, "style") + count_values(rows, "visual_style"))),
            ("尺寸/场景", compact_counts(count_sizes(rows) + count_values(rows, "use_case"))),
            ("功能/配件", compact_counts(count_values(rows, "feature_tags", split=True) + count_terms(rows, ACCESSORY_TERMS))),
        ]
        if reviews:
            review_lines = [
                ("评论覆盖", f"{len(reviews)} 条；低星 {len(negative_reviews)} / 高星 {len(positive_reviews)}"),
                ("消费者在意", compact_counts(positive_focus_counts)),
                ("消费者不满意", compact_counts(negative_pain_counts)),
                ("低星主题", compact_counts(negative_issue_counts)),
            ]
        else:
            review_lines = [
                ("评论覆盖", "当前这批 Sorftime 评论样本未覆盖该形态。"),
                ("能判断什么", "只能先看供给数量、月销、评论中位数、图片/标题特征。"),
                ("不能判断什么", "不能把 listing 卖点直接当成消费者真实需求。"),
                ("下一步", "如果要把这个形态作为候选，需要单独补评论采集。"),
            ]
        cards.append(
            {
                "form": form,
                "summary": {
                    "count": form_row.get("count", 0),
                    "direct": form_row.get("direct_count", 0),
                    "keyword": form_row.get("keyword_count", 0),
                    "avg_price": form_row.get("avg_price", 0),
                    "avg_sales": form_row.get("avg_monthly_sales", 0),
                    "median_reviews": form_row.get("median_reviews", 0),
                    "avg_rating": form_row.get("avg_rating", 0),
                    "note": form_row.get("note", ""),
                },
                "features": feature_lines,
                "reviews": review_lines,
                "entry": form_entry_recommendation(form, form_row, rows, reviews, negative_issue_counts),
                "entry_actions": form_entry_actions(form, reviews),
                "review_badge": f"评论 {len(reviews)} 条" if reviews else "未补评论",
                "representative": representative,
                "images": image_examples,
            }
        )
    return cards


def render_insight_cards(cards: list[dict[str, object]]) -> str:
    html = []
    for card in cards:
        evidence = card.get("evidence", [])
        if isinstance(evidence, str):
            evidence = [evidence]
        evidence_html = "\n".join(f"<li>{escape(str(item))}</li>" for item in evidence)
        html.append(
            f"""
            <article class="insight-card">
              <h4>{escape(str(card.get("title", "")))}</h4>
              <strong>{escape(str(card.get("headline", "")))}</strong>
              <ul>{evidence_html}</ul>
              <p>{escape(str(card.get("opportunity", card.get("action", ""))))}</p>
            </article>
            """
        )
    return "\n".join(html)


def render_review_coverage(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    table_rows = []
    for row in rows[:10]:
        asin = row.get("asin", "")
        table_rows.append(
            """
            <tr>
              <td><a href="{url}" target="_blank" rel="noreferrer">{asin}</a></td>
              <td>{ctype}</td>
              <td>{form}</td>
              <td class="num">{sales}</td>
              <td class="num">{reviews}</td>
              <td class="num">{collected}</td>
            </tr>
            """.format(
                url=escape(row.get("listing_url", "")),
                asin=escape(asin),
                ctype=escape(row.get("competitor_type", "")),
                form=escape(row.get("product_form", "")),
                sales=f"{to_float(row.get('monthly_sales')):,.0f}",
                reviews=f"{to_float(row.get('reviews')):,.0f}",
                collected=f"{to_float(row.get('review_rows_collected')):,.0f}",
            )
        )
    return """
    <div class="table-wrap review-coverage-table">
      <table>
        <thead>
          <tr>
            <th>ASIN</th>
            <th>类型</th>
            <th>形态</th>
            <th class="num">月销</th>
            <th class="num">Listing 评论</th>
            <th class="num">已读评论</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>
    """.format(rows="\n".join(table_rows))


def render_labeled_lines(items: list[object]) -> str:
    html = []
    for item in items:
        if isinstance(item, tuple) and len(item) == 2:
            label, value = item
            html.append(
                f"<li><b>{escape(str(label))}</b><span>{escape(str(value))}</span></li>"
            )
        else:
            html.append(f"<li>{escape(str(item))}</li>")
    return "\n".join(html)


def render_image_tiles(rows: list[dict[str, object]], class_name: str) -> str:
    tiles = []
    for row in rows:
        image = str(row.get("image", "") or "")
        if not image:
            continue
        tiles.append(
            """
            <a class="{class_name}" href="{url}" target="_blank" rel="noreferrer">
              <img src="{image}" alt="{asin}">
              <span>{asin}</span>
            </a>
            """.format(
                class_name=class_name,
                url=escape(str(row.get("listing_url", ""))),
                image=escape(image),
                asin=escape(str(row.get("asin", ""))),
            )
        )
    if not tiles:
        return '<div class="empty image-empty">暂无代表图</div>'
    return "\n".join(tiles)


def render_form_photo_overview(cards: list[dict[str, object]]) -> str:
    html = []
    for card in cards:
        summary = card.get("summary", {})
        html.append(
            """
            <article class="form-overview-card">
              <div class="form-overview-images">
                {images}
              </div>
              <div class="form-overview-body">
                <h4>{form}</h4>
                <p>{note}</p>
                <div class="form-overview-metrics">
                  <span>样本 {count}</span>
                  <span>月销 {sales}</span>
                  <span>{review_badge}</span>
                </div>
              </div>
            </article>
            """.format(
                images=render_image_tiles(card.get("images", []), "overview-image-tile"),
                form=escape(str(card.get("form", ""))),
                note=escape(str(summary.get("note", ""))),
                count=f"{to_float(summary.get('count')):,.0f}",
                sales=f"{to_float(summary.get('avg_sales')):,.0f}",
                review_badge=escape(str(card.get("review_badge", ""))),
            )
        )
    return '<div class="form-overview-grid">' + "\n".join(html) + "</div>"


def render_form_analysis_cards(cards: list[dict[str, object]]) -> str:
    html = []
    for card in cards:
        summary = card.get("summary", {})
        reps = []
        for row in card.get("representative", []):
            reps.append(
                '<li><a href="{url}" target="_blank" rel="noreferrer">{asin}</a><span>{title}</span></li>'.format(
                    url=escape(str(row.get("listing_url", ""))),
                    asin=escape(str(row.get("asin", ""))),
                    title=escape(short_title(str(row.get("title", "")), 72)),
                )
            )
        if not reps:
            reps.append("<li><span>暂无代表 ASIN</span></li>")
        feature_html = render_labeled_lines(card.get("features", []))
        review_html = render_labeled_lines(card.get("reviews", []))
        action_html = "\n".join(f"<li>{escape(str(item))}</li>" for item in card.get("entry_actions", []))
        html.append(
            """
            <article class="form-analysis-card">
              <div class="form-card-head">
                <div>
                  <h4>{form}</h4>
                  <p>{note}</p>
                </div>
                <div class="form-card-side">
                  <span class="pill {badge_class}">{review_badge}</span>
                  <div class="form-card-score">
                    <strong>{sales}</strong>
                    <span>月销均值</span>
                  </div>
                </div>
              </div>
              <div class="form-card-metrics">
                <span>样本 {count}</span>
                <span>直接/关键词 {direct}/{keyword}</span>
                <span>均价 ${price}</span>
                <span>评论中位 {reviews}</span>
                <span>评分 {rating}</span>
              </div>
              <div class="form-card-takeaway">
                <strong>形态结论</strong>
                <span>{entry}</span>
              </div>
              <div class="form-card-images">
                <strong>代表图</strong>
                <div>{images}</div>
              </div>
              <div class="form-card-body">
                <div>
                  <h5>产品维度</h5>
                  <ul>{features}</ul>
                </div>
                <div>
                  <h5>评论反馈</h5>
                  <ul>{reviews_html}</ul>
                </div>
                <div>
                  <h5>可执行切入</h5>
                  <ul>{actions}</ul>
                </div>
                <div>
                  <h5>代表产品</h5>
                  <ul class="rep-list">{reps}</ul>
                </div>
              </div>
            </article>
            """.format(
                form=escape(str(card.get("form", ""))),
                note=escape(str(summary.get("note", ""))),
                sales=f"{to_float(summary.get('avg_sales')):,.0f}",
                count=f"{to_float(summary.get('count')):,.0f}",
                direct=f"{to_float(summary.get('direct')):,.0f}",
                keyword=f"{to_float(summary.get('keyword')):,.0f}",
                price=f"{to_float(summary.get('avg_price')):,.2f}",
                reviews=f"{to_float(summary.get('median_reviews')):,.0f}",
                rating=f"{to_float(summary.get('avg_rating')):.1f}",
                features=feature_html,
                reviews_html=review_html,
                actions=action_html,
                entry=escape(str(card.get("entry", ""))),
                images=render_image_tiles(card.get("images", []), "form-image-tile"),
                review_badge=escape(str(card.get("review_badge", ""))),
                badge_class="green" if "评论" in str(card.get("review_badge", "")) and "未补" not in str(card.get("review_badge", "")) else "orange",
                reps="\n".join(reps),
            )
        )
    return "\n".join(html)


def render_options(values: list[str], all_label: str) -> str:
    options = [f'<option value="">{escape(all_label)}</option>']
    options.extend(f'<option value="{escape(value)}">{escape(value)}</option>' for value in values)
    return "\n".join(options)


def build_html() -> str:
    scoring_cfg = read_json("config/scoring_rules.json")
    discovery_cfg = read_json("config/autodiscovery_rules.json")
    exclusion_cfg = read_json("config/category_exclusions.json")
    selection_rows = normalize_selection(read_csv("reports/selection_ranked.csv"))
    category_scan_rows = normalize_category_scan_state(read_csv("archive/category_scan_state.csv"))
    current_category_rows = normalize_category_report(read_csv("reports/discovered_categories.csv"))
    exclusion_rows = normalize_exclusion_rules(discovery_cfg, exclusion_cfg)
    keyword_search_rows = normalize_keyword_search_index(read_csv("archive/keyword_search_index.csv"))
    latest_keyword_ranked_rows = []
    if keyword_search_rows:
        latest_index_row = read_csv("archive/keyword_search_index.csv")
        latest_index_row.sort(key=lambda row: row.get("searched_at", ""), reverse=True)
        if latest_index_row:
            latest_keyword_ranked_rows = normalize_selection(read_csv_reference(latest_index_row[0].get("ranked_csv", "")))
    seed_archive_rows = normalize_archive(read_csv("archive/opportunity_library.csv"))
    shape_validation_rows = normalize_shape_validation(read_csv("data/category_shape_validation.csv"))
    primary_validation_rows = primary_shape_rows(shape_validation_rows)
    shape_archive_rows = normalize_shape_archive(read_csv("archive/shape_opportunity_library.csv"))
    selection_lookup = {str(row.get("asin", "")): row for row in selection_rows if row.get("asin")}
    product_research_archive = normalize_product_research_archive(
        read_csv("archive/product_research_index.csv"), selection_lookup
    )
    form_rows = normalize_forms(read_csv("research/B0FS1YH17C/product_forms.csv"))
    research_keyword_rows = normalize_keywords(read_csv("research/B0FS1YH17C/keywords.csv"))
    top_product_rows = normalize_top_products(read_csv("research/B0FS1YH17C/top_products.csv"))
    visual_rows = read_csv("research/B0FS1YH17C/visual_labels.csv")
    review_rows = read_csv("research/B0FS1YH17C/reviews.csv")
    review_target_rows = read_csv("research/B0FS1YH17C/review_targets.csv")
    research_report = read_text("reports/product_opportunity_research_B0FS1YH17C.md")

    selection_strategies = tag_list(
        [{"source_strategy": str(row.get("strategy", ""))} for row in selection_rows], "source_strategy"
    )
    selection_category_count = len(selection_strategies)
    selection_recommendations = tag_list(
        [{"recommendation": str(row.get("recommendation", ""))} for row in selection_rows], "recommendation"
    )
    shape_recommendations = tag_list(
        [{"shape_recommendation": str(row.get("shape_recommendation", ""))} for row in primary_validation_rows],
        "shape_recommendation",
    )
    gallery_forms = tag_list(
        [{"visual_product_form": str(row.get("visual_product_form", ""))} for row in top_product_rows],
        "visual_product_form",
    )

    shape_archive_active_count = sum(1 for row in shape_archive_rows if row.get("archive_status") == "active_in_latest_run")
    shape_opportunity_count = sum(1 for row in primary_validation_rows if row.get("shape_recommendation") == "Shape opportunity")
    needs_category_count = sum(1 for row in primary_validation_rows if row.get("shape_recommendation") == "Needs category Top100")
    weekly_category_target = int(to_float(discovery_cfg.get("max_categories"), 100) or 100)
    weekly_products_per_category = int(to_float(discovery_cfg.get("products_per_category"), 100) or 100)
    current_products_examined = int(sum(to_float(row.get("products_examined")) for row in current_category_rows))
    current_new_category_count = sum(1 for row in current_category_rows if row.get("rotation_bucket") == "never_scanned")
    current_rescan_count = sum(1 for row in current_category_rows if row.get("rotation_bucket") == "oldest_rescan")
    category_report_has_metrics = any(row.get("scan_completed_at") for row in current_category_rows)
    current_scanned_category_count = sum(
        1 for row in current_category_rows if str(row.get("scan_status", "")).lower() == "success"
    )
    if category_report_has_metrics and current_scanned_category_count == 0:
        current_scanned_category_count = len(current_category_rows)
    current_products_examined_label = f"{current_products_examined:,}" if category_report_has_metrics else "未记录"
    current_products_examined_note = (
        f"每类目最多 {weekly_products_per_category} 个 Top 产品"
        if category_report_has_metrics
        else "历史结果作为轮换基线；下一轮开始记录实际查看数"
    )
    visual_label_count = sum(1 for row in visual_rows if row.get("visual_product_form", "").strip())
    top_selection_score = max((float(row.get("score", 0)) for row in selection_rows), default=0)

    pain_points = extract_report_section(research_report, "初步痛点")
    recommendations = extract_report_section(research_report, "切入建议")
    next_steps = extract_report_section(research_report, "下一步")
    single_product_insights = build_single_product_insights(top_product_rows, form_rows, review_rows, review_target_rows)
    form_analysis_cards = build_form_analysis_cards(top_product_rows, form_rows, review_rows)
    review_text_rows = [row for row in review_rows if str(row.get("review_text", "")).strip()]
    review_low_count = sum(1 for row in review_text_rows if to_float(row.get("rating")) <= 3)
    review_high_count = sum(1 for row in review_text_rows if to_float(row.get("rating")) >= 4)
    review_covered_count = sum(1 for row in review_target_rows if to_float(row.get("review_rows_collected")) > 0)
    review_form_note = review_form_coverage_note(review_target_rows)
    review_coverage_table = render_review_coverage(review_target_rows)
    weights = scoring_cfg.get("weights", {})
    thresholds = scoring_cfg.get("recommendation_thresholds", {})
    brand_cfg = scoring_cfg.get("brand_moat", {})
    score_formula = " + ".join(
        [
            f"需求 {weights.get('demand', 0) * 100:.0f}%",
            f"竞争 {weights.get('competition', 0) * 100:.0f}%",
            f"利润 {weights.get('profitability', 0) * 100:.0f}%",
            f"差异化 {weights.get('differentiation', 0) * 100:.0f}%",
            f"风险控制 {weights.get('risk_control', 0) * 100:.0f}%",
        ]
    )
    demand_cfg = scoring_cfg.get("demand", {})
    competition_cfg = scoring_cfg.get("competition", {})
    profit_cfg = scoring_cfg.get("profitability", {})
    risk_cfg = scoring_cfg.get("risk", {})

    dashboard_data = {
        "selection": selection_rows,
        "categoryScanState": category_scan_rows,
        "currentCategoryScan": current_category_rows,
        "categoryExclusions": exclusion_rows,
        "keywordSearchHistory": keyword_search_rows,
        "latestKeywordResults": latest_keyword_ranked_rows,
        "seedArchive": seed_archive_rows,
        "shapeValidation": shape_validation_rows,
        "archive": shape_archive_rows,
        "productResearchArchive": product_research_archive,
        "forms": form_rows,
        "researchKeywords": research_keyword_rows,
        "topProducts": top_product_rows,
        "visualCounts": {
            "forms": top_counts(visual_rows, "visual_product_form", 8),
            "materials": top_counts(visual_rows, "visual_material_signal", 8),
            "packs": top_counts(visual_rows, "visual_pack_count", 8),
            "closures": top_counts(visual_rows, "visual_closure", 8),
            "styles": top_counts(visual_rows, "visual_style", 8),
        },
        "painPoints": pain_points,
        "recommendations": recommendations,
        "nextSteps": next_steps,
    }

    data_json = json.dumps(dashboard_data, ensure_ascii=False)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    exclusion_table_rows = "\n".join(
        "<tr><td><strong>{term}</strong></td><td><span class=\"pill gray\">{kind}</span></td><td>{reason}</td></tr>".format(
            term=escape(row["term"]),
            kind=escape(row["type"]),
            reason=escape(row["reason"]),
        )
        for row in exclusion_rows
    )
    coverage_warning_html = ""
    if current_scanned_category_count >= 20 and selection_category_count < min(20, current_scanned_category_count):
        coverage_warning_html = f"""
      <div class="audit-warning">
        <strong>本轮候选覆盖不足，不能据此判断市场没有机会。</strong>
        扫描记录包含 {current_scanned_category_count} 个类目，但进入评分的候选只覆盖
        {selection_category_count} 个类目；需要按类目均衡抽样后重新扫描和验证。
      </div>"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AMZ 选品自动化仪表盘</title>
  <link rel="icon" href="data:,">
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --panel-soft: #f9fafb;
      --text: #172033;
      --muted: #657184;
      --line: #d9dee8;
      --blue: #2364d2;
      --green: #147d54;
      --orange: #b76b00;
      --red: #b42318;
      --shadow: 0 10px 26px rgba(18, 31, 53, 0.08);
      --radius: 8px;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.5;
    }}

    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}

    .shell {{
      display: grid;
      grid-template-columns: 240px minmax(0, 1fr);
      min-height: 100vh;
    }}

    .sidebar {{
      position: sticky;
      top: 0;
      height: 100vh;
      padding: 22px 18px;
      border-right: 1px solid var(--line);
      background: #ffffff;
    }}

    .brand {{
      margin-bottom: 24px;
    }}

    .brand strong {{
      display: block;
      font-size: 18px;
      letter-spacing: 0;
    }}

    .brand span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }}

    .nav {{
      display: grid;
      gap: 6px;
    }}

    .nav a {{
      display: block;
      color: #334155;
      padding: 9px 10px;
      border-radius: 6px;
      font-weight: 600;
    }}

    .nav a:hover {{
      background: #eef3ff;
      text-decoration: none;
    }}

    .main {{
      padding: 24px;
      max-width: 1500px;
      width: 100%;
      min-width: 0;
    }}

    .topbar {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 18px;
    }}

    h1 {{
      font-size: 24px;
      line-height: 1.2;
      margin: 0 0 6px;
      letter-spacing: 0;
    }}

    h2 {{
      font-size: 18px;
      margin: 0;
      letter-spacing: 0;
    }}

    h3 {{
      font-size: 15px;
      margin: 0;
      letter-spacing: 0;
    }}

    .subtle {{ color: var(--muted); }}

    .timestamp {{
      color: var(--muted);
      white-space: nowrap;
      padding-top: 5px;
    }}

    .audit-warning {{
      margin: 0 0 18px;
      padding: 12px 14px;
      border: 1px solid #f0b35a;
      border-left: 4px solid #c87800;
      border-radius: 6px;
      background: #fff8e8;
      color: #6d4300;
      line-height: 1.6;
    }}

    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}

    .kpi {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 14px;
      min-height: 92px;
      box-shadow: var(--shadow);
    }}

    .kpi .label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}

    .kpi .value {{
      font-size: 26px;
      font-weight: 760;
      line-height: 1;
    }}

    .kpi .note {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }}

    .metric-summary strong {{
      display: block;
      margin-top: 8px;
      font-size: 26px;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }}

    .metric-summary span {{
      display: block;
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }}

    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 18px;
      margin-bottom: 18px;
      box-shadow: var(--shadow);
      min-width: 0;
    }}

    .section-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 14px;
    }}

    .section-head p {{
      margin: 4px 0 0;
      color: var(--muted);
      max-width: 880px;
    }}

    .controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }}

    input,
    select,
    button {{
      min-height: 36px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
    }}

    button {{
      cursor: pointer;
      font-weight: 700;
    }}

    button:hover {{
      background: #eef3ff;
    }}

    input[type="search"] {{
      min-width: 280px;
      flex: 1 1 280px;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      max-width: 100%;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      line-height: 1.3;
      font-weight: 700;
      background: #eef3ff;
      color: #204a9a;
      white-space: nowrap;
    }}

    .pill.green {{ background: #e8f6ef; color: var(--green); }}
    .pill.orange {{ background: #fff2dc; color: var(--orange); }}
    .pill.red {{ background: #ffe9e6; color: var(--red); }}
    .pill.gray {{ background: #eef0f3; color: #4b5563; }}

    .table-wrap {{
      overflow: auto;
      max-width: 100%;
      border: 1px solid var(--line);
      border-radius: var(--radius);
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 980px;
      background: #fff;
    }}

    th,
    td {{
      padding: 10px 11px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}

    th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f2f5f9;
      color: #405066;
      font-size: 12px;
      text-transform: uppercase;
    }}

    tr:last-child td {{ border-bottom: 0; }}

    .num {{
      text-align: right;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }}

    .title-cell {{
      min-width: 320px;
      max-width: 520px;
      font-weight: 650;
    }}

    .muted-small {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
      margin-top: 3px;
    }}

    .score {{
      display: grid;
      gap: 4px;
      min-width: 88px;
    }}

    .score b {{
      font-variant-numeric: tabular-nums;
    }}

    .shape-list,
    .opportunity-pool {{
      display: grid;
      gap: 12px;
    }}

    .validation-card,
    .category-group {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fff;
      overflow: hidden;
    }}

    .validation-head,
    .category-group-head {{
      display: grid;
      grid-template-columns: minmax(260px, 1.4fr) minmax(460px, 2fr);
      gap: 14px;
      padding: 14px;
      background: #fbfcfe;
      border-bottom: 1px solid var(--line);
    }}

    .validation-title a,
    .pool-item-title a {{
      display: inline-block;
      font-weight: 780;
      font-size: 17px;
      line-height: 1.25;
    }}

    .validation-title h3,
    .category-group-head h3 {{
      margin: 0 0 5px;
      font-size: 16px;
    }}

    .validation-status {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-top: 8px;
    }}

    .validation-metrics {{
      display: grid;
      grid-template-columns: 112px 112px 112px 112px minmax(180px, 1fr);
      gap: 10px;
      align-items: start;
    }}

    .validation-metric {{
      min-width: 0;
    }}

    .validation-metric strong {{
      display: block;
      font-size: 18px;
      font-variant-numeric: tabular-nums;
      line-height: 1.2;
    }}

    .validation-body {{
      padding: 14px;
      display: grid;
      grid-template-columns: minmax(260px, 0.9fr) minmax(420px, 1.6fr);
      gap: 14px;
    }}

    .validation-note {{
      color: var(--muted);
      line-height: 1.5;
      margin: 0;
    }}

    .evidence-box {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      overflow: hidden;
    }}

    .evidence-box h4 {{
      margin: 0;
      padding: 10px 12px;
      font-size: 13px;
      background: #f2f5f9;
      color: #405066;
    }}

    .evidence-row {{
      display: grid;
      grid-template-columns: minmax(160px, 1.2fr) 132px 72px 76px 76px;
      gap: 10px;
      padding: 10px 12px;
      border-top: 1px solid var(--line);
      align-items: start;
    }}

    .evidence-row .pill {{
      white-space: normal;
    }}

    .evidence-row:first-of-type {{
      border-top: 0;
    }}

    .evidence-form {{
      font-weight: 720;
      line-height: 1.3;
    }}

    .pool-list {{
      display: grid;
      gap: 10px;
      padding: 14px;
    }}

    .pool-item {{
      display: grid;
      grid-template-columns: minmax(260px, 1.3fr) repeat(4, 94px) minmax(260px, 1.5fr);
      gap: 12px;
      align-items: start;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel-soft);
    }}

    .pool-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }}

    .shape-cell-label {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      margin-bottom: 3px;
    }}

    .shape-metric {{
      font-variant-numeric: tabular-nums;
      font-weight: 720;
      line-height: 1.25;
    }}

    .shape-note {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}

    .shape-note + .shape-note {{
      margin-top: 4px;
    }}

    .bar {{
      height: 6px;
      border-radius: 999px;
      background: #e7ebf2;
      overflow: hidden;
    }}

    .bar span {{
      display: block;
      height: 100%;
      width: var(--w);
      background: var(--blue);
    }}

    .card-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}

    .summary-card {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 13px;
      background: var(--panel-soft);
      min-height: 150px;
    }}

    .summary-card .metric-row {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }}

    .metric {{
      padding-top: 8px;
      border-top: 1px solid var(--line);
    }}

    .metric strong {{
      display: block;
      font-size: 16px;
      font-variant-numeric: tabular-nums;
    }}

    .metric span {{
      color: var(--muted);
      font-size: 12px;
    }}

    .logic-grid,
    .next-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}

    .logic-block,
    .next-block {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 14px;
      background: var(--panel-soft);
    }}

    .logic-block ul,
    .next-block ul,
    .bullet-list {{
      margin: 10px 0 0;
      padding-left: 18px;
      color: #2d3748;
    }}

    .logic-block li,
    .next-block li,
    .bullet-list li {{
      margin: 6px 0;
    }}

    .scoring-wide {{
      grid-column: 1 / -1;
      background: #ffffff;
    }}

    .formula-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-top: 12px;
    }}

    .formula-card {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 12px;
      background: var(--panel-soft);
    }}

    .formula-card strong {{
      display: block;
      font-size: 18px;
      margin-bottom: 4px;
    }}

    .formula-card span {{
      color: var(--muted);
      font-size: 12px;
    }}

    .review-strip {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin: 10px 0 14px;
    }}

    .review-stat {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 12px;
      background: var(--panel-soft);
    }}

    .review-stat span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
    }}

    .review-stat strong {{
      display: block;
      margin-top: 4px;
      font-size: 22px;
      font-variant-numeric: tabular-nums;
    }}

    .review-coverage-table {{
      margin-bottom: 18px;
    }}

    .form-photo-overview {{
      margin-top: 18px;
    }}

    .form-photo-overview > p {{
      color: var(--muted);
      margin: 6px 0 12px;
    }}

    .form-overview-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}

    .form-overview-card {{
      display: grid;
      grid-template-columns: 132px minmax(0, 1fr);
      gap: 12px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 10px;
      background: #fff;
      min-height: 142px;
    }}

    .form-overview-images {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      align-content: start;
    }}

    .overview-image-tile,
    .form-image-tile {{
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 650;
      min-width: 0;
    }}

    .overview-image-tile:hover,
    .form-image-tile:hover {{
      text-decoration: none;
    }}

    .overview-image-tile img,
    .form-image-tile img {{
      width: 100%;
      aspect-ratio: 1 / 1;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #f7f8fa;
    }}

    .overview-image-tile span,
    .form-image-tile span {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .form-overview-body h4 {{
      margin: 0 0 5px;
      font-size: 15px;
    }}

    .form-overview-body p {{
      margin: 0;
      color: var(--muted);
      min-height: 42px;
    }}

    .form-overview-metrics {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }}

    .form-overview-metrics span {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 7px;
      color: var(--muted);
      font-size: 12px;
      background: var(--panel-soft);
    }}

    .image-empty {{
      grid-column: 1 / -1;
      padding: 10px;
      min-height: 78px;
      display: grid;
      place-items: center;
    }}

    .form-analysis-section {{
      margin-top: 18px;
    }}

    .form-analysis-section > p {{
      color: var(--muted);
      margin-top: -4px;
    }}

    .form-analysis-card {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #ffffff;
      margin-top: 12px;
      overflow: hidden;
    }}

    .form-card-head {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      padding: 14px 16px;
      background: var(--panel-soft);
      border-bottom: 1px solid var(--line);
    }}

    .form-card-head h4 {{
      margin: 0 0 4px;
      font-size: 19px;
    }}

    .form-card-head p {{
      margin: 0;
      color: var(--muted);
    }}

    .form-card-side {{
      display: grid;
      justify-items: end;
      align-content: start;
      gap: 8px;
      min-width: 126px;
    }}

    .form-card-score {{
      min-width: 96px;
      text-align: right;
    }}

    .form-card-score strong {{
      display: block;
      font-size: 24px;
      font-variant-numeric: tabular-nums;
    }}

    .form-card-score span {{
      color: var(--muted);
      font-size: 12px;
    }}

    .form-card-metrics {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 10px 16px;
      border-bottom: 1px solid var(--line);
    }}

    .form-card-metrics span {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 9px;
      background: #fff;
      color: var(--muted);
      font-size: 12px;
    }}

    .form-card-takeaway {{
      display: grid;
      grid-template-columns: 82px minmax(0, 1fr);
      gap: 10px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: #f7fbff;
    }}

    .form-card-takeaway strong {{
      color: var(--blue);
      font-size: 13px;
    }}

    .form-card-takeaway span {{
      color: var(--text);
      font-weight: 650;
    }}

    .form-card-images {{
      display: grid;
      grid-template-columns: 82px minmax(0, 1fr);
      gap: 10px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }}

    .form-card-images > strong {{
      color: var(--blue);
      font-size: 13px;
    }}

    .form-card-images > div {{
      display: grid;
      grid-template-columns: repeat(2, 120px);
      gap: 10px;
      align-items: start;
    }}

    .form-card-body {{
      display: grid;
      grid-template-columns: 1.05fr 1.05fr 1.15fr 0.95fr;
      gap: 16px;
      padding: 14px 16px 16px;
    }}

    .form-card-body h5 {{
      margin: 0 0 8px;
      font-size: 14px;
    }}

    .form-card-body ul {{
      margin: 0;
      padding-left: 18px;
    }}

    .form-card-body li {{
      margin: 6px 0;
      color: #2d3748;
    }}

    .form-card-body li b {{
      display: block;
      color: var(--text);
      font-weight: 750;
    }}

    .form-card-body li span {{
      display: block;
      color: #334155;
      margin-top: 2px;
    }}

    .form-card-body p {{
      margin: 0 0 12px;
      color: #2d3748;
    }}

    .rep-list li span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }}

    .research-layout {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 14px;
      align-items: start;
    }}

    .count-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}

    .count-box {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 12px;
      background: #fff;
    }}

    .count-row {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 5px 0;
      border-bottom: 1px solid #edf0f4;
    }}

    .count-row:last-child {{ border-bottom: 0; }}

    .insight-section {{
      margin-top: 18px;
      padding-top: 16px;
      border-top: 1px solid var(--line);
    }}

    .insight-section > p {{
      color: var(--muted);
      margin: 6px 0 12px;
    }}

    .insight-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}

    .insight-card {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 14px;
      background: #fff;
      min-height: 210px;
    }}

    .insight-card h4 {{
      margin: 0 0 8px;
      font-size: 13px;
      color: var(--muted);
    }}

    .insight-card strong {{
      display: block;
      font-size: 15px;
      line-height: 1.35;
      margin-bottom: 8px;
    }}

    .insight-card ul {{
      margin: 0;
      padding-left: 18px;
      color: #334155;
    }}

    .insight-card li {{
      margin: 5px 0;
    }}

    .insight-card p {{
      margin: 10px 0 0;
      color: var(--blue);
      font-weight: 650;
    }}

    .gallery-controls {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 12px 0;
    }}

    .gallery {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}

    .product-tile {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fff;
      overflow: hidden;
    }}

    .product-tile img {{
      display: block;
      width: 100%;
      aspect-ratio: 1 / 1;
      object-fit: contain;
      background: #f7f8fa;
      border-bottom: 1px solid var(--line);
    }}

    .tile-body {{
      padding: 10px;
    }}

    .tile-title {{
      display: block;
      font-weight: 700;
      min-height: 42px;
      color: var(--text);
    }}

    .tile-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin-top: 8px;
    }}

    .contact-sheet {{
      width: 100%;
      max-height: 760px;
      object-fit: contain;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fff;
    }}

    .empty {{
      color: var(--muted);
      padding: 16px;
      border: 1px dashed var(--line);
      border-radius: var(--radius);
      background: #fff;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}

    @media (max-width: 1100px) {{
      .shell {{ grid-template-columns: 1fr; }}
      .sidebar {{
        position: static;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      .nav {{
        grid-template-columns: repeat(4, minmax(0, 1fr));
      }}
      .kpi-grid,
      .card-grid,
      .gallery {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .logic-grid,
      .next-grid,
      .insight-grid,
      .form-overview-grid,
      .research-layout,
      .form-card-body {{
        grid-template-columns: 1fr;
      }}
      .formula-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .validation-head,
      .validation-body,
      .category-group-head,
      .pool-item {{
        grid-template-columns: 1fr;
      }}
      .validation-metrics {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }}
      .evidence-row {{
        grid-template-columns: minmax(180px, 1.4fr) 132px repeat(3, 86px);
      }}
    }}

    @media (max-width: 700px) {{
      .main {{ padding: 14px; }}
      .sidebar {{ padding: 12px 14px 10px; }}
      .brand {{ margin-bottom: 8px; }}
      .brand strong {{ font-size: 16px; }}
      .brand span {{ display: none; }}
      .nav {{
        display: flex;
        gap: 4px;
        overflow-x: auto;
        padding-bottom: 2px;
        scrollbar-width: thin;
      }}
      .nav a {{
        flex: 0 0 auto;
        padding: 7px 9px;
        font-size: 13px;
        white-space: nowrap;
      }}
      .topbar,
      .section-head {{
        display: block;
      }}
      .timestamp {{ margin-top: 8px; white-space: normal; }}
      .kpi-grid,
      .card-grid,
      .gallery,
      .count-grid,
      .review-strip {{
        grid-template-columns: 1fr;
      }}
      .formula-grid {{
        grid-template-columns: 1fr;
      }}
      .validation-metrics,
      .evidence-row {{
        grid-template-columns: 1fr;
      }}
      .form-card-takeaway {{
        grid-template-columns: 1fr;
      }}
      .form-card-images,
      .form-overview-card {{
        grid-template-columns: 1fr;
      }}
      .form-card-images > div {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      input[type="search"] {{ min-width: 100%; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <strong>AMZ 选品自动化</strong>
        <span>从挖掘到单品机会研究</span>
      </div>
      <nav class="nav">
        <a href="#overview">总览</a>
        <a href="#discovery">种子发现</a>
        <a href="#category-coverage">扫描覆盖</a>
        <a href="#keyword-search">关键词选品</a>
        <a href="#shape-validation">候选验证</a>
        <a href="#archive">机会池</a>
        <a href="#research-archive">单品研究档案</a>
        <a href="#research">当前单品研究</a>
        <a href="#logic">生成逻辑</a>
        <a href="#weekly-scan">每周扫描</a>
        <a href="#next">下一步</a>
      </nav>
    </aside>

    <main class="main">
      <div class="topbar" id="overview">
        <div>
          <h1>Amazon US 选品工作台</h1>
          <div class="subtle">候选品、竞品结构、图片识别、材质判断、切入建议集中展示</div>
        </div>
        <div class="timestamp">生成时间：{escape(generated_at)}</div>
      </div>

      {coverage_warning_html}

      <div class="kpi-grid">
        <div class="kpi">
          <div class="label">扫描种子</div>
          <div class="value">{len(selection_rows)}</div>
          <div class="note">只作为类目/形态入口，不直接等于机会</div>
        </div>
        <div class="kpi">
          <div class="label">候选覆盖类目</div>
          <div class="value">{selection_category_count}</div>
          <div class="note">本轮扫描 {current_scanned_category_count} 类目；最高种子分 {top_selection_score:.1f}</div>
        </div>
        <div class="kpi">
          <div class="label">待补 Top100</div>
          <div class="value">{needs_category_count}</div>
          <div class="note">种子看起来可疑，但还没完成最小类目验证</div>
        </div>
        <div class="kpi">
          <div class="label">通过验证机会</div>
          <div class="value">{shape_opportunity_count}</div>
          <div class="note">类目 Top100 + 形态拆分后仍成立</div>
        </div>
        <div class="kpi">
          <div class="label">机会池档案</div>
          <div class="value">{len(shape_archive_rows)}</div>
          <div class="note">本轮仍有效 {shape_archive_active_count} 个，旧机会不覆盖</div>
        </div>
      </div>

      <section id="discovery">
        <div class="section-head">
          <div>
            <h2>种子发现</h2>
            <p>这里是第一层粗筛，共 {len(selection_rows)} 个种子，覆盖 {selection_category_count} 个来源类目；种子只负责提供切入口，不能直接进入机会档案。必须经过最小类目 Top100 和形态验证后，才算真正机会。</p>
          </div>
        </div>
        <div class="controls">
          <input id="selectionSearch" type="search" placeholder="搜索产品 / ASIN / 类目">
          <select id="selectionStrategy">{render_options(selection_strategies, "全部策略")}</select>
          <select id="selectionRecommendation">{render_options(selection_recommendations, "全部结论")}</select>
          <select id="selectionLimit">
            <option value="20">显示 20 个</option>
            <option value="50">显示 50 个</option>
            <option value="999">显示全部</option>
          </select>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rank</th>
                <th>产品</th>
                <th>策略</th>
                <th class="num">分数</th>
                <th class="num">证据</th>
                <th class="num">售价</th>
                <th class="num">单件毛利</th>
                <th class="num">毛利率</th>
                <th class="num">月毛利</th>
                <th class="num">月销</th>
                <th>风险</th>
              </tr>
            </thead>
            <tbody id="selectionBody"></tbody>
          </table>
        </div>
      </section>

      <section id="category-coverage">
        <div class="section-head">
          <div>
            <h2>类目扫描覆盖</h2>
            <p>每周优先选择从未扫描的最小类目；全部覆盖后，再按最久未扫描排序复查。明确属于大件、强合规、强专利/品牌垄断或高责任风险的类目永久跳过，不占每周配额。</p>
          </div>
        </div>
        <div class="next-grid">
          <div class="next-block">
            <h3>累计已扫描</h3>
            <div class="metric-summary"><strong>{len(category_scan_rows)}</strong><span>按 Category ID 去重并长期保留状态</span></div>
          </div>
          <div class="next-block">
            <h3>当前轮次</h3>
            <div class="metric-summary"><strong>{len(current_category_rows)}</strong><span>新类目 {current_new_category_count} · 到期复扫 {current_rescan_count}</span></div>
          </div>
          <div class="next-block">
            <h3>本轮查看产品</h3>
            <div class="metric-summary"><strong>{current_products_examined_label}</strong><span>{current_products_examined_note}</span></div>
          </div>
        </div>
        <h3 style="margin-top: 18px;">当前轮次类目</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>最小类目</th><th>轮换原因</th><th class="num">查看产品</th><th class="num">初筛候选</th><th>上次扫描</th></tr></thead>
            <tbody id="currentCategoryScanBody"></tbody>
          </table>
        </div>
        <h3 style="margin-top: 18px;">永久排除规则</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>类目路径匹配</th><th>风险类型</th><th>不再重复扫描的原因</th></tr></thead>
            <tbody>{exclusion_table_rows or '<tr><td colspan="3"><div class="empty">还没有永久排除规则。</div></td></tr>'}</tbody>
          </table>
        </div>
      </section>

      <section id="keyword-search">
        <div class="section-head">
          <div>
            <h2>关键词选品</h2>
            <p>输入你关心的关键词后，通过 Sorftime 搜索最多 100 个相关产品并按同一套个人卖家标准初筛。关键词结果只是候选入口，仍需通过最小类目和产品形态验证后才能进入正式机会池。</p>
          </div>
        </div>
        <div class="controls">
          <input id="keywordSearchInput" type="search" placeholder="输入关键词，例如 bread storage bag">
          <button id="keywordSearchCommand" type="button">生成搜索命令</button>
        </div>
        <div id="keywordSearchHint" class="empty" style="display: none; margin-bottom: 12px;"></div>
        <h3>搜索档案</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>关键词</th><th>搜索时间</th><th class="num">相关产品</th><th class="num">通过基础过滤</th><th class="num">Go / Watch</th><th class="num">最高分</th><th>档案</th></tr></thead>
            <tbody id="keywordSearchHistoryBody"></tbody>
          </table>
        </div>
        <h3 style="margin-top: 18px;">最近一次关键词结果</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>产品</th><th class="num">分数</th><th>结论</th><th class="num">月销</th><th class="num">评论</th><th class="num">售价</th><th>风险</th></tr></thead>
            <tbody id="keywordResultBody"></tbody>
          </table>
        </div>
      </section>

      <section id="shape-validation">
        <div class="section-head">
          <div>
            <h2>候选产品验证</h2>
            <p>每个候选 ASIN 只显示一张验证卡。它会先进最小类目看 Top 产品，再把相邻形态作为证据列在卡片里；证据形态不会单独进入机会池，避免同一个类目反复看。</p>
          </div>
        </div>
        <div class="controls">
          <input id="shapeSearch" type="search" placeholder="搜索种子 / 形态 / 类目">
          <select id="shapeRecommendation">{render_options(shape_recommendations, "全部结论")}</select>
        </div>
        <div class="shape-list" id="shapeValidationList"></div>
      </section>

      <section id="archive">
        <div class="section-head">
          <div>
            <h2>机会池</h2>
            <p>只有候选产品通过最小类目 Top 验证后才会进入这里。机会按类目归档，同类目放在一起，后续你从这里挑产品进入单品深度研究。</p>
          </div>
        </div>
        <div class="controls">
          <input id="archiveSearch" type="search" placeholder="搜索机会 / ASIN / 类目">
          <select id="archiveStatus">
            <option value="">全部状态</option>
            <option value="active_in_latest_run" selected>本轮仍出现</option>
            <option value="not_in_latest_run">未在本轮出现</option>
          </select>
        </div>
        <div class="opportunity-pool" id="archiveBody"></div>
      </section>

      <section id="research-archive">
        <div class="section-head">
          <div>
            <h2>单品研究入口和档案</h2>
            <p>这里保留所有做过的研究，包括已淘汰产品；它是历史档案，不等于当前推荐。请先看“结论”和原因，再决定是否打开研究页。</p>
          </div>
        </div>
        <div class="controls">
          <input id="productResearchAsin" type="search" placeholder="输入 ASIN，例如 B0FS1YH17C">
          <button id="productResearchOpen" type="button">打开已有研究</button>
          <button id="productResearchCommand" type="button">生成研究命令</button>
        </div>
        <div id="productResearchHint" class="empty" style="display: none; margin-bottom: 12px;"></div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>研究产品</th>
                <th>主形态</th>
                <th>结论</th>
                <th class="num">产品池</th>
                <th class="num">形态</th>
                <th class="num">评论</th>
                <th>最近研究</th>
                <th>入口</th>
              </tr>
            </thead>
            <tbody id="productResearchArchiveBody"></tbody>
          </table>
        </div>
      </section>

      <section id="research">
        <div class="section-head">
          <div>
            <h2>历史单品研究：Beeswax Bread Bags</h2>
            <p><strong>当前结论：已淘汰。</strong> 该产品直接接触食品，对个人卖家存在额外材料安全、迁移测试和合规资质风险。以下内容仅作历史研究证据，不代表当前机会推荐。</p>
            <p>后续你可以指定任意 ASIN 开同样的研究页；系统会优先补它所在最小类目 Top100，再把形态、材质、套装、图片、评论痛点和切入方向一起存档。</p>
          </div>
          <a class="pill green" href="https://www.amazon.com/dp/B0FS1YH17C" target="_blank" rel="noreferrer">打开目标 Listing</a>
        </div>

        <div class="insight-section">
          <h3>评论覆盖</h3>
          <p>{escape(str(single_product_insights["review_source_note"]))}</p>
          <div class="review-strip">
            <div class="review-stat"><span>已跑 ASIN</span><strong>{len(review_target_rows)}</strong></div>
            <div class="review-stat"><span>有评论返回</span><strong>{review_covered_count}</strong></div>
            <div class="review-stat"><span>已读评论正文</span><strong>{len(review_text_rows)}</strong></div>
            <div class="review-stat"><span>低星 / 高星</span><strong>{review_low_count} / {review_high_count}</strong></div>
          </div>
          {review_coverage_table}
        </div>

        <div class="form-photo-overview">
          <h3>产品形态照片总览</h3>
          <p>先用图片建立类目地图：每个形态展示 1-2 个代表 listing，下面再看销量、评论和切入判断。</p>
          {render_form_photo_overview(form_analysis_cards)}
        </div>

        <div class="research-layout">
          <div>
            <h3>产品形态拆分</h3>
            <div class="table-wrap" style="margin-top: 10px;">
              <table>
                <thead>
                  <tr>
                    <th>形态</th>
                    <th class="num">数量</th>
                    <th class="num">直接/关键词</th>
                    <th class="num">均价</th>
                    <th class="num">月销均值</th>
                    <th class="num">评论中位数</th>
                    <th>材质 / 套装</th>
                    <th>机会备注</th>
                  </tr>
                </thead>
                <tbody id="formsBody"></tbody>
              </table>
            </div>
          </div>
          <div>
            <h3>视觉识别摘要</h3>
            <div class="count-grid" id="visualCounts" style="margin-top: 10px;"></div>
          </div>
        </div>

        <div class="form-analysis-section">
          <h3>按产品形态拆解：特征 / 评论 / 切入</h3>
          <p>每个形态下面直接合并产品维度、评论信号和可执行切入方向。{escape(review_form_note)}</p>
          {render_form_analysis_cards(form_analysis_cards)}
        </div>

        <div class="insight-section">
          <h3>整体机会结论</h3>
          <p>先看各形态卡片里的切入判断，再用这里做总体决策。机会不是“beeswax bread bag 这个词能卖”，而是从材质、规格、配件、视觉和用户痛点里找到更具体的切入点。</p>
          <div class="insight-grid">
            {render_insight_cards(single_product_insights["conclusions"])}
          </div>
        </div>

        <div style="margin-top: 18px;">
          <h3>关键词与需求入口</h3>
          <div class="table-wrap" style="margin-top: 10px;">
            <table>
              <thead>
                <tr>
                  <th>关键词</th>
                  <th class="num">搜索量</th>
                  <th class="num">90天点击</th>
                  <th class="num">展示份额</th>
                  <th>Top3 ASIN</th>
                </tr>
              </thead>
              <tbody id="researchKeywordBody"></tbody>
            </table>
          </div>
        </div>

        <div style="margin-top: 18px;">
          <div class="section-head" style="margin-bottom: 0;">
            <div>
              <h3>图片识别产品池</h3>
              <p>材质由标题、属性、描述和详情文本综合判断；视觉标签来自主图识别。</p>
            </div>
          </div>
          <div class="gallery-controls">
            <select id="galleryForm">{render_options(gallery_forms, "全部视觉形态")}</select>
            <input id="gallerySearch" type="search" placeholder="搜索 ASIN / 标题 / 材质 / 风格">
          </div>
          <div class="gallery" id="productGallery"></div>
        </div>

        <div style="margin-top: 18px;">
          <h3>主图总览</h3>
          <img class="contact-sheet" src="../research/B0FS1YH17C/image_contact_sheet.jpg" alt="B0FS1YH17C image contact sheet">
        </div>
      </section>

      <section id="logic">
        <div class="section-head">
          <div>
            <h2>生成逻辑和筛选标准</h2>
            <p>这部分对应整个流程背后的判断口径，避免只看到分数看不到原因。</p>
          </div>
        </div>
        <div class="logic-grid">
          <div class="logic-block scoring-wide">
            <h3>新版判断链路</h3>
            <p class="subtle" style="margin: 6px 0 0;">现在不再把“某个 listing 看起来不错”直接当机会。完整判断对象是：一个最小类目里的某个产品形态。</p>
            <div class="formula-grid">
              <div class="formula-card">
                <strong>1</strong>
                <span><b>种子发现：</b>用多策略找入口 ASIN。这里的分数只是粗筛分，用来决定是否值得进入类目验证。</span>
              </div>
              <div class="formula-card">
                <strong>2</strong>
                <span><b>最小类目 Top 产品：</b>看整个类目是否有评论墙、头部品牌集中、低评高销缺口、CN/HK/FBA 占比和真实需求。</span>
              </div>
              <div class="formula-card">
                <strong>3</strong>
                <span><b>产品形态拆分：</b>同一个类目下再按形态、材质、套装、功能、尺寸、视觉元素拆开，避免马桶刷这类红海类目的局部误判。</span>
              </div>
              <div class="formula-card">
                <strong>4</strong>
                <span><b>机会池：</b>只有类目和形态都通过，才进入档案。旧机会不会被每周更新覆盖，会保留历史。</span>
              </div>
              <div class="formula-card">
                <strong>5</strong>
                <span><b>单品研究：</b>通过后的形态才继续读评论、看图片、拆材质规格和切入方向。</span>
              </div>
            </div>
            <ul>
              <li><b>Shape opportunity：</b>有最小类目 Top 产品数据，且种子所在形态通过需求、评论、低评高销和风险检查，才进入机会池。</li>
              <li><b>Watch shape：</b>相邻形态或数据不错但还不够确定，只保留观察，不当作当前主机会。</li>
              <li><b>Needs category Top100：</b>种子 ASIN 看起来可能有机会，但还缺最小类目 Top 产品验证。</li>
              <li><b>Reject category/form：</b>类目或形态存在评论墙、头部品牌集中、低评高销缺口不足、大件/高风险等问题。</li>
            </ul>
          </div>
          <div class="logic-block scoring-wide">
            <h3>种子粗筛分怎么算</h3>
            <p class="subtle" style="margin: 6px 0 0;">当前公式：{escape(score_formula)}。这个分数只负责找入口，不是最终机会分。所有子项先转成 0-100 分，再按权重合并。</p>
            <p class="subtle" style="margin: 6px 0 0;">正向指标按“低于下限=0，高于上限=100，中间线性换算”；反向指标按“好值以下=100，坏值以上=0，中间线性扣分”。</p>
            <div class="formula-grid">
              <div class="formula-card">
                <strong>{weights.get('demand', 0) * 100:.0f}%</strong>
                <span>需求：月销量 {demand_cfg.get('est_monthly_sales', {}).get('min', 0)}-{demand_cfg.get('est_monthly_sales', {}).get('max', 0)}、关键词搜索量 {demand_cfg.get('keyword_search_volume', {}).get('min', 0)}-{demand_cfg.get('keyword_search_volume', {}).get('max', 0)}</span>
              </div>
              <div class="formula-card">
                <strong>{weights.get('competition', 0) * 100:.0f}%</strong>
                <span>竞争：评论数越低越好，{competition_cfg.get('avg_review_count', {}).get('good', 0)} 以下好，{competition_cfg.get('avg_review_count', {}).get('bad', 0)} 以上差；Top10 集中度和 CPC 越低越好</span>
              </div>
              <div class="formula-card">
                <strong>{weights.get('profitability', 0) * 100:.0f}%</strong>
                <span>利润：毛利率 {profit_cfg.get('gross_margin', {}).get('min', 0) * 100:.0f}%-{profit_cfg.get('gross_margin', {}).get('max', 0) * 100:.0f}%、单件毛利 ${profit_cfg.get('gross_profit_per_unit', {}).get('min', 0):,.0f}-${profit_cfg.get('gross_profit_per_unit', {}).get('max', 0):,.0f}、月毛利 ${profit_cfg.get('monthly_gross_profit', {}).get('min', 0):,.0f}-${profit_cfg.get('monthly_gross_profit', {}).get('max', 0):,.0f}</span>
              </div>
              <div class="formula-card">
                <strong>{weights.get('differentiation', 0) * 100:.0f}%</strong>
                <span>差异化：早期默认给基础分，后续靠竞品形态、评论痛点、图片识别和供应商方案修正</span>
              </div>
              <div class="formula-card">
                <strong>{weights.get('risk_control', 0) * 100:.0f}%</strong>
                <span>风险控制：合规 {risk_cfg.get('compliance_risk', 0) * 100:.0f}%、易碎 {risk_cfg.get('fragile_risk', 0) * 100:.0f}%、大件 {risk_cfg.get('oversize_risk', 0) * 100:.0f}%、季节性 {risk_cfg.get('seasonality_score', 0) * 100:.0f}%</span>
              </div>
            </div>
            <div class="table-wrap" style="margin-top: 14px;">
              <table>
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>在总分里</th>
                    <th>当前阈值</th>
                    <th>怎么理解</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><b>月销量</b></td>
                    <td>需求分的 75%，总分约 11.25%</td>
                    <td>50 以下=0 分；100 约 11 分；200 约 33 分；300 约 56 分；500 以上=100 分</td>
                    <td>个人卖家口径里，100+ 算有基础需求，300+ 已经值得看，500+ 就给满分。高于 500 不继续加分，避免只追高销量红海。</td>
                  </tr>
                  <tr>
                    <td><b>关键词搜索量</b></td>
                    <td>需求分的 25%，总分约 3.75%</td>
                    <td>300 以下=0 分；1,000 约 4 分；5,000 约 24 分；10,000 约 49 分；20,000 以上=100 分</td>
                    <td>搜索量只做辅助，不让大词绑架决策。对个人卖家来说，精准小词比泛大词更重要。</td>
                  </tr>
                  <tr>
                    <td><b>平均评论数</b></td>
                    <td>竞争分的 50%，总分约 15%</td>
                    <td>20 以下=100 分；21-50=90 分；51-100=80 分；101-200=65 分；201-400=45 分；401-800=20 分；800 以上=0 分</td>
                    <td>评论门槛现在按个人卖家重设。100评以内是重点区间；200评以上开始谨慎；400评以上偏重；800评以上基本不适合冷启动。</td>
                  </tr>
                  <tr>
                    <td><b>Top10 评论集中度</b></td>
                    <td>竞争分的 25%，总分约 5%</td>
                    <td>20% 以下=100 分；40% 约 56 分；65% 以上=0 分</td>
                    <td>头部卖家评论占比越高，说明市场越被头部吃掉。超过 65% 会打“头部集中”风险标。</td>
                  </tr>
                  <tr>
                    <td><b>关键词 CPC</b></td>
                    <td>竞争分的 15%，总分约 4.5%</td>
                    <td>$0.3 以下=100 分；$0.9 约 50 分；$1.5 以上=0 分</td>
                    <td>CPC 越高，冷启动广告成本越重。个人卖家优先找低广告成本入口。</td>
                  </tr>
                  <tr>
                    <td><b>评分缺口</b></td>
                    <td>竞争分的 10%，总分约 3%</td>
                    <td>3.7-4.2=85 分；4.2-4.5=65 分；4.5 以上=25 分；低于 3.7=35 分</td>
                    <td>评分 3.7-4.2 代表有改良空间；4.5+ 说明用户已经满意，靠质量改良的空间小；低于 3.7 可能是产品本身难做。</td>
                  </tr>
                  <tr>
                    <td><b>毛利率</b></td>
                    <td>利润分的 40%，总分约 14%</td>
                    <td>25% 以下=0 分；35% 约 33 分；45% 约 67 分；55% 以上=100 分</td>
                    <td>进入供应商验证要求至少 35%。这里目前是估算成本，拿到真实报价后必须复算。</td>
                  </tr>
                  <tr>
                    <td><b>单件毛利</b></td>
                    <td>利润分的 40%，总分约 14%</td>
                    <td>$5 以下=0 分；$8 约 15 分；$15 约 50 分；$25 以上=100 分</td>
                    <td>这是个人卖家版本新增的核心指标。进入供应商验证要求单件毛利至少 $8，优先找 $15+ 的产品。</td>
                  </tr>
                  <tr>
                    <td><b>月毛利</b></td>
                    <td>利润分的 20%，总分约 7%</td>
                    <td>$1,000 以下=0 分；$3,000 约 29 分；$5,000 约 57 分；$8,000 以上=100 分</td>
                    <td>月毛利只是辅助，不再鼓励追很高销量。超过 $8,000 不继续加分。</td>
                  </tr>
                  <tr>
                    <td><b>风险控制</b></td>
                    <td>总分 15%</td>
                    <td>合规风险占风险分 40%；易碎 20%；大件 20%；季节性 20%</td>
                    <td>风险分越高，风险控制分越低。合规 80+ 直接淘汰；大件/易碎/强季节会明显压分。</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="logic-block scoring-wide">
            <h3>形态机会怎么判定</h3>
            <div class="table-wrap" style="margin-top: 10px;">
              <table>
                <thead>
                  <tr>
                    <th>检查项</th>
                    <th>当前口径</th>
                    <th>为什么这么设</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><b>数据层</b></td>
                    <td>优先要求最小类目 Top 产品数据；只有种子粗筛数据时最多进入 Needs category Top100。</td>
                    <td>单个 listing 容易误判，最小类目的 Top 产品结构更能反映真实竞争。</td>
                  </tr>
                  <tr>
                    <td><b>类目评论墙</b></td>
                    <td>类目评论中位数 >= 1000，或 Top10 评论中位数 >= 1500，会强烈压分。</td>
                    <td>个人卖家冷启动最怕整个类目都被老链接占住。</td>
                  </tr>
                  <tr>
                    <td><b>头部品牌集中</b></td>
                    <td>Top10 里同一头部品牌占比 >= 35% 视为集中风险。</td>
                    <td>这类类目往往不是“产品问题”，而是品牌/渠道护城河问题。</td>
                  </tr>
                  <tr>
                    <td><b>形态样本量</b></td>
                    <td>同一产品形态至少 2 个样本；样本太少只观察，不直接进机会档案。</td>
                    <td>避免被单个异常 listing 误导。</td>
                  </tr>
                  <tr>
                    <td><b>形态需求</b></td>
                    <td>形态平均月销至少 500；低于这个值通常先观察。</td>
                    <td>你的目标不是追极高销量，但也要有可验证的稳定需求。</td>
                  </tr>
                  <tr>
                    <td><b>形态竞争</b></td>
                    <td>形态评论中位数 <= 300 更友好；或至少有 3 个低评高销样本。</td>
                    <td>低评论还能卖，说明这个形态可能仍有进入空间。</td>
                  </tr>
                  <tr>
                    <td><b>主动机会</b></td>
                    <td>只把种子对应形态、形态分 >= 65、且无硬伤的结果记为 Shape opportunity。</td>
                    <td>相邻形态可以启发方向，但不能混成同一个机会结论。</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="logic-block">
            <h3>1. 种子发现</h3>
            <ul>
              <li>用多策略扫描，不依赖单一关键词：轻小泛品、耗材替换、新品低评、评分缺口、高客单轻量。</li>
              <li>先排除明显不适合小卖家的类目和关键词，例如电子核心件、成人、强合规、高破损、超大件。</li>
              <li>再排除明显大品牌/官方品牌产品，例如 Dyson、OXO、KitchenAid、Made In 这类品牌护城河过强的候选。</li>
              <li>种子粗筛分综合需求、竞争、利润和风险，只用来决定是否进入下一层。</li>
            </ul>
          </div>
          <div class="logic-block">
            <h3>2. 类目/形态验证</h3>
            <ul>
              <li>每个候选 ASIN 先定位到最小类目，拉 Top 产品，再按形态聚类。</li>
              <li>关键词和相似竞品只在单品研究里辅助分析，不再作为主页面的一层机会池。</li>
              <li>重点看类目评论墙、头部品牌占比、形态评论中位数、低评高销数量、CN/HK 卖家占比、FBA 占比和变体数量。</li>
              <li>例如马桶刷类目整体评论墙很重，即使某个单品销量好，也会在这一层被拒绝。</li>
            </ul>
          </div>
          <div class="logic-block">
            <h3>3. 单品研究</h3>
            <ul>
              <li>只围绕已经通过形态验证、或你手动指定的 ASIN 展开。</li>
              <li>拆关键词、Top 产品形态、材质、套装、尺寸、配件、功能、闭合方式和视觉风格。</li>
              <li>图片识别和标题/详情材质判断合并，减少只靠标题造成的误判。</li>
              <li>评论分析直接放到每个产品形态下面，输出消费者在意点、不满意点和可执行切入方向。</li>
            </ul>
          </div>
        </div>
      </section>

      <section id="weekly-scan">
        <div class="section-head">
          <div>
            <h2>每周类目扫描</h2>
            <p>周更不再只靠关键词或单个爆品触发，而是每周扫描约 {weekly_category_target} 个最小类目，每个类目取约 {weekly_products_per_category} 个 Top 产品，先找候选，再做类目/形态验证。</p>
          </div>
        </div>
        <div class="next-grid">
          <div class="next-block">
            <h3>扫描对象</h3>
            <ul>
              <li>优先 Home、Kitchen、Storage、Garden、Office、Tools、Craft 等适合个人卖家的小类目。</li>
              <li>跳过成人、食品/保健、强合规、电子核心件、大件、易破损、强品牌护城河类目。</li>
              <li>每周生成新种子，但历史机会池不会被覆盖。</li>
            </ul>
          </div>
          <div class="next-block">
            <h3>入池规则</h3>
            <ul>
              <li>种子只是入口，必须通过最小类目 Top 验证。</li>
              <li>只把种子对应形态通过的结果放进机会池。</li>
              <li>相邻形态只作为证据和后续灵感，不单独算机会。</li>
            </ul>
          </div>
          <div class="next-block">
            <h3>后续研究</h3>
            <ul>
              <li>你从机会池选择产品后，进入单品深度研究。</li>
              <li>深度研究继续按形态、材质、颜色、套装、尺寸、配件、功能、图片和评论痛点拆解。</li>
              <li>研究结果单独存档，下一次可以直接打开。</li>
            </ul>
          </div>
        </div>
      </section>

      <section id="next">
        <div class="section-head">
          <div>
            <h2>第二个项目可以做什么</h2>
            <p>针对已经通过类目/形态验证的机会，下一步从“能不能卖”转成“做什么规格更有胜算”。</p>
          </div>
        </div>
        <div class="next-grid">
          <div class="next-block">
            <h3>用户痛点验证</h3>
            <ul id="painPointList"></ul>
          </div>
          <div class="next-block">
            <h3>切入方向</h3>
            <ul id="recommendationList"></ul>
          </div>
          <div class="next-block">
            <h3>执行顺序</h3>
            <ul id="nextStepList"></ul>
          </div>
        </div>
      </section>
    </main>
  </div>

  <script>
    const DATA = {data_json};

    const fmtMoney = (value) => {{
      const number = Number(value || 0);
      return number ? "$" + number.toLocaleString(undefined, {{ maximumFractionDigits: 2 }}) : "-";
    }};

    const fmtInt = (value) => {{
      const number = Number(value || 0);
      return number ? number.toLocaleString(undefined, {{ maximumFractionDigits: 0 }}) : "-";
    }};

    const fmtOne = (value) => {{
      const number = Number(value || 0);
      return number ? number.toLocaleString(undefined, {{ maximumFractionDigits: 1 }}) : "-";
    }};

    const fmtPct = (value) => {{
      const number = Number(value || 0);
      return number ? number.toLocaleString(undefined, {{ maximumFractionDigits: 1 }}) + "%" : "-";
    }};

    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({{
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;"
    }}[char]));

    const pillClass = (text) => {{
      const value = String(text || "").toLowerCase();
      if (value.includes("supplier") || value.includes("direct") || value.includes("validation")) return "green";
      if (value.includes("watch") || value.includes("keyword")) return "orange";
      if (value.includes("noise") || value.includes("risk") || value.includes("wall")) return "red";
      return "gray";
    }};

    const scoreBar = (score) => {{
      const width = Math.max(0, Math.min(100, Number(score || 0)));
      return `<div class="score"><b>${{fmtOne(score)}}</b><div class="bar"><span style="--w:${{width}}%"></span></div></div>`;
    }};

    const researchHref = (path) => {{
      const text = String(path || "");
      if (!text) return "";
      if (text.startsWith("web/")) return text.slice(4);
      return text;
    }};

    function renderSelection() {{
      const q = document.getElementById("selectionSearch").value.trim().toLowerCase();
      const strategy = document.getElementById("selectionStrategy").value;
      const recommendation = document.getElementById("selectionRecommendation").value;
      const limit = Number(document.getElementById("selectionLimit").value);
      const rows = DATA.selection
        .filter(row => !strategy || row.strategy === strategy)
        .filter(row => !recommendation || row.recommendation === recommendation)
        .filter(row => {{
          if (!q) return true;
          return [row.asin, row.title, row.category, row.flags, row.strategy].join(" ").toLowerCase().includes(q);
        }})
        .slice(0, limit);

      document.getElementById("selectionBody").innerHTML = rows.map(row => `
        <tr>
          <td class="num">${{row.rank}}</td>
          <td class="title-cell">
            <a href="${{esc(row.listing_url)}}" target="_blank" rel="noreferrer">${{esc(row.title_short)}}</a>
            <span class="muted-small">${{esc(row.asin)}} · ${{esc(row.category)}}</span>
          </td>
          <td><span class="pill gray">${{esc(row.strategy || "-")}}</span></td>
          <td class="num">${{scoreBar(row.score)}}</td>
          <td class="num"><span class="pill ${{row.evidence_confidence >= 60 ? "green" : row.evidence_confidence >= 40 ? "orange" : "gray"}}">${{esc(row.evidence_grade || "-")}} · ${{fmtOne(row.evidence_confidence)}}%</span></td>
          <td class="num">${{fmtMoney(row.price)}}</td>
          <td class="num">${{fmtMoney(row.unit_profit)}}</td>
          <td class="num">${{fmtPct(row.margin)}}</td>
          <td class="num">${{fmtMoney(row.monthly_profit)}}</td>
          <td class="num">${{fmtInt(row.monthly_sales)}}</td>
          <td><span class="pill ${{pillClass(row.flags)}}">${{esc(row.flags || "待复核")}}</span><span class="muted-small">${{esc(row.profit_estimate_status || "")}}</span></td>
        </tr>
      `).join("") || `<tr><td colspan="11"><div class="empty">没有匹配结果</div></td></tr>`;
    }}

    function renderCurrentCategoryScan() {{
      const rows = DATA.currentCategoryScan || [];
      document.getElementById("currentCategoryScanBody").innerHTML = rows.map(row => `
        <tr>
          <td class="title-cell">${{esc(row.name || "-")}}<span class="muted-small">${{esc(row.path || row.category_id || "-")}}${{row.scan_error ? " · " + esc(row.scan_error) : ""}}</span></td>
          <td><span class="pill ${{row.rotation_bucket === "never_scanned" ? "green" : "gray"}}">${{row.rotation_bucket === "never_scanned" ? "首次扫描" : row.rotation_bucket === "oldest_rescan" ? "最久未扫" : row.rotation_bucket === "manual_seed" ? "手动指定" : "历史基线"}}</span></td>
          <td class="num">${{fmtInt(row.products_examined)}}</td>
          <td class="num">${{fmtInt(row.candidate_count)}}${{row.scan_status === "failed" ? '<span class="pill red">失败</span>' : ''}}</td>
          <td>${{esc(row.previous_last_scanned_at || (row.rotation_bucket ? "从未扫描" : "迁移前已扫描"))}}</td>
        </tr>
      `).join("") || `<tr><td colspan="5"><div class="empty">还没有类目轮换记录；下次周更后自动生成。</div></td></tr>`;
    }}

    function renderKeywordSearchHistory() {{
      const q = String(document.getElementById("keywordSearchInput").value || "").trim().toLowerCase();
      const rows = (DATA.keywordSearchHistory || []).filter(row => !q || String(row.keyword || "").toLowerCase().includes(q));
      document.getElementById("keywordSearchHistoryBody").innerHTML = rows.map(row => `
        <tr>
          <td class="title-cell">${{esc(row.keyword || "-")}}<span class="muted-small">${{esc(row.top_title || row.top_asin || "尚无通过候选")}}</span></td>
          <td>${{esc(row.searched_at || "-")}}</td>
          <td class="num">${{fmtInt(row.raw_result_count)}}</td>
          <td class="num">${{fmtInt(row.eligible_candidate_count)}}</td>
          <td class="num">${{fmtInt(row.go_count)}} / ${{fmtInt(row.watch_count)}}</td>
          <td class="num">${{fmtOne(row.top_score)}}</td>
          <td><a class="pill green" href="${{esc(row.report_md)}}" target="_blank" rel="noreferrer">报告</a> <a class="pill gray" href="${{esc(row.ranked_csv)}}" target="_blank" rel="noreferrer">数据</a></td>
        </tr>
      `).join("") || `<tr><td colspan="7"><div class="empty">还没有匹配的关键词搜索档案。</div></td></tr>`;
    }}

    function renderLatestKeywordResults() {{
      const rows = DATA.latestKeywordResults || [];
      document.getElementById("keywordResultBody").innerHTML = rows.slice(0, 30).map(row => `
        <tr>
          <td class="title-cell"><a href="${{esc(row.listing_url)}}" target="_blank" rel="noreferrer">${{esc(row.title_short || row.title || "-")}}</a><span class="muted-small">${{esc(row.asin || "-")}} · ${{esc(row.category || "-")}}</span></td>
          <td class="num">${{scoreBar(row.score)}}</td>
          <td><span class="pill ${{pillClass(row.recommendation)}}">${{esc(row.recommendation || "-")}}</span></td>
          <td class="num">${{fmtInt(row.monthly_sales)}}</td>
          <td class="num">${{fmtInt(row.reviews)}}</td>
          <td class="num">${{fmtMoney(row.price)}}</td>
          <td>${{esc(row.flags || "待复核")}}</td>
        </tr>
      `).join("") || `<tr><td colspan="7"><div class="empty">还没有关键词评分结果。输入关键词后生成搜索命令。</div></td></tr>`;
    }}

    function showKeywordSearchCommand() {{
      const keyword = String(document.getElementById("keywordSearchInput").value || "").trim();
      const value = keyword || "KEYWORD";
      const command = `python3 keyword_opportunity_search.py --keyword ${{JSON.stringify(value)}}`;
      const hint = document.getElementById("keywordSearchHint");
      hint.style.display = "block";
      hint.textContent = `关键词搜索命令：${{command}}。运行后会自动归档结果并刷新本网页。你也可以直接把关键词发给 Codex，由 Codex 执行。`;
    }}

    function validationGroups() {{
      const grouped = new Map();
      (DATA.shapeValidation || []).forEach(row => {{
        const key = row.seed_asin || row.seed_title || row.category_path || String(row.rank || "");
        if (!grouped.has(key)) grouped.set(key, []);
        grouped.get(key).push(row);
      }});
      const verdictWeight = (text) => {{
        if (text === "Shape opportunity") return 0;
        if (text === "Watch shape") return 1;
        if (text === "Needs category Top100") return 2;
        return 3;
      }};
      return Array.from(grouped.values()).map(rows => {{
        const primary = rows.find(row => row.shape_scope === "seed_form")
          || rows.find(row => row.shape_recommendation === "Shape opportunity")
          || rows[0];
        const evidence = rows
          .filter(row => row !== primary && row.product_form && row.product_form !== "unknown")
          .sort((a, b) => verdictWeight(a.shape_recommendation) - verdictWeight(b.shape_recommendation) || Number(b.shape_score || 0) - Number(a.shape_score || 0));
        return {{ primary, evidence, rows }};
      }}).sort((a, b) => verdictWeight(a.primary.shape_recommendation) - verdictWeight(b.primary.shape_recommendation) || Number(b.primary.shape_score || 0) - Number(a.primary.shape_score || 0));
    }}

    function validationAction(row) {{
      if (!row.research_page) return "";
      const href = esc(researchHref(row.research_page));
      const rec = row.shape_recommendation || "";
      if (rec === "Shape opportunity") {{
        return `<a class="pill green" href="${{href}}" target="_blank" rel="noreferrer">进入深度研究</a>`;
      }}
      if (rec === "Watch shape") {{
        return `<a class="pill orange" href="${{href}}" target="_blank" rel="noreferrer">查看观察资料</a>`;
      }}
      if (rec === "Needs category Top100") {{
        return "";
      }}
      return `<a class="pill gray" href="${{href}}" target="_blank" rel="noreferrer">查看淘汰依据</a>`;
    }}

    function renderShapeValidation() {{
      const q = document.getElementById("shapeSearch").value.trim().toLowerCase();
      const recommendation = document.getElementById("shapeRecommendation").value;
      const groups = validationGroups()
        .filter(group => !recommendation || group.primary.shape_recommendation === recommendation)
        .filter(group => {{
          if (!q) return true;
          return group.rows.some(row => [row.seed_asin, row.seed_title, row.product_form, row.category_path, row.validation_flags, row.opportunity_thesis, row.next_action].join(" ").toLowerCase().includes(q));
        }});

      document.getElementById("shapeValidationList").innerHTML = groups.map(group => {{
        const row = group.primary;
        const evidenceRows = group.evidence.slice(0, 6).map(item => `
          <div class="evidence-row">
            <div>
              <div class="evidence-form">${{esc(item.product_form || "-")}}</div>
              <span class="muted-small">${{item.shape_scope === "seed_form" ? "候选形态" : "Top100 相邻形态"}} · ${{esc(item.validation_flags || item.next_action || "仅作类目参考")}}</span>
            </div>
            <div><span class="shape-cell-label">结论</span><span class="pill ${{pillClass(item.shape_recommendation)}}">${{esc(item.shape_recommendation || "-")}}</span></div>
            <div><span class="shape-cell-label">形态分</span><strong>${{fmtOne(item.shape_score)}}</strong></div>
            <div><span class="shape-cell-label">月销</span><strong>${{fmtInt(item.form_avg_sales)}}</strong></div>
            <div><span class="shape-cell-label">评论中位</span><strong>${{fmtInt(item.form_median_reviews)}}</strong></div>
          </div>
        `).join("");
        return `
          <article class="validation-card">
            <div class="validation-head">
              <div class="validation-title">
                <a href="${{esc(row.seed_listing_url)}}" target="_blank" rel="noreferrer">${{esc(row.product_form || row.seed_title_short || "-")}}</a>
                <span class="muted-small">${{esc(row.seed_asin)}} · ${{esc(row.seed_title_short || "-")}}</span>
                <span class="muted-small">${{esc(row.category_path || "-")}}</span>
                <div class="validation-status">
                  <span class="pill ${{pillClass(row.shape_recommendation)}}">${{esc(row.shape_recommendation || "-")}}</span>
                  <span class="pill gray">${{esc(row.data_quality || "-")}}</span>
                  <span class="pill gray">候选形态</span>
                </div>
              </div>
              <div class="validation-metrics">
                <div class="validation-metric">
                  <span class="shape-cell-label">形态分</span>
                  ${{scoreBar(row.shape_score)}}
                </div>
                <div class="validation-metric"><span class="shape-cell-label">月销</span><strong>${{fmtInt(row.form_avg_sales)}}</strong></div>
                <div class="validation-metric"><span class="shape-cell-label">评论中位</span><strong>${{fmtInt(row.form_median_reviews)}}</strong></div>
                <div class="validation-metric"><span class="shape-cell-label">低评高销</span><strong>${{fmtInt(row.form_low_review_high_sales_count)}}</strong></div>
                <div class="validation-metric">
                  <span class="shape-cell-label">类目结构</span>
                  <span class="shape-note">Top品牌 ${{esc(row.category_top_brand || "-")}} / ${{fmtPct(row.category_top_brand_share)}}；Top10评论中位 ${{fmtInt(row.category_top10_median_reviews)}}</span>
                </div>
              </div>
            </div>
            <div class="validation-body">
              <div>
                <p class="validation-note">${{esc(row.opportunity_thesis || "等待类目验证结论。")}}</p>
                <p class="validation-note" style="margin-top: 8px;">${{esc(row.validation_flags || "无明显硬伤")}}；${{esc(row.next_action || "-")}}</p>
                <div class="pool-actions">
                  ${{validationAction(row)}}
                  <a class="pill gray" href="${{esc(row.seed_listing_url)}}" target="_blank" rel="noreferrer">Listing</a>
                </div>
              </div>
              <div class="evidence-box">
                <h4>Top100 形态拆分证据（不单独进入机会池）</h4>
                ${{evidenceRows || `<div class="empty">还没有相邻形态证据；当前只验证了候选形态。</div>`}}
              </div>
            </div>
          </article>
        `;
      }}).join("") || `<div class="empty">还没有候选产品验证数据。</div>`;
    }}

    function renderArchive() {{
      const q = document.getElementById("archiveSearch").value.trim().toLowerCase();
      const status = document.getElementById("archiveStatus").value;
      const rows = (DATA.archive || [])
        .filter(row => !status || row.archive_status === status)
        .filter(row => {{
          if (!q) return true;
          return [row.seed_asin, row.seed_title, row.product_form, row.category_path, row.opportunity_thesis, row.research_status].join(" ").toLowerCase().includes(q);
        }});

      const grouped = new Map();
      rows.forEach(row => {{
        const key = row.category_path || "未识别类目";
        if (!grouped.has(key)) grouped.set(key, []);
        grouped.get(key).push(row);
      }});

      const html = Array.from(grouped.entries()).map(([category, groupRows]) => {{
        groupRows.sort((a, b) => Number(b.archive_best_score || b.shape_score || 0) - Number(a.archive_best_score || a.shape_score || 0));
        const activeCount = groupRows.filter(row => row.archive_status === "active_in_latest_run").length;
        return `
          <article class="category-group">
            <div class="category-group-head">
              <div>
                <h3>${{esc(category)}}</h3>
                <span class="muted-small">${{fmtInt(groupRows.length)}} 个机会，${{fmtInt(activeCount)}} 个本轮仍出现</span>
              </div>
              <div class="validation-metrics">
                <div class="validation-metric"><span class="shape-cell-label">最高分</span><strong>${{fmtOne(Math.max(...groupRows.map(row => Number(row.archive_best_score || row.shape_score || 0))))}}</strong></div>
                <div class="validation-metric"><span class="shape-cell-label">最高月销</span><strong>${{fmtInt(Math.max(...groupRows.map(row => Number(row.form_avg_sales || 0))))}}</strong></div>
                <div class="validation-metric"><span class="shape-cell-label">最低评论中位</span><strong>${{fmtInt(Math.min(...groupRows.map(row => Number(row.form_median_reviews || 0)).filter(Boolean)))}}</strong></div>
                <div class="validation-metric"><span class="shape-cell-label">低评高销</span><strong>${{fmtInt(groupRows.reduce((sum, row) => sum + Number(row.form_low_review_high_sales_count || 0), 0))}}</strong></div>
              </div>
            </div>
            <div class="pool-list">
              ${{groupRows.map(row => `
                <div class="pool-item">
                  <div class="pool-item-title">
                    <a href="${{esc(row.seed_listing_url)}}" target="_blank" rel="noreferrer">${{esc(row.product_form || "-")}}</a>
                    <span class="muted-small">${{esc(row.seed_asin)}} · ${{esc(row.seed_title_short || "-")}}</span>
                    <div class="pool-actions">
                      <span class="pill ${{row.archive_status === "active_in_latest_run" ? "green" : "gray"}}">${{row.archive_status === "active_in_latest_run" ? "本轮仍出现" : "历史机会"}}</span>
                      ${{row.research_page ? `<a class="pill green" href="${{esc(researchHref(row.research_page))}}" target="_blank" rel="noreferrer">单品研究</a>` : ""}}
                      <a class="pill gray" href="${{esc(row.seed_listing_url)}}" target="_blank" rel="noreferrer">Listing</a>
                    </div>
                  </div>
                  <div><span class="shape-cell-label">历史最高</span><strong>${{fmtOne(row.archive_best_score)}}</strong></div>
                  <div><span class="shape-cell-label">本轮分</span><strong>${{fmtOne(row.archive_latest_score)}}</strong></div>
                  <div><span class="shape-cell-label">月销</span><strong>${{fmtInt(row.form_avg_sales)}}</strong></div>
                  <div><span class="shape-cell-label">评论中位</span><strong>${{fmtInt(row.form_median_reviews)}}</strong></div>
                  <div>
                    <span class="shape-cell-label">机会依据</span>
                    <span class="shape-note">${{esc(row.opportunity_thesis || "-")}}</span>
                    <span class="muted-small">首次 ${{esc(row.archive_first_seen || "-")}} · 最近 ${{esc(row.archive_last_seen || "-")}} · 出现 ${{fmtInt(row.archive_seen_count)}} 次 · ${{esc(row.research_status || "-")}}</span>
                  </div>
                </div>
              `).join("")}}
            </div>
          </article>
        `;
      }}).join("");

      document.getElementById("archiveBody").innerHTML = html || `<div class="empty">机会池还没有匹配结果。只有通过候选产品验证的机会才会进入这里。</div>`;
    }}

    function renderProductResearchArchive() {{
      const rows = DATA.productResearchArchive || [];
      document.getElementById("productResearchArchiveBody").innerHTML = rows.map(row => `
        <tr>
          <td class="title-cell">
            ${{row.web_page_available ? `<a href="${{esc(row.web_page)}}" target="_blank" rel="noreferrer">${{esc(row.title_short || row.asin)}}</a>` : `<strong>${{esc(row.title_short || row.asin)}}</strong>`}}
            <span class="muted-small">${{esc(row.asin)}} · 首次 ${{esc(row.first_researched || "-")}} · 研究 ${{fmtInt(row.research_count)}} 次</span>
          </td>
          <td>${{esc(row.top_form || "-")}}</td>
          <td><span class="pill ${{row.decision === "可继续" ? "green" : row.decision === "待复核" ? "orange" : "gray"}}">${{esc(row.decision)}}</span><span class="muted-small">${{esc(row.decision_reason)}}</span></td>
          <td class="num">${{fmtInt(row.products_count)}}</td>
          <td class="num">${{fmtInt(row.forms_count)}}</td>
          <td class="num">${{fmtInt(row.reviews_count)}}</td>
          <td>${{esc(row.last_researched || "-")}}</td>
          <td>
            ${{row.web_page_available ? `<a class="pill green" href="${{esc(row.web_page)}}" target="_blank" rel="noreferrer">研究页</a>` : `<span class="pill gray">页面未生成</span>`}}
            ${{row.report_available ? `<a class="pill gray" href="${{esc(row.report_path)}}" target="_blank" rel="noreferrer">报告</a>` : ""}}
          </td>
        </tr>
      `).join("") || `<tr><td colspan="8"><div class="empty">还没有单品研究档案。用上方输入 ASIN 后生成研究命令。</div></td></tr>`;
    }}

    function findResearchByAsin(asin) {{
      const target = String(asin || "").trim().toUpperCase();
      return (DATA.productResearchArchive || []).find(row => String(row.asin || "").toUpperCase() === target);
    }}

    function showResearchHint(text) {{
      const node = document.getElementById("productResearchHint");
      node.style.display = "block";
      node.textContent = text;
    }}

    function openExistingResearch() {{
      const asin = document.getElementById("productResearchAsin").value;
      const row = findResearchByAsin(asin);
      if (row && row.web_page && row.web_page_available) {{
        window.open(row.web_page, "_blank", "noreferrer");
        return;
      }}
      showResearchHint(`还没有找到 ${{String(asin || "").trim().toUpperCase() || "这个 ASIN"}} 的研究档案。需要先运行：python3 refresh_product_research.py --asin ${{String(asin || "").trim().toUpperCase() || "ASIN"}}`);
    }}

    function showResearchCommand() {{
      const asin = String(document.getElementById("productResearchAsin").value || "").trim().toUpperCase();
      showResearchHint(`新增或刷新单品研究：python3 refresh_product_research.py --asin ${{asin || "ASIN"}}。这个命令会拉 Sorftime、写入研究档案，并生成独立研究页。`);
    }}

    function renderForms() {{
      document.getElementById("formsBody").innerHTML = DATA.forms.map(row => `
        <tr>
          <td class="title-cell">${{esc(row.form)}}</td>
          <td class="num">${{fmtInt(row.count)}}</td>
          <td class="num">${{fmtInt(row.direct_count)}} / ${{fmtInt(row.keyword_count)}}</td>
          <td class="num">${{fmtMoney(row.avg_price)}}</td>
          <td class="num">${{fmtInt(row.avg_monthly_sales)}}</td>
          <td class="num">${{fmtInt(row.median_reviews)}}</td>
          <td>
            ${{esc(row.materials || "-")}}
            <span class="muted-small">${{esc(row.pack_counts || "")}}</span>
          </td>
          <td>${{esc(row.note || "-")}}</td>
        </tr>
      `).join("");
    }}

    function renderVisualCounts() {{
      const groups = [
        ["视觉形态", DATA.visualCounts.forms],
        ["材质信号", DATA.visualCounts.materials],
        ["套装数量", DATA.visualCounts.packs],
        ["闭合方式", DATA.visualCounts.closures],
        ["视觉风格", DATA.visualCounts.styles],
      ];
      document.getElementById("visualCounts").innerHTML = groups.map(([title, rows]) => `
        <div class="count-box">
          <h3>${{esc(title)}}</h3>
          ${{rows.map(row => `
            <div class="count-row"><span>${{esc(row.name)}}</span><strong>${{fmtInt(row.count)}}</strong></div>
          `).join("") || `<div class="muted-small">暂无数据</div>`}}
        </div>
      `).join("");
    }}

    function renderResearchKeywords() {{
      document.getElementById("researchKeywordBody").innerHTML = DATA.researchKeywords.slice(0, 12).map(row => `
        <tr>
          <td class="title-cell">${{esc(row.keyword)}}</td>
          <td class="num">${{fmtInt(row.search_volume)}}</td>
          <td class="num">${{fmtInt(row.clicks_90d)}}</td>
          <td class="num">${{fmtPct(row.show_share)}}</td>
          <td>${{esc(row.top3_asins || "-")}}</td>
        </tr>
      `).join("");
    }}

    function renderGallery() {{
      const form = document.getElementById("galleryForm").value;
      const q = document.getElementById("gallerySearch").value.trim().toLowerCase();
      const rows = DATA.topProducts
        .filter(row => !form || row.visual_product_form === form)
        .filter(row => {{
          if (!q) return true;
          return [
            row.asin,
            row.title,
            row.material,
            row.material_evidence,
            row.visual_product_form,
            row.visual_material_signal,
            row.visual_style,
            row.visual_notes
          ].join(" ").toLowerCase().includes(q);
        }})
        .slice(0, 40);

      document.getElementById("productGallery").innerHTML = rows.map(row => `
        <article class="product-tile">
          ${{row.image ? `<img src="${{esc(row.image)}}" alt="${{esc(row.asin)}}">` : `<div class="empty">无图</div>`}}
          <div class="tile-body">
            <a class="tile-title" href="${{esc(row.listing_url)}}" target="_blank" rel="noreferrer">${{esc(row.title_short)}}</a>
            <span class="muted-small">${{esc(row.asin)}} · ${{fmtMoney(row.price)}} · 月销 ${{fmtInt(row.monthly_sales)}} · 评论 ${{fmtInt(row.reviews)}}</span>
            <div class="tile-meta">
              <span class="pill green">${{esc(row.visual_product_form || row.product_form || "-")}}</span>
              <span class="pill gray">${{esc(row.material || row.visual_material_signal || "-")}}</span>
              <span class="pill orange">${{esc(row.visual_pack_count || row.pack_count || "-")}} pack</span>
              <span class="pill gray">${{esc(row.visual_closure || row.closure || "-")}}</span>
            </div>
            <span class="muted-small">${{esc(row.material_evidence || row.detail_evidence || row.visual_notes || "")}}</span>
          </div>
        </article>
      `).join("") || `<div class="empty">没有匹配图片</div>`;
    }}

    function renderBullets(id, items, fallback) {{
      const list = document.getElementById(id);
      const rows = items && items.length ? items : fallback;
      list.innerHTML = rows.map(item => `<li>${{esc(item)}}</li>`).join("");
    }}

    function bind(id, event, handler) {{
      const node = document.getElementById(id);
      if (node) node.addEventListener(event, handler);
    }}

    renderSelection();
    renderCurrentCategoryScan();
    renderKeywordSearchHistory();
    renderLatestKeywordResults();
    renderShapeValidation();
    renderArchive();
    renderProductResearchArchive();
    renderForms();
    renderVisualCounts();
    renderResearchKeywords();
    renderGallery();
    renderBullets("painPointList", DATA.painPoints, [
      "蜂蜡气味和清洗说明需要验证",
      "尺寸是否适配圆形 sourdough 和高吐司",
      "拉链耐用性、面包屑外漏",
      "保鲜平衡：透气 vs 面包变干"
    ]);
    renderBullets("recommendationList", DATA.recommendations, [
      "不要只做普通 2-pack，优先验证 2-size combo、3-pack 或 gift set",
      "供应商必须验证蜂蜡涂层、棉麻比例、可清洗边界和食品接触合规",
      "用图片队列继续聚类套装数量、闭合方式、自然/farmhouse 风格和礼品化"
    ]);
    renderBullets("nextStepList", DATA.nextSteps, [
      "补齐 Top ASIN 评论明细",
      "找 3-5 个供应商验证规格和成本",
      "根据评论痛点反推产品规格，再进入素材和 listing 自动化"
    ]);

    ["selectionSearch", "selectionStrategy", "selectionRecommendation", "selectionLimit"].forEach(id => {{
      bind(id, "input", renderSelection);
      bind(id, "change", renderSelection);
    }});
    bind("keywordSearchInput", "input", renderKeywordSearchHistory);
    bind("keywordSearchCommand", "click", showKeywordSearchCommand);
    ["shapeSearch", "shapeRecommendation"].forEach(id => {{
      bind(id, "input", renderShapeValidation);
      bind(id, "change", renderShapeValidation);
    }});
    ["archiveSearch", "archiveStatus"].forEach(id => {{
      bind(id, "input", renderArchive);
      bind(id, "change", renderArchive);
    }});
    bind("productResearchOpen", "click", openExistingResearch);
    bind("productResearchCommand", "click", showResearchCommand);
    ["galleryForm", "gallerySearch"].forEach(id => {{
      bind(id, "input", renderGallery);
      bind(id, "change", renderGallery);
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build_html(), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
