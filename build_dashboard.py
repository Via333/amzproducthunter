#!/usr/bin/env python3
"""Build a static dashboard for the Amazon selection workflow."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

from product_risk import has_valid_category, infer_compliance_risk, infer_fragile_risk, infer_oversize_risk
from dashboard_catalog import build_catalog
from research_workbench import opportunity_keys
from product_market_validation import build_current_product_groups
from opportunity_feedback import feedback_state


from workflow_storage import atomic_write_text, current_root, run_quality, load_rules
import os
ROOT = Path(os.environ["AMZ_WORKFLOW_ROOT"]) if os.environ.get("AMZ_WORKFLOW_ROOT") else current_root(Path(__file__).resolve().parent)
WEB_DIR = ROOT / "web"
OUTPUT = WEB_DIR / "index.html"
SOURCE_ROOT = Path(__file__).resolve().parent


def data_path(relative_path):
    path = Path(relative_path)
    if path.is_absolute():
        return path
    manual = str(path).startswith(("research/", "archive/keyword_search_runs/", "reports/product_opportunity_research_")) or str(path) in {
        "archive/product_research_index.csv", "archive/category_research_index.csv", "archive/keyword_search_index.csv"}
    if manual and not os.environ.get("AMZ_WORKFLOW_ROOT") and (SOURCE_ROOT / path).exists():
        return SOURCE_ROOT / path
    return ROOT / path


def read_csv(relative_path: str) -> list[dict[str, str]]:
    path = data_path(relative_path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_csv_reference(path_text: str) -> list[dict[str, str]]:
    if not path_text:
        return []
    path = data_path(path_text)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_text(relative_path: str) -> str:
    path = data_path(relative_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def read_json(relative_path: str) -> dict:
    path = data_path(relative_path)
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
    try:
        return path.relative_to("web").as_posix()
    except ValueError:
        pass
    return f"../{path.as_posix()}"


def dashboard_product_image(row: dict[str, str]) -> str:
    image_file = str(row.get("image_file", "") or "").strip()
    if not image_file:
        return ""
    if image_file.startswith(("https://", "http://")):
        return image_file

    asin = str(row.get("asin", "") or "").strip()
    if not asin:
        return ""
    published = WEB_DIR / "research" / "assets" / asin / Path(image_file).name
    if not published.is_file():
        return ""
    return published.relative_to(WEB_DIR).as_posix()


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
                "source_category_id": row.get("source_category_id", ""),
                "source_category_name": row.get("source_category_name", ""),
                "source_category_path": row.get("source_category_path", ""),
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
    normalized = [
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
            "category_health_score": to_float(row.get("category_health_score")),
            "category_health_rank": to_float(row.get("category_health_rank")),
            "category_health_flags": row.get("category_health_flags", ""),
            "category_sample_count": to_float(row.get("category_sample_count")),
            "category_total_sales": to_float(row.get("category_total_sales")),
            "category_median_reviews": to_float(row.get("category_median_reviews")),
            "category_top10_median_reviews": to_float(row.get("category_top10_median_reviews")),
            "category_top_brand": row.get("category_top_brand", ""),
            "category_top_brand_share": as_percent(row.get("category_top_brand_share")),
            "category_low_review_high_sales_count": to_float(row.get("category_low_review_high_sales_count")),
            "category_new_entrant_count": to_float(row.get("category_new_entrant_count")),
            "category_new_entrant_sales": to_float(row.get("category_new_entrant_sales")),
        }
        for row in rows
    ]
    normalized.sort(
        key=lambda row: (
            row["scan_status"] != "success",
            row["category_health_rank"] or 999999,
            -row["category_health_score"],
            row["path"],
        )
    )
    return normalized


def normalize_exclusion_rules(discovery_cfg: dict, exclusion_cfg: dict) -> list[dict[str, str]]:
    rows = []
    seen = set()
    reasons = discovery_cfg.get("category_filters", {}).get("exclusion_reasons", {})
    for term in discovery_cfg.get("category_filters", {}).get("exclude_name_contains", []):
        key = str(term).strip().lower()
        if key and key not in seen:
            rows.append({"term": str(term), "type": "基础排除", "reason": str(reasons.get(str(term)) or f"类目预筛：路径包含 {term}")})
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
                "archive_notes": row.get("archive_notes", ""),
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
                "decision_keys": opportunity_keys(row),
                "rank": index,
                "seed_rank": to_float(row.get("seed_rank")) or index,
                "seed_asin": row.get("seed_asin", ""),
                "seed_title": row.get("seed_title", ""),
                "seed_title_short": short_title(row.get("seed_title", ""), 95),
                "seed_listing_url": normalize_url(row, "seed_listing_url"),
                "seed_score": to_float(row.get("seed_score")),
                "source_category_id": row.get("source_category_id", ""),
                "source_category_name": row.get("source_category_name", ""),
                "validation_run_id": row.get("validation_run_id", ""),
                "category_path": row.get("category_path", ""),
                "category_health_score": to_float(row.get("category_health_score")),
                "category_health_rank": to_float(row.get("category_health_rank")),
                "data_quality": row.get("data_quality", ""),
                "product_form": row.get("product_form", ""),
                "product_family": row.get("product_family", ""),
                "product_family_label": row.get("product_family_label", ""),
                "product_form_label": row.get("product_form_label", ""),
                "classification_status": row.get("classification_status", ""),
                "form_landed_cost_ceiling_median": row.get("form_landed_cost_ceiling_median", ""),
                "form_fee_coverage": row.get("form_fee_coverage", 0),
                "form_fee_source": row.get("form_fee_source", "missing"),
                "assessment_scope": row.get("assessment_scope", ""),
                **{key: row.get(key) for key in (
                    "market_id", "market_label", "source_categories", "family_sources",
                    "snapshot_id", "family_duplicate_count", "family_conflict_count", "comparison_status",
                )},
                "family_sample_count": to_float(row.get("family_sample_count"), 0),
                "shape_scope": row.get("shape_scope", ""),
                "shape_score": to_float(row.get("shape_score")) if row.get("shape_score") not in ("", None) else None,
                "shape_recommendation": row.get("shape_recommendation", ""),
                "category_sample_count": to_float(row.get("category_sample_count")),
                "category_total_sales": to_float(row.get("category_total_sales")),
                "category_median_reviews": to_float(row.get("category_median_reviews")),
                "category_top10_median_reviews": to_float(row.get("category_top10_median_reviews")),
                "category_top_brand": row.get("category_top_brand", ""),
                "category_top_brand_share": as_percent(row.get("category_top_brand_share")),
                "form_count": to_float(row.get("form_count")),
                "form_avg_price": to_float(row.get("form_avg_price")),
                "form_price_median": to_float(row.get("form_price_median")),
                "form_avg_sales": to_float(row.get("form_avg_sales")),
                "form_median_reviews": to_float(row.get("form_median_reviews")),
                "form_low_review_high_sales_count": to_float(row.get("form_low_review_high_sales_count")),
                "form_top_materials": row.get("form_top_materials", ""),
                "form_top_packs": row.get("form_top_packs", ""),
                "form_excluded_share": as_percent(row.get("form_excluded_share")),
                "form_excluded_reasons": row.get("form_excluded_reasons", ""),
                "form_new_entrant_count": to_float(row.get("form_new_entrant_count")),
                "form_new_entrant_success_count": to_float(row.get("form_new_entrant_success_count")),
                "form_new_entrant_success_rate": as_percent(row.get("form_new_entrant_success_rate")),
                "form_new_entrant_median_sales": to_float(row.get("form_new_entrant_median_sales")),
                "form_brand_dependent_share": as_percent(row.get("form_brand_dependent_share")),
                "reference_asins": row.get("form_reference_asins", ""),
                "validation_flags": row.get("validation_flags", ""),
                "opportunity_thesis": row.get("opportunity_thesis", ""),
                "next_action": row.get("next_action", ""),
                "research_page": row.get("research_page", ""),
            }
        )
    normalized.sort(
        key=lambda row: (
            row["category_health_rank"] or 999999,
            -to_float(row["shape_score"]),
            row["product_form"],
        )
    )
    return normalized


def primary_shape_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        key = str(row.get("category_path") or row.get("source_category_id") or row.get("seed_asin") or row.get("rank"))
        grouped[key].append(row)

    primary_rows: list[dict[str, object]] = []
    for group_rows in grouped.values():
        verdict_order = {
            "Shape opportunity": 0,
            "Watch shape": 1,
            "Needs category Top100": 2,
            "Needs classification": 2,
            "Reject category/form": 3,
        }
        primary_rows.append(
            min(
                group_rows,
                key=lambda row: (
                    verdict_order.get(str(row.get("shape_recommendation")), 9),
                    -to_float(row.get("shape_score")),
                ),
            )
        )

    primary_rows.sort(
        key=lambda row: (
            to_float(row.get("category_health_rank"), 999999) or 999999,
            -to_float(row.get("shape_score")),
        )
    )
    return primary_rows


def normalize_shape_archive(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        normalized.append(
            {
                "decision_keys": opportunity_keys(row),
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
                "archive_notes": row.get("archive_notes", ""),
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
        elif row.get("status") == "profitability_unverified":
            decision, reason = "利润未验证", row.get("notes") or "缺少 1688 同款同规格 SKU 中位采购价"
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
        report_path = f"research/reports/{asin}.md"
        report_available = (WEB_DIR / report_path).is_file()
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
    normalized.sort(key=lambda row: str(row.get("last_researched", "")), reverse=True)
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
                "image": dashboard_product_image(row),
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


GENERIC_REVIEW_TAG_LABELS = {
    "magnet_strength": "磁力/吸附稳定性",
    "size_capacity": "尺寸/容量",
    "durability": "耐用性",
    "price_value": "价格/价值",
    "surface_protection": "表面防护",
    "pickup_tool": "磁吸拾取杆",
    "fit": "适配性",
    "ease_of_use": "易用性",
    "material_quality": "材质质量",
    "performance": "功能表现",
}


def translated_review_counts(counts: Counter, limit: int = 5) -> str:
    if not counts:
        return "-"
    return "；".join(
        f"{GENERIC_REVIEW_TAG_LABELS.get(name, name)} {count}"
        for name, count in counts.most_common(limit)
    )


def build_generic_product_insights(
    relevant: list[dict[str, object]],
    seed: dict[str, object],
    form_rows: list[dict[str, object]],
    review_rows: list[dict[str, str]],
    review_target_rows: list[dict[str, str]],
    business: dict[str, object],
) -> dict[str, list[dict[str, object]] | list[str]]:
    seed_form = str(seed.get("product_form") or "当前形态")
    target_rows = [row for row in relevant if row.get("product_form") == seed_form] or relevant
    target_form = main_form_row(form_rows, seed_form)
    direct_rows = [row for row in target_rows if row.get("competitor_type") in {"seed", "direct"}]
    adjacent_rows = [row for row in relevant if row.get("product_form") != seed_form]
    target_asins = {
        str(row.get("asin") or "")
        for row in review_target_rows
        if row.get("product_form") == seed_form and row.get("asin")
    }
    target_reviews = [
        row for row in review_rows
        if not target_asins or str(row.get("review_target_asin") or row.get("asin") or "") in target_asins
    ]
    positive_reviews = [row for row in target_reviews if to_float(row.get("rating")) >= 4]
    negative_reviews = [row for row in target_reviews if 0 < to_float(row.get("rating")) <= 3]
    pain_counts: Counter = Counter()
    negative_pain_counts: Counter = Counter()
    for row in target_reviews:
        tags = [tag.strip() for tag in str(row.get("pain_point_tags") or "").split(";") if tag.strip()]
        pain_counts.update(tags)
        if 0 < to_float(row.get("rating")) <= 3:
            negative_pain_counts.update(tags)

    material_counts = count_values(target_rows, "material") + count_values(target_rows, "visual_material_signal")
    pack_counts = count_values(target_rows, "pack_count") + count_values(target_rows, "visual_pack_count")
    feature_counts = count_values(target_rows, "feature_tags", split=True)
    closure_counts = count_values(target_rows, "closure") + count_values(target_rows, "visual_closure")
    use_case_counts = count_values(target_rows, "use_case")
    style_counts = count_values(target_rows, "style") + count_values(target_rows, "visual_style")
    total_sales = sum(to_float(row.get("monthly_sales")) for row in direct_rows)
    ranked_sales = sorted((to_float(row.get("monthly_sales")) for row in direct_rows), reverse=True)
    top3_share = sum(ranked_sales[:3]) / total_sales if total_sales else 0
    review_counts = [to_float(row.get("reviews")) for row in direct_rows if to_float(row.get("reviews")) > 0]
    ratings = [to_float(row.get("rating")) for row in direct_rows if to_float(row.get("rating")) > 0]
    low_review_high_sales = int(to_float(target_form.get("low_review_high_sales_count")))
    review_source_note = (
        f"已覆盖 {len(target_asins)} 个 {seed_form} ASIN，共 {len(target_reviews)} 条评论正文。"
        if target_reviews
        else f"当前还没有 {seed_form} 的真实评论正文，只能根据供给结构做候选判断。"
    )
    concentration = "头部集中" if top3_share >= 0.6 else "集中度中等" if top3_share >= 0.4 else "供给较分散"

    dimensions = [
        {
            "title": "材质 / 结构",
            "headline": f"{seed_form} 的主流结构已可识别，但不能只复制外观",
            "evidence": [
                f"材质与视觉信号：{compact_counts(material_counts)}",
                f"功能卖点：{compact_counts(feature_counts)}",
                f"直接形态样本 {len(direct_rows)} 个。",
            ],
            "opportunity": "把主流材质、结构和功能做成可量化的样品对比，用实测证明差异，不把标题词当作产品壁垒。",
        },
        {
            "title": "规格 / 套装",
            "headline": "规格组合是可感知差异，必须按一个 Amazon 可售单元对齐",
            "evidence": [
                f"套装数量：{compact_counts(pack_counts)}",
                f"闭合/固定方式：{compact_counts(closure_counts)}",
                f"使用场景：{compact_counts(use_case_counts)}",
            ],
            "opportunity": "先明确目标尺寸、数量、配件和包装内容，再比较销量、评论和 1688 同规格成本。",
        },
        {
            "title": "需求 / 竞争",
            "headline": f"目标形态合计月销约 {total_sales:,.0f}，Top3 占 {top3_share:.1%}，{concentration}",
            "evidence": [
                f"月销中位数：{med([to_float(row.get('monthly_sales')) for row in direct_rows]):,.0f}",
                f"评论中位数：{med(review_counts):,.0f}；低评高销样本 {low_review_high_sales} 个",
                f"相邻形态 {len(adjacent_rows)} 个样本，不计入 {seed_form} 需求。",
            ],
            "opportunity": "有需求不等于可进入；继续看新品成功、头部集中、获客词和利润能否同时成立。",
        },
        {
            "title": "评论需求",
            "headline": f"已读取 {len(target_reviews)} 条目标形态评论，优先关注 {translated_review_counts(pain_counts, 3)}",
            "evidence": [
                f"高星评论 {len(positive_reviews)} 条；低星评论 {len(negative_reviews)} 条",
                f"全部需求/痛点标签：{translated_review_counts(pain_counts)}",
                f"低星痛点：{translated_review_counts(negative_pain_counts)}",
            ],
            "opportunity": "差异化必须对应反复出现的评论主题，并转成可测试的产品指标。",
        },
        {
            "title": "视觉 / 场景",
            "headline": "主图需要证明真实使用方式，不是只展示产品本体",
            "evidence": [
                f"视觉/场景风格：{compact_counts(style_counts)}",
                f"代表产品图片 {sum(1 for row in target_rows if row.get('image'))} 张",
                f"当前形态：{seed_form}",
            ],
            "opportunity": "用实际工位、收纳过程、前后对比和规格对照表表达功能，避免和同质主图混在一起。",
        },
    ]

    rating_summary = [
        {
            "title": "评分结构",
            "headline": f"直接形态平均评分约 {avg(ratings):.1f}，评论中位数 {med(review_counts):.0f}",
            "evidence": [
                f"直接样本 {len(direct_rows)} 个；真实评论 {len(target_reviews)} 条",
                f"低评高销样本 {low_review_high_sales} 个",
                review_source_note,
            ],
            "opportunity": "评分结构只用来定位验证重点，不能单独证明有机会。",
        },
        {
            "title": "用户反馈焦点",
            "headline": translated_review_counts(pain_counts, 4),
            "evidence": [
                f"高星样本 {len(positive_reviews)} 条",
                f"低星样本 {len(negative_reviews)} 条",
                f"低星标签：{translated_review_counts(negative_pain_counts)}",
            ],
            "opportunity": "把高频主题分成“必须达标”和“可做差异”两类，用样品对比验证。",
        },
    ]

    if business.get("profitability_verified"):
        commercial_headline = f"1688 同规格成本基准下，贡献利润率约 {to_float(business.get('contribution_margin')):.1%}"
        commercial_evidence = (
            f"有效 SKU 报价 {int(to_float(business.get('quote_sample_count')))} 条，"
            f"采购中位数 ¥{to_float(business.get('procurement_cost_cny')):.2f}。"
        )
        commercial_action = "继续补正式 MOQ、包装、头程和测试费，再将成本基准升级为可下单利润。"
    else:
        commercial_headline = "利润未验证，当前不能立项"
        commercial_evidence = (
            f"有效同规格 SKU 报价 {int(to_float(business.get('quote_sample_count')))} 条；"
            f"待核规格候选 {int(to_float(business.get('candidate_quote_count')))} 条。"
        )
        commercial_action = "核对到至少 3 条 1688 同款同规格 SKU 价后取中位数，再重算贡献利润率。"

    conclusions = [
        {
            "title": "当前判断",
            "headline": f"保留 {seed_form} 继续验证，不直接立项",
            "evidence": f"直接样本月销合计 {total_sales:,.0f}，Top3 占 {top3_share:.1%}，低评高销样本 {low_review_high_sales} 个。",
            "action": "需求、新品成功、用户痛点、利润和风险全部通过后，才能进入机会池。",
        },
        {
            "title": "产品切入",
            "headline": f"优先解决：{translated_review_counts(pain_counts, 3)}",
            "evidence": f"评论覆盖 {len(target_reviews)} 条，高星 {len(positive_reviews)} 条，低星 {len(negative_reviews)} 条。",
            "action": "把高频需求转成材质、规格、配件、耐用和使用场景的样品测试项。",
        },
        {
            "title": "利润门槛",
            "headline": commercial_headline,
            "evidence": commercial_evidence,
            "action": commercial_action,
        },
        {
            "title": "边界提醒",
            "headline": f"不把 {len(adjacent_rows)} 个相邻形态的销量当成 {seed_form} 需求",
            "evidence": f"当前共识别 {len(form_rows)} 个产品形态，只有 seed/direct 样本进入目标形态结论。",
            "action": "相邻产品只用于看替代方案、用户场景和价格带，需要单独回到各自最小类目验证。",
        },
    ]
    return {
        "dimensions": dimensions,
        "rating_summary": rating_summary,
        "conclusions": conclusions,
        "review_source_note": review_source_note,
    }


def build_single_product_insights(
    product_rows: list[dict[str, object]],
    form_rows: list[dict[str, object]],
    review_rows: list[dict[str, str]],
    review_target_rows: list[dict[str, str]],
    business: dict[str, object] | None = None,
) -> dict[str, list[dict[str, object]] | list[str]]:
    relevant = [row for row in product_rows if row.get("competitor_type") in {"seed", "direct", "keyword"}]
    seed = next((row for row in relevant if row.get("competitor_type") == "seed"), relevant[0] if relevant else {})
    seed_form = str(seed.get("product_form", ""))
    if seed_form != "beeswax bread bag":
        return build_generic_product_insights(
            relevant,
            seed,
            form_rows,
            review_rows,
            review_target_rows,
            business or {},
        )
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


VERDICT_LABELS = {
    "Shape opportunity": "可以研究",
    "Watch shape": "待核对",
    "Needs classification": "用途待确认",
    "Needs category Top100": "缺类目 Top100",
    "Reject category/form": "已排除",
}


def render_verdict_options(values: list[str]) -> str:
    """Filter options keep the pipeline value but show the Chinese label."""
    order = list(VERDICT_LABELS)
    ranked = sorted(values, key=lambda value: order.index(value) if value in order else len(order))
    options = ['<option value="">全部结论</option>']
    options.extend(
        f'<option value="{escape(value)}">{escape(VERDICT_LABELS.get(value, value))}</option>' for value in ranked
    )
    return "\n".join(options)


def build_html() -> str:
    scoring_cfg = read_json("config/scoring_rules.json")
    discovery_cfg = read_json("config/autodiscovery_rules.json")
    exclusion_cfg = read_json("config/category_exclusions.json")
    product_exclusion_cfg = read_json("config/product_exclusions.json")
    selection_rows = normalize_selection(read_csv("reports/selection_ranked.csv"))
    category_scan_rows = normalize_category_scan_state(read_csv("archive/category_scan_state.csv"))
    current_category_rows = normalize_category_report(read_csv("reports/discovered_categories.csv"))
    exclusion_rows = normalize_exclusion_rules(discovery_cfg, exclusion_cfg)
    proposals = read_json("reports/category_exclusion_proposals.json") or []
    proposal_rows = "".join(f'<tr><td>{escape(str(p.get("path", "")))}</td><td>{float(p.get("excluded_share", 0)):.0%}</td><td>{escape(str(p.get("reason", "")))}</td></tr>' for p in proposals)
    keyword_search_rows = normalize_keyword_search_index(read_csv("archive/keyword_search_index.csv"))
    latest_keyword_ranked_rows = []
    if keyword_search_rows:
        latest_index_row = read_csv("archive/keyword_search_index.csv")
        latest_index_row.sort(key=lambda row: row.get("searched_at", ""), reverse=True)
        if latest_index_row:
            latest_keyword_ranked_rows = normalize_selection(read_csv_reference(latest_index_row[0].get("ranked_csv", "")))
    source_shape_rows = read_csv("data/category_shape_validation.csv")
    source_run_ids = {row.get("validation_run_id") for row in source_shape_rows if row.get("validation_run_id")}
    batch_status = ""
    if len(source_run_ids) == 1:
        source_run = next(iter(source_run_ids))
        manifest = read_json(f"archive/discovery_runs/{source_run}/run_manifest.json")
        if manifest and "selected_categories" in manifest:
            quality = run_quality(manifest, load_rules(ROOT))
            label = "历史回放" if manifest.get("data_provider") == "mcp_replay" else "数据批次"
            finished_at = str(manifest.get("finished_at", ""))
            succeeded = manifest.get("successful_categories", 0)
            selected = manifest.get("selected_categories", 0)
            batch_status = (
                f'<div class="batch-line"><span>{label} {escape(source_run)}</span>'
                f'<span>成功 {succeeded}/{selected} 个类目</span>'
                f'<span>采集于 {escape(finished_at[:16].replace("T", " "))}</span>'
                f'<span id="batchFreshness" class="freshness" data-finished="{escape(finished_at)}" data-limit="8"></span></div>'
                '<div class="audit-warning" id="staleBanner" role="status" hidden><b></b> 每周扫描可能停了或一直失败，'
                '请看本机日志 <code>logs/weekly_category_scan.latest.log</code>。</div>'
            )
            if not quality["publishable"]:
                batch_status += (
                    f'<div class="audit-warning" role="status"><b>这一批只成功扫描了 {succeeded}/{selected} 个类目，'
                    '没达到发布门槛。</b>下面的结论只覆盖成功的类目，不代表完整市场。</div>'
                )
    shape_validation_rows = normalize_shape_validation(build_current_product_groups(ROOT, source_shape_rows))
    archive_source_rows = read_csv("archive/shape_opportunity_library.csv")
    for row in archive_source_rows:
        state, source = feedback_state(row)
        if state:
            row["archive_status"], row["review_hold_source"] = state, source
    shape_archive_rows = normalize_shape_archive(archive_source_rows)
    selection_lookup = {str(row.get("asin", "")): row for row in selection_rows if row.get("asin")}
    product_research_index = read_csv("archive/product_research_index.csv")
    product_research_archive = normalize_product_research_archive(product_research_index, selection_lookup)
    category_research_archive = read_csv("archive/category_research_index.csv")
    # The newest research only seeds the ASIN example; each research has its own page.
    current_research_asin = str((product_research_archive[0] if product_research_archive else {}).get("asin") or "")

    shape_recommendations = tag_list(
        [{"shape_recommendation": str(row.get("shape_recommendation", ""))} for row in shape_validation_rows],
        "shape_recommendation",
    )

    shape_archive_stale_count = sum(1 for row in shape_archive_rows if row.get("archive_status") == "stale_needs_rescan")
    shape_opportunity_count = sum(1 for row in shape_validation_rows if row.get("shape_recommendation") == "Shape opportunity")
    watch_shape_count = sum(1 for row in shape_validation_rows if row.get("shape_recommendation") == "Watch shape")
    validated_category_count = len(
        {str(row.get("category_path")) for row in source_shape_rows if row.get("category_path")}
    )
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

    category_research_rows = "\n".join(
        """
        <tr>
          <td><strong>{name}</strong><span>{node_id} · {path}</span></td>
          <td>{verdict}</td>
          <td>{opportunity}</td>
          <td class="num">{products}</td>
          <td class="num">{reviews}</td>
          <td>{researched_at}</td>
          <td><a class="pill green" href="{page}">研究页</a> <a class="pill gray" href="{report}">报告</a></td>
        </tr>
        """.format(
            name=escape(row.get("category_name", "")),
            node_id=escape(row.get("node_id", "")),
            path=escape(row.get("category_path", "")),
            verdict=escape(row.get("verdict", "")),
            opportunity=escape(row.get("primary_opportunity", "")),
            products=escape(row.get("product_count", "")),
            reviews=escape(row.get("raw_review_count", "")),
            researched_at=escape(row.get("researched_at", "")),
            page=escape(web_path(row.get("page_path", ""))),
            report=escape(web_path(row.get("report_path", ""))),
        )
        for row in sorted(category_research_archive, key=lambda item: item.get("researched_at", ""), reverse=True)
    )
    weights = scoring_cfg.get("weights", {})
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
        "currentCategoryScan": current_category_rows,
        "keywordSearchHistory": keyword_search_rows,
        "latestKeywordResults": latest_keyword_ranked_rows,
        "shapeValidation": shape_validation_rows,
        "productCatalog": build_catalog(ROOT, source_shape_rows, manual_root=ROOT if os.environ.get("AMZ_WORKFLOW_ROOT") else SOURCE_ROOT),
        "archive": shape_archive_rows,
        "productResearchArchive": product_research_archive,
    }

    data_json = json.dumps(dashboard_data, ensure_ascii=False)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    workbench_version = hashlib.sha256((WEB_DIR / "workbench.js").read_bytes()).hexdigest()[:12]
    keyword_version = hashlib.sha256((WEB_DIR / "keyword-search.js").read_bytes()).hexdigest()[:12]
    labels_version = hashlib.sha256((WEB_DIR / "labels.js").read_bytes()).hexdigest()[:12]
    exclusion_table_rows = "\n".join(
        '<tr><td data-label="类目路径匹配"><strong>{term}</strong></td>'
        '<td data-label="风险类型"><span class="pill gray">{kind}</span></td>'
        '<td data-label="排除原因">{reason}</td></tr>'.format(
            term=escape(row["term"]),
            kind=escape(row["type"]),
            reason=escape(row["reason"]),
        )
        for row in exclusion_rows
    )
    review_category_table_rows = "\n".join(
        '<tr><td data-label="类目范围"><strong>{name}</strong></td>'
        '<td data-label="保留评估">{allowed}</td>'
        '<td data-label="仍需排除">{blocked}</td></tr>'.format(
            name=escape(row["name"]),
            allowed=escape(row["allowed"]),
            blocked=escape(row["blocked"]),
        )
        for row in product_exclusion_cfg.get("review_categories", [])
    )
    coverage_warning_html = ""
    if current_scanned_category_count and validated_category_count < current_scanned_category_count:
        coverage_warning_html = f"""
      <div class="audit-warning">
        <strong>本轮形态验证尚未覆盖全部成功类目。</strong>
        已成功扫描 {current_scanned_category_count} 个类目，但形态验证结果只覆盖
        {validated_category_count} 个类目；本轮结果不能当成完整机会结论。
      </div>"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AMZ 选品自动化仪表盘</title>
  <link rel="stylesheet" href="workbench.css?v={workbench_version}">
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

    .policy-table {{ min-width: 0; table-layout: fixed; }}
    .policy-table th:first-child {{ width: 26%; }}
    .policy-table th,
    .policy-table td {{ overflow-wrap: anywhere; }}

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
      grid-template-columns: minmax(240px, 1.2fr) minmax(0, 2fr);
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
      grid-template-columns: repeat(4, minmax(72px, 1fr)) minmax(140px, 1.4fr);
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

    .shape-category-group {{
      display: grid;
      gap: 12px;
      padding: 4px 0 18px;
    }}

    .shape-category-group + .shape-category-group {{
      padding-top: 18px;
      border-top: 1px solid var(--line);
    }}

    .shape-category-head {{
      display: grid;
      grid-template-columns: minmax(260px, 1.2fr) minmax(0, 1.8fr);
      gap: 16px;
      align-items: start;
    }}

    .shape-category-head h3 {{
      margin: 0 0 4px;
      font-size: 17px;
    }}

    .shape-category-summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(84px, 1fr));
      gap: 10px;
    }}

    .shape-card-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}

    .shape-validation-card {{
      display: grid;
      align-content: start;
      gap: 12px;
      min-width: 0;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fff;
    }}

    .shape-card-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }}

    .shape-card-head h4 {{
      margin: 0;
      color: var(--text);
      font-size: 16px;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }}

    .shape-card-head .score {{
      flex: 0 0 92px;
    }}

    .shape-card-metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      padding: 10px 0;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
    }}

    .shape-card-metrics strong {{
      display: block;
      font-size: 16px;
      font-variant-numeric: tabular-nums;
    }}

    .shape-card-details {{
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}

    .reference-asins {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}

    .reference-asin {{
      display: inline-grid;
      gap: 1px;
      padding: 6px 8px;
      border: 1px solid #bfd2f2;
      border-radius: 6px;
      background: #f4f8ff;
      color: var(--blue);
      font-size: 12px;
      line-height: 1.25;
    }}

    .reference-asin:hover {{
      background: #eaf2ff;
      text-decoration: none;
    }}

    .reference-asin span {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 500;
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
      grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
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
      .shell {{ grid-template-columns: minmax(0, 1fr); }}
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
        grid-template-columns: minmax(0, 1fr);
      }}
      .formula-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .validation-head,
      .validation-body,
      .category-group-head,
      .shape-category-head,
      .pool-item {{
        grid-template-columns: minmax(0, 1fr);
      }}
      .validation-metrics {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }}
      .evidence-row {{
        grid-template-columns: minmax(180px, 1.4fr) 132px repeat(3, 86px);
      }}
      .shape-card-grid {{
        grid-template-columns: minmax(0, 1fr);
      }}
    }}

    @media (max-width: 700px) {{
      .policy-table,
      .policy-table tbody,
      .policy-table tr {{ display: block; }}
      .policy-table thead {{
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip-path: inset(50%);
      }}
      .policy-table tr {{
        padding: 10px 12px;
        border-bottom: 1px solid var(--line);
      }}
      .policy-table tr:last-child {{ border-bottom: 0; }}
      .policy-table td {{ display: block; padding: 4px 0; border: 0; }}
      .policy-table td:not(:first-child)::before {{
        content: attr(data-label);
        display: block;
        color: var(--muted);
        font-size: 12px;
        margin-bottom: 2px;
      }}
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
        grid-template-columns: minmax(0, 1fr);
      }}
      .formula-grid {{
        grid-template-columns: minmax(0, 1fr);
      }}
      .validation-metrics,
      .evidence-row,
      .shape-category-summary,
      .shape-card-metrics {{
        grid-template-columns: minmax(0, 1fr);
      }}
      .form-card-takeaway {{
        grid-template-columns: minmax(0, 1fr);
      }}
      .form-card-images,
      .form-overview-card {{
        grid-template-columns: minmax(0, 1fr);
      }}
      .form-card-images > div {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      input[type="search"] {{ min-width: 100%; }}
    }}
    .legacy-scoring {{ display: none; }}
    /* Readability pass: decision order, one shared visual rhythm. */
    .kpi-grid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .kpi-link {{ display: block; color: inherit; border-top: 3px solid var(--line); }}
    .kpi-link:hover {{ text-decoration: none; border-color: var(--blue); }}
    .kpi-link.good {{ border-top-color: var(--green); }}
    .kpi-link.warn {{ border-top-color: #e0a12e; }}
    .kpi .value small {{ font-size: 14px; font-weight: 500; color: var(--muted); }}
    .nav a {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
    .nav-count {{ font-size: 12px; font-weight: 600; color: var(--muted); background: #f1f3f6; border-radius: 999px; padding: 0 8px; }}
    .nav-foot {{ margin-top: 20px; font-size: 12px; color: var(--muted); }}
    .batch-line {{ display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; margin: -6px 0 14px; font-size: 13px; color: var(--muted); }}
    .freshness {{ font-weight: 700; padding: 1px 8px; border-radius: 999px; }}
    .freshness.fresh {{ background: #e8f6ef; color: var(--green); }}
    .freshness.stale {{ background: #ffe9e6; color: var(--red); }}
    .audit-warning b {{ margin-right: 4px; }}
    .audit-warning code {{ font-size: 12px; background: rgba(0,0,0,.05); padding: 1px 4px; border-radius: 3px; }}
    .subsection {{ padding: 6px 0 18px; }}
    .subsection + .subsection {{ border-top: 1px solid var(--line); padding-top: 16px; }}
    .subsection-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 10px; }}
    .scan-summary {{ display: flex; flex-wrap: wrap; gap: 6px 22px; margin: 0 0 14px; font-size: 13px; color: var(--muted); }}
    .scan-summary b {{ color: var(--text); font-size: 15px; }}
    .health-table .title-cell {{ min-width: 220px; }}
    .health-table .rank {{ display: inline-block; min-width: 34px; color: var(--muted); font-size: 12px; }}
    .health-table tr.failed td {{ color: var(--muted); }}
    .issues {{ min-width: 160px; }}
    .issue {{ display: inline-block; font-size: 12px; margin: 0 4px 4px 0; padding: 1px 6px; border-radius: 3px; background: #eef0f3; color: #4b5563; }}
    .issue.hard {{ background: #ffe9e6; color: var(--red); }}
    .issue.ok {{ background: transparent; color: var(--muted); padding: 0; }}
    .table-more {{ text-align: center; margin: 10px 0 4px; }}
    .fold {{ border-top: 1px solid var(--line); padding: 4px 0; }}
    .fold > summary {{ cursor: pointer; font-weight: 600; padding: 10px 2px; }}
    .fold[open] > summary {{ margin-bottom: 6px; }}
    .fold .formula-grid {{ margin-top: 6px; }}
    .verdict-list {{ list-style: none; padding: 0; margin: 14px 0 6px; display: grid; gap: 8px; }}
    .verdict-list .pill {{ margin-right: 8px; min-width: 76px; text-align: center; }}
    .pool-group {{ border-top: 1px solid var(--line); padding: 12px 0 4px; }}
    .pool-group:first-child {{ border-top: 0; }}
    .pool-group-head strong {{ display: block; font-size: 15px; }}
    .pool-group-head .muted-small {{ margin-top: 0; }}
    .pool-row {{ display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px 18px; align-items: start; padding: 12px 0; border-bottom: 1px solid #eef1f5; }}
    .pool-row:last-child {{ border-bottom: 0; }}
    .pool-row-title {{ display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; }}
    .pool-row-title b {{ font-size: 14px; }}
    .pool-row-main p {{ margin: 6px 0 2px; font-size: 13px; line-height: 1.6; }}
    .pool-note {{ color: var(--orange); }}
    .muted-inline {{ font-size: 12px; color: var(--muted); }}
    .pool-row-stats {{ display: flex; gap: 18px; }}
    .pool-row-stats span {{ display: grid; text-align: right; }}
    .pool-row-stats small {{ font-size: 11px; color: var(--muted); }}
    .pool-row-stats b {{ font-variant-numeric: tabular-nums; }}
    .decision-cell {{ min-width: 220px; }}
    .clamp {{ display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
    @media (max-width: 1100px) {{
      .kpi-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 760px) {{
      .pool-row {{ grid-template-columns: minmax(0, 1fr); }}
      .pool-row-stats span {{ text-align: left; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <strong>AMZ 选品自动化</strong>
        <span>类目扫描 → 形态验证 → 单品研究</span>
      </div>
      <nav class="nav">
        <a href="#overview">总览</a>
        <a href="#shape-validation">形态机会<span class="nav-count">{shape_opportunity_count} · {watch_shape_count}</span></a>
        <a href="#archive">机会池<span class="nav-count">{len(shape_archive_rows)}</span></a>
        <a href="#research-archive">研究档案<span class="nav-count">{len(product_research_archive)}</span></a>
        <a href="#keyword-search">关键词选品</a>
        <a href="#discovery">扫描明细</a>
        <a href="#logic">规则说明</a>
      </nav>
      <p class="nav-foot">页面生成于 {escape(generated_at)}</p>
    </aside>

    <main class="main">
      <div class="topbar" id="overview">
        <div>
          <h1>Amazon US 选品工作台</h1>
          <div class="subtle">每周扫描最小类目，把 Top100 拆成产品形态，只留下个人卖家能做的形态。</div>
        </div>
      </div>

      {batch_status}
      {coverage_warning_html}

      <div class="kpi-grid">
        <a class="kpi kpi-link good" href="#shape-validation">
          <div class="label">可以进入单品研究</div>
          <div class="value" id="visibleShapeOpportunityCount">{shape_opportunity_count}</div>
          <div class="note">需求、竞争、风险、价格带全部通过的形态</div>
        </a>
        <a class="kpi kpi-link warn" href="#shape-validation">
          <div class="label">值得人工核对</div>
          <div class="value" id="visibleShapeWatchCount">{watch_shape_count}</div>
          <div class="note">有需求信号，还差一步证据</div>
        </a>
        <a class="kpi kpi-link" href="#archive">
          <div class="label">机会池待复扫</div>
          <div class="value">{shape_archive_stale_count}<small> / {len(shape_archive_rows)}</small></div>
          <div class="note">历史机会，需要新一轮数据确认</div>
        </a>
        <a class="kpi kpi-link" href="#discovery">
          <div class="label">本批成功扫描</div>
          <div class="value">{current_scanned_category_count}<small> / {len(current_category_rows) or weekly_category_target} 类目</small></div>
          <div class="note">拆出 {len(shape_validation_rows)} 个形态，验证 {validated_category_count} 个类目</div>
        </a>
      </div>

      <section id="shape-validation">
        <div class="section-head">
          <div>
            <h2>形态机会</h2>
            <p>机会对象是「某个类目里的某种产品形态」，不是单个 listing。先看第一组；没有的话，看第二组排在前面的几项。点开一行看原因、下一步和参考 ASIN。</p>
          </div>
        </div>
        <div class="controls">
          <input id="shapeSearch" type="search" placeholder="搜索形态 / 类目 / 参考 ASIN">
          <select id="shapeRecommendation">{render_verdict_options(shape_recommendations)}</select>
        </div>
        <div class="shape-list" id="shapeValidationList"></div>
      </section>

      <section id="archive">
        <div class="section-head">
          <div>
            <h2>机会池</h2>
            <p>曾经通过验证的形态都留在这里，不会被每周更新覆盖。默认只看还有效和待复扫的；失效、舍弃和拦截的可以从右侧筛选里找回。</p>
          </div>
        </div>
        <div class="controls">
          <input id="archiveSearch" type="search" placeholder="搜索形态 / ASIN / 类目">
          <select id="archiveStatus">
            <option value="current" selected>有效与待复扫</option>
            <option value="">全部状态</option>
            <option value="active">历史验证有效</option>
            <option value="active_in_latest_run">本轮验证有效</option>
            <option value="stale_needs_rescan">待复扫</option>
            <option value="failed_on_rescan">复扫未通过</option>
            <option value="ip_review_required">知识产权风险拦截</option>
            <option value="suppressed_by_user">人工舍弃</option>
            <option value="rejected_by_research">研究拦截</option>
            <option value="seasonal_confirmed">季节风险待复核</option>
            <option value="not_in_latest_run">最近一轮未出现</option>
            <option value="invalidated_by_rule">新规则判定失效</option>
          </select>
        </div>
        <div class="opportunity-pool" id="archiveBody"></div>
      </section>

      <section id="research-archive">
        <div class="section-head">
          <div>
            <h2>研究档案</h2>
            <p>做过的单品研究和类目研究都在这里，包括已淘汰的。先看结论和原因，再决定要不要打开研究页。</p>
          </div>
        </div>

        <div id="research-workbench" class="subsection">
          <div class="subsection-head"><h3>收藏与研究队列</h3><span id="workbenchConnection" role="status">连接中</span></div>
          <div class="workbench-tabs" role="tablist" aria-label="收藏与研究">
            <button type="button" role="tab" aria-selected="true" data-workbench-tab="favorites">收藏 <span id="favoriteCount">0</span></button>
            <button type="button" role="tab" aria-selected="false" data-workbench-tab="jobs">研究队列 <span id="jobCount">0</span></button>
            <button type="button" role="tab" aria-selected="false" data-workbench-tab="dismissals">已舍弃 <span id="dismissalCount">0</span></button>
          </div>
          <div id="workbenchNotice" role="status" aria-live="polite"></div>
          <div id="workbenchBody"></div>
        </div>

        <div class="subsection">
          <div class="subsection-head"><h3>单品研究</h3><span class="subtle">{len(product_research_archive)} 个</span></div>
          <div class="controls">
            <input id="productResearchAsin" type="search" placeholder="输入 ASIN 查找，例如 {escape(current_research_asin or 'B0FXG8J58Q')}">
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
                  <th class="num">形态</th>
                  <th class="num">评论</th>
                  <th>最近研究</th>
                  <th>入口</th>
                </tr>
              </thead>
              <tbody id="productResearchArchiveBody"></tbody>
            </table>
          </div>
        </div>

        <div class="subsection" id="category-research-archive">
          <div class="subsection-head"><h3>类目深度研究</h3><span class="subtle">{len(category_research_archive)} 个</span></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>类目</th><th>整体判断</th><th>优先形态</th><th class="num">Top 产品</th><th class="num">原始评论</th><th>研究时间</th><th>入口</th></tr></thead>
              <tbody>{category_research_rows or '<tr><td colspan="7"><div class="empty">还没有类目深度研究档案。</div></td></tr>'}</tbody>
            </table>
          </div>
        </div>
      </section>

      <section id="keyword-search">
        <div class="section-head">
          <div>
            <h2>关键词选品</h2>
            <p>已经有方向时用这里：通过 Sorftime 搜最多 100 个相关产品，按同一套个人卖家标准初筛。结果只是候选，仍要过类目和形态验证才能进机会池。</p>
          </div>
        </div>
        <form id="keywordSearchForm" class="controls">
          <input id="keywordSearchInput" type="search" required maxlength="200" aria-label="搜索关键词" placeholder="输入关键词，例如 bread storage bag">
          <button id="keywordSearchSubmit" type="submit" disabled title="通过 Sorftime 搜索，最多 5 次数据调用"><i data-lucide="search" aria-hidden="true"></i><span>开始搜索</span></button>
        </form>
        <div id="keywordSearchStatus" role="status" aria-live="polite"></div>
        <div id="keywordSearchJobs"></div>
        <h3>搜索记录</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>关键词</th><th>搜索时间</th><th class="num">相关产品</th><th class="num">通过基础过滤</th><th class="num">可研究 / 待核对</th><th class="num">最高分</th><th>档案</th></tr></thead>
            <tbody id="keywordSearchHistoryBody"></tbody>
          </table>
        </div>
        <h3 id="keywordResultsHeading" style="margin-top: 18px;">最近一次关键词结果</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>产品</th><th class="num">分数</th><th>结论</th><th class="num">月销</th><th class="num">评论</th><th class="num">售价</th><th>原因</th></tr></thead>
            <tbody id="keywordResultBody"></tbody>
          </table>
        </div>
      </section>

      <section id="discovery">
        <div class="section-head">
          <div>
            <h2>扫描明细</h2>
            <p>本批扫描的类目按健康度排序。健康度只决定验证顺序，不决定能不能做；能不能做看上面的形态结论。</p>
          </div>
        </div>
        <div class="scan-summary" id="category-coverage">
          <span>累计已扫描 <b>{len(category_scan_rows)}</b> 个类目</span>
          <span>本批 <b>{len(current_category_rows)}</b> 个（新类目 {current_new_category_count} · 到期复扫 {current_rescan_count}）</span>
          <span>成功 <b>{current_scanned_category_count}</b> 个</span>
          <span>查看产品 <b>{current_products_examined_label}</b></span>
          <span>永久跳过规则 <b>{len(exclusion_rows)}</b> 条</span>
        </div>
        <div class="table-wrap">
          <table class="health-table">
            <thead>
              <tr>
                <th>类目</th>
                <th class="num">健康度</th>
                <th class="num">类目月销</th>
                <th class="num">评论中位</th>
                <th class="num">Top10 评论</th>
                <th>头部品牌</th>
                <th class="num">低评高销</th>
                <th class="num">新进入者</th>
                <th>问题</th>
              </tr>
            </thead>
            <tbody id="categoryHealthBody"></tbody>
          </table>
        </div>
        <div class="table-more"><button type="button" id="categoryHealthMore" hidden></button></div>

        <details class="fold">
          <summary>永久跳过的类目规则（{len(exclusion_rows)}）</summary>
          <p class="subtle">明确属于大件、强合规、强专利 / 品牌垄断或高责任风险的类目，以后不再占每周配额。</p>
          <div class="table-wrap">
            <table class="policy-table">
              <thead><tr><th>类目路径匹配</th><th>风险类型</th><th>原因</th></tr></thead>
              <tbody>{exclusion_table_rows or '<tr><td colspan="3"><div class="empty">还没有类目预筛排除规则。</div></td></tr>'}</tbody>
            </table>
          </div>
        </details>
        <details class="fold" id="category-review-rules">
          <summary>逐个产品检查的类目</summary>
          <p class="subtle">这些类目不整体跳过，但其中的高风险产品会在形态验证时逐个排除。</p>
          <div class="table-wrap">
            <table class="policy-table">
              <thead><tr><th>类目范围</th><th>保留评估</th><th>仍需排除</th></tr></thead>
              <tbody>{review_category_table_rows}</tbody>
            </table>
          </div>
        </details>
        <details class="fold">
          <summary>待确认的排除类目建议（{len(proposals)}）</summary>
          <div class="table-wrap"><table><thead><tr><th>类目</th><th>排除比例</th><th>产品级原因</th></tr></thead><tbody>{proposal_rows or '<tr><td colspan="3"><div class="empty">没有待确认的建议。</div></td></tr>'}</tbody></table></div>
        </details>
      </section>

      <section id="logic">
        <div class="section-head">
          <div>
            <h2>规则说明</h2>
            <p>判断口径都在这里，平时不用展开；对某个结论有疑问时再来查。</p>
          </div>
        </div>
        <details class="fold" open>
          <summary>判断链路：从类目到单品</summary>
          <div class="formula-grid">
            <div class="formula-card"><strong>1</strong><span><b>类目扫描：</b>每周轮换约 {weekly_category_target} 个最小类目，每个类目读 Top {weekly_products_per_category}；失败的类目单独记录，不当成「没机会」。</span></div>
            <div class="formula-card"><strong>2</strong><span><b>类目健康度：</b>评论墙、头部集中、新进入者、低评高销、需求和数据完整性，只决定验证顺序。</span></div>
            <div class="formula-card"><strong>3</strong><span><b>形态验证：</b>类目里每个形态单独检查需求、竞争、价格带，先剔除大件、易碎、合规、品牌和标题排除品。</span></div>
            <div class="formula-card"><strong>4</strong><span><b>机会池：</b>只有通过的形态才入档，带 1–3 个参考 ASIN；历史机会不会被覆盖。</span></div>
            <div class="formula-card"><strong>5</strong><span><b>单品研究：</b>从通过的形态里挑 ASIN，读评论、看图片、拆材质规格、找切入方向。</span></div>
          </div>
          <ul class="verdict-list">
            <li><span class="pill green">可以研究</span>有完整类目 Top 数据，形态通过需求、评论、低评高销、排除品和价格带检查。</li>
            <li><span class="pill orange">待核对</span>数据不错但还不够确定，比如按整类目统计、样本少或利润只是粗估。</li>
            <li><span class="pill gray">用途待确认</span>样本用途或结构不一致，要先拆成确定的形态。</li>
            <li><span class="pill red">已排除</span>评论墙、头部集中、新品卖不起来、大件 / 高风险等硬伤。</li>
          </ul>
        </details>
        <details class="fold">
          <summary>形态机会的判定标准</summary>
          <div class="table-wrap">
            <table>
              <thead><tr><th>检查项</th><th>当前口径</th><th>为什么这么设</th></tr></thead>
              <tbody>
                <tr><td><b>数据层</b></td><td>每周自动链路只用最小类目完整 Top 产品数据；手动 ASIN 缺类目数据时最多到「缺类目 Top100」。</td><td>单个 listing 容易误判，类目 Top 结构更能反映真实竞争。</td></tr>
                <tr><td><b>新进入者</b></td><td>近 18 个月上架的 listing 里有多少已经卖起来。</td><td>直接回答「新品能不能进」，权重和需求、可进入性并列最高。</td></tr>
                <tr><td><b>类目评论墙</b></td><td>类目评论中位数 ≥ 1000，或 Top10 评论中位数 ≥ 1500，强烈压分。</td><td>冷启动最怕整个类目被老链接占住。</td></tr>
                <tr><td><b>头部品牌集中</b></td><td>Top10 里同一品牌占比 ≥ 35% 视为集中。</td><td>这类往往是品牌 / 渠道护城河问题，不是产品问题。</td></tr>
                <tr><td><b>形态样本量</b></td><td>同一形态至少 2 个样本；太少只观察。</td><td>避免被单个异常 listing 误导。</td></tr>
                <tr><td><b>形态需求</b></td><td>同时看总月销、单品中位月销和销量集中度。</td><td>不追极高销量，但要有可验证的稳定需求。</td></tr>
                <tr><td><b>形态竞争</b></td><td>评论中位数 ≤ 300 更友好，或至少 3 个低评高销样本。</td><td>低评论还能卖，说明还有进入空间。</td></tr>
                <tr><td><b>排除品</b></td><td>大件、易碎、强合规、品牌护城河和标题排除品占比 ≥ 50%，直接排除。</td><td>类目健康不代表具体产品适合个人卖家。</td></tr>
                <tr><td><b>价格带</b></td><td>有效样本价格中位数 $12–$80；超出时最多「待核对」。</td><td>太低覆盖不了履约成本，太高验证成本太重。</td></tr>
                <tr><td><b>利润</b></td><td>没拿到供应商报价前，默认成本只用于粗排，不单独触发淘汰。</td><td>估算成本不能当报价用；拿到报价后恢复完整利润门槛。</td></tr>
              </tbody>
            </table>
          </div>
        </details>
        <details class="fold">
          <summary>关键词入口的粗筛分（{escape(score_formula)}）</summary>
          <p class="subtle">只用于关键词选品的候选排序，不是形态机会分。所有子项先转成 0–100 分，再按权重合并。</p>
          <div class="formula-grid">
            <div class="formula-card"><strong>{weights.get('demand', 0) * 100:.0f}%</strong><span>需求：月销量 {demand_cfg.get('est_monthly_sales', {}).get('min', 0)}–{demand_cfg.get('est_monthly_sales', {}).get('max', 0)}、关键词搜索量 {demand_cfg.get('keyword_search_volume', {}).get('min', 0)}–{demand_cfg.get('keyword_search_volume', {}).get('max', 0)}</span></div>
            <div class="formula-card"><strong>{weights.get('competition', 0) * 100:.0f}%</strong><span>竞争：评论 {competition_cfg.get('avg_review_count', {}).get('good', 0)} 以下好，{competition_cfg.get('avg_review_count', {}).get('bad', 0)} 以上差；Top10 集中度和 CPC 越低越好</span></div>
            <div class="formula-card"><strong>{weights.get('profitability', 0) * 100:.0f}%</strong><span>利润：毛利率 {profit_cfg.get('gross_margin', {}).get('min', 0) * 100:.0f}%–{profit_cfg.get('gross_margin', {}).get('max', 0) * 100:.0f}%、单件毛利 ${profit_cfg.get('gross_profit_per_unit', {}).get('min', 0):,.0f}–${profit_cfg.get('gross_profit_per_unit', {}).get('max', 0):,.0f}</span></div>
            <div class="formula-card"><strong>{weights.get('differentiation', 0) * 100:.0f}%</strong><span>差异化：先给基础分，之后靠形态、评论痛点、图片和供应商方案修正</span></div>
            <div class="formula-card"><strong>{weights.get('risk_control', 0) * 100:.0f}%</strong><span>风险：合规 {risk_cfg.get('compliance_risk', 0) * 100:.0f}%、易碎 {risk_cfg.get('fragile_risk', 0) * 100:.0f}%、大件 {risk_cfg.get('oversize_risk', 0) * 100:.0f}%、季节性 {risk_cfg.get('seasonality_score', 0) * 100:.0f}%</span></div>
          </div>
        </details>
        <details class="fold">
          <summary>每周扫描怎么轮换</summary>
          <ul>
            <li>每周扫描约 {weekly_category_target} 个最小类目，每个类目取约 {weekly_products_per_category} 个 Top 产品。</li>
            <li>优先从未扫过的类目；全部覆盖后，按最久未扫的顺序复扫。机会池里待复扫的类目排在复扫名额最前。</li>
            <li>成人、食品 / 保健、强合规、电子核心件、大件、易碎、强品牌护城河类目永久跳过。</li>
            <li>扫描失败的类目单独记录；覆盖不完整的批次不会替换线上结果。</li>
          </ul>
        </details>
      </section>
    </main>
  </div>

  <script src="vendor/lucide.min.js"></script>
  <script src="labels.js?v={labels_version}"></script>
  <script src="keyword-search.js?v={keyword_version}"></script>
  <script src="workbench.js?v={workbench_version}"></script>
  <script>
    const DATA = {data_json};
    const L = window.AmzLabels;

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
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    }}[char]));
    const pill = ([text, tone]) => `<span class="pill ${{tone}}">${{esc(text)}}</span>`;
    const scoreBar = (score) => {{
      const width = Math.max(0, Math.min(100, Number(score || 0)));
      return `<div class="score"><b>${{fmtOne(score)}}</b><div class="bar"><span style="--w:${{width}}%"></span></div></div>`;
    }};
    const researchHref = (path) => {{
      const text = String(path || "");
      if (!text) return "";
      return text.startsWith("web/") ? text.slice(4) : text;
    }};
    const categoryCell = (name, path) => `<strong title="${{esc(path)}}">${{esc(name || L.shortPath(path, 1) || "-")}}</strong><span class="muted-small" title="${{esc(path)}}">${{esc(L.parentPath(path, 3))}}</span>`;

    // Data freshness is judged when the page is opened, not when it was built.
    function renderFreshness() {{
      const node = document.getElementById("batchFreshness");
      if (!node) return;
      const finished = new Date(node.dataset.finished);
      if (Number.isNaN(finished.getTime())) return;
      const days = Math.floor((Date.now() - finished.getTime()) / 86400000);
      const limit = Number(node.dataset.limit || 8);
      node.textContent = days <= 0 ? "今天的数据" : `${{days}} 天前的数据`;
      node.className = "freshness " + (days > limit ? "stale" : "fresh");
      const banner = document.getElementById("staleBanner");
      if (banner && days > limit) {{
        banner.hidden = false;
        banner.querySelector("b").textContent = `已经 ${{days}} 天没有新数据。`;
      }}
    }}

    const HEALTH_PREVIEW = 15;
    let showAllCategories = false;
    function renderCategoryHealth() {{
      const rows = [...(DATA.currentCategoryScan || [])].sort((a, b) =>
        Number(a.category_health_rank || 999999) - Number(b.category_health_rank || 999999));
      const visible = showAllCategories ? rows : rows.slice(0, HEALTH_PREVIEW);
      document.getElementById("categoryHealthBody").innerHTML = visible.map(row => {{
        const failed = String(row.scan_status || "").toLowerCase() === "failed" || (row.scan_error && !row.category_health_rank);
        const issues = failed
          ? '<span class="pill red">扫描失败</span>'
          : (L.flags(row.category_health_flags).map(item => `<span class="issue ${{item.tone}}">${{esc(item.text)}}</span>`).join("") || '<span class="issue ok">无明显硬伤</span>');
        const rotation = row.rotation_bucket ? `<span class="issue">${{esc(L.rotation(row.rotation_bucket)[0])}}</span>` : "";
        return `
        <tr class="${{failed ? "failed" : ""}}">
          <td class="title-cell"><span class="rank">${{row.category_health_rank ? "#" + fmtInt(row.category_health_rank) : "—"}}</span>${{categoryCell(row.name, row.path || row.category_id)}}</td>
          <td class="num">${{failed ? "—" : scoreBar(row.category_health_score)}}</td>
          <td class="num">${{fmtInt(row.category_total_sales)}}</td>
          <td class="num">${{fmtInt(row.category_median_reviews)}}</td>
          <td class="num">${{fmtInt(row.category_top10_median_reviews)}}</td>
          <td>${{esc(row.category_top_brand || "-")}}${{row.category_top_brand_share ? `<span class="muted-small">占比 ${{fmtPct(row.category_top_brand_share)}}</span>` : ""}}</td>
          <td class="num">${{fmtInt(row.category_low_review_high_sales_count)}}</td>
          <td class="num">${{fmtInt(row.category_new_entrant_count)}}</td>
          <td class="issues">${{issues}} ${{rotation}}</td>
        </tr>`;
      }}).join("") || `<tr><td colspan="9"><div class="empty">还没有本批类目扫描结果。</div></td></tr>`;
      const more = document.getElementById("categoryHealthMore");
      more.hidden = rows.length <= HEALTH_PREVIEW;
      more.textContent = showAllCategories ? `只看前 ${{HEALTH_PREVIEW}} 个` : `显示全部 ${{rows.length}} 个类目`;
    }}

    function renderKeywordSearchHistory() {{
      const q = String(document.getElementById("keywordSearchInput").value || "").trim().toLowerCase();
      const rows = (DATA.keywordSearchHistory || []).filter(row => !q || String(row.keyword || "").toLowerCase().includes(q));
      document.getElementById("keywordSearchHistoryBody").innerHTML = rows.map(row => `
        <tr>
          <td class="title-cell">${{esc(row.keyword || "-")}}<span class="muted-small">${{esc(row.top_title || row.top_asin || "尚无通过候选")}}</span></td>
          <td>${{esc(String(row.searched_at || "-").replace("T", " ").slice(0, 16))}}</td>
          <td class="num">${{fmtInt(row.raw_result_count)}}</td>
          <td class="num">${{fmtInt(row.eligible_candidate_count)}}</td>
          <td class="num">${{fmtInt(row.go_count)}} / ${{fmtInt(row.watch_count)}}</td>
          <td class="num">${{fmtOne(row.top_score)}}</td>
          <td><a class="pill green" href="${{esc(row.report_md)}}" target="_blank" rel="noreferrer">报告</a> <a class="pill gray" href="${{esc(row.ranked_csv)}}" target="_blank" rel="noreferrer">数据</a></td>
        </tr>
      `).join("") || `<tr><td colspan="7"><div class="empty">还没有匹配的关键词搜索记录。</div></td></tr>`;
    }}

    function renderLatestKeywordResults() {{
      const rows = DATA.latestKeywordResults || [];
      document.getElementById("keywordResultBody").innerHTML = rows.slice(0, 30).map(row => `
        <tr>
          <td class="title-cell"><a href="${{esc(row.listing_url)}}" target="_blank" rel="noreferrer">${{esc(row.title_short || row.title || "-")}}</a><span class="muted-small">${{esc(row.asin || "-")}} · ${{esc(L.shortPath(row.category, 2) || "-")}}</span></td>
          <td class="num">${{scoreBar(row.score)}}</td>
          <td>${{pill(L.verdict(row.recommendation))}}</td>
          <td class="num">${{fmtInt(row.monthly_sales)}}</td>
          <td class="num">${{fmtInt(row.reviews)}}</td>
          <td class="num">${{fmtMoney(row.price)}}</td>
          <td>${{esc(L.flagsText(row.flags, "待复核"))}}</td>
        </tr>
      `).join("") || `<tr><td colspan="7"><div class="empty">${{DATA.keywordSearchHistory?.length ? "最近一次搜索没有通过基础过滤的候选。" : "还没有做过关键词搜索。"}}</div></td></tr>`;
    }}

    window.addEventListener("amz-keyword-results", event => {{
      const result = event.detail;
      DATA.keywordSearchHistory = [result.summary, ...(DATA.keywordSearchHistory || []).filter(row => row.run_id !== result.summary.run_id)];
      DATA.latestKeywordResults = result.rows;
      document.getElementById("keywordResultsHeading").textContent = `关键词结果：${{result.summary.keyword}}`;
      renderKeywordSearchHistory();
      renderLatestKeywordResults();
    }});

    function renderShapeValidation() {{
      if (window.AmzWorkbench) window.AmzWorkbench.renderShapes(DATA);
      else document.getElementById("shapeValidationList").innerHTML = '<div class="empty">形态列表脚本没有加载，请刷新页面。</div>';
    }}

    function renderArchive() {{
      const q = document.getElementById("archiveSearch").value.trim().toLowerCase();
      const status = document.getElementById("archiveStatus").value;
      const rows = (DATA.archive || [])
        .filter(row => !window.AmzWorkbench?.isDiscarded(row))
        .filter(row => !status || (status === "current" ? ["active", "active_in_latest_run", "stale_needs_rescan"].includes(row.archive_status) : row.archive_status === status))
        .filter(row => !q || [row.seed_asin, row.seed_title, row.product_form, row.category_path, row.opportunity_thesis].join(" ").toLowerCase().includes(q));

      const grouped = new Map();
      rows.forEach(row => {{
        const key = row.category_path || "未识别类目";
        if (!grouped.has(key)) grouped.set(key, []);
        grouped.get(key).push(row);
      }});
      const best = list => Math.max(...list.map(row => Number(row.archive_best_score || row.shape_score || 0)));
      const groups = [...grouped.entries()].sort((a, b) => best(b[1]) - best(a[1]));

      const html = groups.map(([category, groupRows]) => {{
        groupRows.sort((a, b) => Number(b.archive_best_score || b.shape_score || 0) - Number(a.archive_best_score || a.shape_score || 0));
        return `
          <article class="pool-group">
            <header class="pool-group-head">
              ${{categoryCell(L.shortPath(category, 1), category)}}
            </header>
            ${{groupRows.map(row => {{
              const note = L.archiveNote(row.archive_notes);
              return `
              <div class="pool-row">
                <div class="pool-row-main">
                  <div class="pool-row-title">
                    <b>${{esc(row.product_form || "-")}}</b>
                    ${{pill(L.archiveStatus(row.archive_status))}}
                  </div>
                  <p>${{esc(L.prose(row.opportunity_thesis) || "-")}}</p>
                  ${{note ? `<p class="pool-note">${{esc(note)}}</p>` : ""}}
                  <span class="muted-small">${{row.seed_asin ? `参考 <a href="${{esc(row.seed_listing_url)}}" target="_blank" rel="noreferrer">${{esc(row.seed_asin)}}</a> ${{esc(row.seed_title_short || "")}} · ` : ""}}首次入池 ${{esc(String(row.archive_first_seen || "-").slice(0, 10))}} · 最近 ${{esc(String(row.archive_last_seen || "-").slice(0, 10))}} · 出现 ${{fmtInt(row.archive_seen_count)}} 次</span>
                </div>
                <div class="pool-row-stats">
                  <span><small>历史最高分</small><b>${{fmtOne(row.archive_best_score)}}</b></span>
                  <span><small>平均月销</small><b>${{fmtInt(row.form_avg_sales)}}</b></span>
                  <span><small>评论中位</small><b>${{fmtInt(row.form_median_reviews)}}</b></span>
                </div>
                <div class="pool-actions">
                  ${{row.research_page ? `<a class="pill green" href="${{esc(researchHref(row.research_page))}}" target="_blank" rel="noreferrer">单品研究</a>` : ""}}
                  ${{window.AmzWorkbench?.discardButton("shape", row.decision_keys?.shape, row.product_form || "形态") || ""}}
                </div>
              </div>`;
            }}).join("")}}
          </article>
        `;
      }}).join("");

      document.getElementById("archiveBody").innerHTML = html || `<div class="empty">没有匹配的机会。只有通过形态验证的才会进入机会池。</div>`;
      window.lucide?.createIcons();
    }}

    function renderProductResearchArchive() {{
      const rows = DATA.productResearchArchive || [];
      const tone = decision => decision === "可继续" ? "green" : ["待复核", "利润未验证"].includes(decision) ? "orange" : "gray";
      document.getElementById("productResearchArchiveBody").innerHTML = rows.map(row => `
        <tr>
          <td class="title-cell">
            ${{row.web_page_available ? `<a href="${{esc(row.web_page)}}" target="_blank" rel="noreferrer">${{esc(row.title_short || row.asin)}}</a>` : `<strong>${{esc(row.title_short || row.asin)}}</strong>`}}
            <span class="muted-small">${{esc(row.asin)}} · 研究 ${{fmtInt(row.research_count)}} 次</span>
          </td>
          <td>${{esc(row.top_form || "-")}}</td>
          <td class="decision-cell"><span class="pill ${{tone(row.decision)}}">${{esc(row.decision)}}</span><span class="muted-small clamp" title="${{esc(row.decision_reason)}}">${{esc(row.decision_reason)}}</span></td>
          <td class="num">${{fmtInt(row.forms_count)}}</td>
          <td class="num">${{fmtInt(row.reviews_count)}}</td>
          <td>${{esc(String(row.last_researched || "-").slice(0, 10))}}</td>
          <td>
            ${{row.web_page_available ? `<a class="pill green" href="${{esc(row.web_page)}}" target="_blank" rel="noreferrer">研究页</a>` : `<span class="pill gray">页面未生成</span>`}}
            ${{row.report_available ? `<a class="pill gray" href="${{esc(row.report_path)}}" target="_blank" rel="noreferrer">报告</a>` : ""}}
          </td>
        </tr>
      `).join("") || `<tr><td colspan="7"><div class="empty">还没有单品研究档案。上方输入 ASIN 后生成研究命令。</div></td></tr>`;
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
      showResearchHint(`还没有 ${{String(asin || "").trim().toUpperCase() || "这个 ASIN"}} 的研究档案。需要先运行：python3 refresh_product_research.py --asin ${{String(asin || "").trim().toUpperCase() || "ASIN"}}`);
    }}
    function showResearchCommand() {{
      const asin = String(document.getElementById("productResearchAsin").value || "").trim().toUpperCase();
      showResearchHint(`新增或刷新单品研究：python3 refresh_product_research.py --asin ${{asin || "ASIN"}}。这个命令会调用 Sorftime、写入研究档案，并生成独立研究页。`);
    }}

    function bind(id, event, handler) {{
      const node = document.getElementById(id);
      if (node) node.addEventListener(event, handler);
    }}

    renderFreshness();
    renderCategoryHealth();
    renderKeywordSearchHistory();
    renderLatestKeywordResults();
    renderShapeValidation();
    renderArchive();
    renderProductResearchArchive();
    if (window.AmzWorkbench) window.AmzWorkbench.attach(DATA);

    bind("categoryHealthMore", "click", () => {{ showAllCategories = !showAllCategories; renderCategoryHealth(); }});
    bind("keywordSearchInput", "input", renderKeywordSearchHistory);
    bind("shapeSearch", "input", renderShapeValidation);
    bind("shapeRecommendation", "change", renderShapeValidation);
    ["archiveSearch", "archiveStatus"].forEach(id => {{
      bind(id, "input", renderArchive);
      bind(id, "change", renderArchive);
    }});
    window.addEventListener("amz-dismissals-changed", renderArchive);
    bind("productResearchOpen", "click", openExistingResearch);
    bind("productResearchCommand", "click", showResearchCommand);
  </script>
</body>
</html>
"""


def main() -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(OUTPUT, build_html())
    print(OUTPUT)


if __name__ == "__main__":
    main()
