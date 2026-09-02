#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from product_risk import has_valid_category, infer_compliance_risk, infer_fragile_risk, infer_oversize_risk
from product_exclusions import (
    brand_moat_reason as configured_brand_moat_reason,
    hard_exclusion_reason as configured_hard_exclusion_reason,
)


NUMERIC_FIELDS = {
    "target_price",
    "cost",
    "shipping",
    "fba_fee",
    "referral_fee_rate",
    "est_monthly_sales",
    "avg_review_count",
    "avg_rating",
    "top10_review_share",
    "keyword_search_volume",
    "keyword_cpc",
    "seasonality_score",
    "differentiation_score",
    "compliance_risk",
    "fragile_risk",
    "oversize_risk",
    "market_data_completeness",
    "profit_data_confidence",
    "evidence_confidence",
}


OUTPUT_FIELDS = [
    "listing_url",
    "opportunity_score",
    "recommendation",
    "gross_profit_per_unit",
    "gross_margin",
    "monthly_gross_profit",
    "demand_score",
    "competition_score",
    "profitability_score",
    "risk_score",
    "risk_control_score",
    "key_flags",
    "brand_moat_reason",
    "hard_stop_reason",
    "evidence_confidence",
    "evidence_grade",
    "profit_estimate_status",
]


ARCHIVE_META_FIELDS = [
    "archive_key",
    "archive_first_seen",
    "archive_last_seen",
    "archive_seen_count",
    "archive_best_score",
    "archive_latest_score",
    "archive_best_recommendation",
    "archive_latest_recommendation",
    "archive_status",
    "archive_last_run_id",
    "research_status",
    "archive_notes",
]


def amazon_listing_url(asin):
    asin = str(asin or "").strip()
    return f"https://www.amazon.com/dp/{asin}" if asin else ""


def parse_number(value, default=0.0):
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    if text.endswith("%"):
        text = text[:-1]
        try:
            return float(text.replace(",", "")) / 100
        except ValueError:
            return default
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return default


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def score_range(value, low, high):
    if high <= low:
        return 0.0
    return clamp((value - low) / (high - low) * 100)


def score_inverse(value, good, bad):
    if bad <= good:
        return 0.0
    return clamp((bad - value) / (bad - good) * 100)


def weighted_average(parts):
    total_weight = sum(weight for _, weight in parts)
    if total_weight == 0:
        return 0.0
    return sum(score * weight for score, weight in parts) / total_weight


def score_tiers(value, tiers):
    for tier in sorted(tiers, key=lambda item: parse_number(item.get("max"), 0)):
        if value <= parse_number(tier.get("max"), 0):
            return clamp(parse_number(tier.get("score"), 0))
    return 0.0


def rating_improvement_score(avg_rating):
    if avg_rating <= 0:
        return 40.0
    if avg_rating < 3.7:
        return 35.0
    if avg_rating < 4.2:
        return 85.0
    if avg_rating < 4.5:
        return 65.0
    return 25.0


def profit_is_estimated(row):
    status = str(row.get("profit_estimate_status", "") or "").strip().lower()
    return not status or "estimated" in status or "unverified" in status


def normalize_row(row, config):
    normalized = {key: value for key, value in row.items() if key is not None}
    if "listing_url" not in normalized:
        normalized["listing_url"] = amazon_listing_url(normalized.get("source_asin"))
    if row.get(None):
        overflow = ", ".join(str(value).strip() for value in row[None] if str(value).strip())
        existing_notes = str(normalized.get("notes", "")).strip()
        normalized["notes"] = ", ".join(part for part in [existing_notes, overflow] if part)
    for field in NUMERIC_FIELDS:
        normalized[field] = parse_number(row.get(field))
    normalized["fragile_risk"] = infer_fragile_risk(
        normalized.get("product_name", ""),
        normalized.get("category", ""),
        extract_brand(normalized),
        normalized.get("fragile_risk", 0),
    )
    normalized["compliance_risk"] = infer_compliance_risk(
        normalized.get("product_name", ""),
        normalized.get("category", ""),
        extract_brand(normalized),
        normalized.get("compliance_risk", 0),
    )
    normalized["oversize_risk"] = infer_oversize_risk(
        normalized.get("product_name", ""),
        normalized.get("category", ""),
        extract_brand(normalized),
        normalized.get("oversize_risk", 0),
    )
    if "evidence_confidence" not in row or not str(row.get("evidence_confidence", "")).strip():
        normalized["evidence_confidence"] = 35.0
    normalized["evidence_grade"] = str(row.get("evidence_grade") or confidence_grade(normalized["evidence_confidence"]))
    normalized["profit_estimate_status"] = str(row.get("profit_estimate_status") or "estimated_or_unverified")
    if normalized.get("referral_fee_rate", 0) == 0:
        normalized["referral_fee_rate"] = config["defaults"]["referral_fee_rate"]
    return normalized


def confidence_grade(value):
    score = parse_number(value)
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def normalize_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def extract_brand(row):
    brand = str(row.get("brand", "") or "").strip()
    if brand:
        return brand
    notes = str(row.get("notes", "") or "")
    match = re.search(r"(?:^|[;,]\s*)brand\s+([^;,]+)", notes, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def starts_with_brand(title, brand):
    title_norm = normalize_text(title)
    brand_norm = normalize_text(brand)
    return bool(brand_norm and (title_norm == brand_norm or title_norm.startswith(f"{brand_norm} ")))


def brand_moat_reason(row, config):
    reason = configured_brand_moat_reason(row)
    if reason:
        return reason
    cfg = config.get("brand_moat", {})
    if not cfg.get("enabled", False):
        return ""
    title = row.get("product_name", "")
    brand = extract_brand(row)
    for blocked in cfg.get("exclude_brands", []):
        if brand and normalize_text(brand) == normalize_text(blocked):
            return f"brand moat: {blocked}"
        if starts_with_brand(title, blocked):
            return f"brand moat: {blocked}"
    return ""


def text_contains_any(value, terms):
    normalized_value = normalize_text(value)
    for term in terms:
        normalized_term = normalize_text(term)
        if normalized_term and normalized_term in normalized_value:
            return term
    return ""


def hard_exclusion_reason(row, config):
    reason = configured_hard_exclusion_reason(row)
    if reason:
        return reason
    cfg = config.get("hard_exclusions", {})
    if cfg.get("require_valid_category", False) and not has_valid_category(row.get("category", "")):
        return "missing or invalid category evidence"
    title_match = text_contains_any(row.get("product_name", ""), cfg.get("title_contains", []))
    if title_match:
        return f"excluded title term: {title_match}"
    category_match = text_contains_any(row.get("category", ""), cfg.get("category_contains", []))
    if category_match:
        return f"excluded category: {category_match}"
    brand_match = text_contains_any(extract_brand(row), cfg.get("brand_contains", []))
    if brand_match:
        return f"excluded brand: {brand_match}"
    return ""


def risk_hard_stop_reason(row, config):
    thresholds = config.get("recommendation_thresholds", {})
    if row["compliance_risk"] >= thresholds.get("hard_stop_compliance_risk", 1000):
        return "hard compliance risk"
    if row["oversize_risk"] >= thresholds.get("hard_stop_oversize_risk", 1000):
        return "hard oversize/logistics risk"
    if row["fragile_risk"] >= thresholds.get("hard_stop_fragile_risk", 1000):
        return "hard fragile logistics risk"
    if row["seasonality_score"] >= thresholds.get("hard_stop_seasonality_score", 1000):
        return "hard seasonality risk"
    return ""


def hard_stop_reason(row, config):
    return hard_exclusion_reason(row, config) or risk_hard_stop_reason(row, config)


def calculate_scores(row, config):
    price = row["target_price"]
    referral_fee = price * row["referral_fee_rate"]
    gross_profit = price - row["cost"] - row["shipping"] - row["fba_fee"] - referral_fee
    gross_margin = gross_profit / price if price > 0 else 0
    monthly_gross_profit = gross_profit * row["est_monthly_sales"]

    demand_cfg = config["demand"]
    demand_score = weighted_average(
        [
            (
                score_range(
                    row["est_monthly_sales"],
                    demand_cfg["est_monthly_sales"]["min"],
                    demand_cfg["est_monthly_sales"]["max"],
                ),
                demand_cfg["est_monthly_sales"]["weight"],
            ),
            (
                score_range(
                    row["keyword_search_volume"],
                    demand_cfg["keyword_search_volume"]["min"],
                    demand_cfg["keyword_search_volume"]["max"],
                ),
                demand_cfg["keyword_search_volume"]["weight"],
            ),
        ]
    )

    competition_cfg = config["competition"]
    competition_score = weighted_average(
        [
            (
                score_tiers(row["avg_review_count"], competition_cfg["avg_review_count"].get("tiers", []))
                if competition_cfg["avg_review_count"].get("tiers")
                else score_inverse(
                    row["avg_review_count"],
                    competition_cfg["avg_review_count"]["good"],
                    competition_cfg["avg_review_count"]["bad"],
                ),
                competition_cfg["avg_review_count"]["weight"],
            ),
            (
                score_inverse(
                    row["top10_review_share"],
                    competition_cfg["top10_review_share"]["good"],
                    competition_cfg["top10_review_share"]["bad"],
                ),
                competition_cfg["top10_review_share"]["weight"],
            ),
            (
                score_inverse(
                    row["keyword_cpc"],
                    competition_cfg["keyword_cpc"]["good"],
                    competition_cfg["keyword_cpc"]["bad"],
                ),
                competition_cfg["keyword_cpc"]["weight"],
            ),
            (
                rating_improvement_score(row["avg_rating"]),
                competition_cfg["rating_improvement"]["weight"],
            ),
        ]
    )

    profit_cfg = config["profitability"]
    profit_parts = []
    if "gross_margin" in profit_cfg:
        profit_parts.append(
            (
                score_range(
                    gross_margin,
                    profit_cfg["gross_margin"]["min"],
                    profit_cfg["gross_margin"]["max"],
                ),
                profit_cfg["gross_margin"]["weight"],
            )
        )
    if "gross_profit_per_unit" in profit_cfg:
        profit_parts.append(
            (
                score_range(
                    gross_profit,
                    profit_cfg["gross_profit_per_unit"]["min"],
                    profit_cfg["gross_profit_per_unit"]["max"],
                ),
                profit_cfg["gross_profit_per_unit"]["weight"],
            )
        )
    if "monthly_gross_profit" in profit_cfg:
        profit_parts.append(
            (
                score_range(
                    monthly_gross_profit,
                    profit_cfg["monthly_gross_profit"]["min"],
                    profit_cfg["monthly_gross_profit"]["max"],
                ),
                profit_cfg["monthly_gross_profit"]["weight"],
            )
        )
    profitability_score = weighted_average(profit_parts)

    risk_cfg = config["risk"]
    risk_score = weighted_average(
        [
            (clamp(row["compliance_risk"]), risk_cfg["compliance_risk"]),
            (clamp(row["fragile_risk"]), risk_cfg["fragile_risk"]),
            (clamp(row["oversize_risk"]), risk_cfg["oversize_risk"]),
            (clamp(row["seasonality_score"]), risk_cfg["seasonality_score"]),
        ]
    )
    risk_control_score = 100 - risk_score
    differentiation_score = clamp(row["differentiation_score"])

    weights = config["weights"]
    score_parts = [
        (demand_score, weights["demand"]),
        (competition_score, weights["competition"]),
        (differentiation_score, weights["differentiation"]),
        (risk_control_score, weights["risk_control"]),
    ]
    # Default cost-rate assumptions are useful for rough ranking, but they are
    # not supplier quotes. Give provisional profit a reduced weight and never
    # use it as a rejection gate. Verified landed cost restores the full weight.
    profit_weight = weights["profitability"]
    if profit_is_estimated(row):
        profit_weight = min(profit_weight, profit_cfg.get("estimated_score_weight", 0.15))
    if profit_weight > 0:
        score_parts.append((profitability_score, profit_weight))
    opportunity_score = weighted_average(score_parts)

    moat_reason = brand_moat_reason(row, config)
    if moat_reason:
        opportunity_score = min(opportunity_score, float(config.get("brand_moat", {}).get("score_cap", 45)))
    stop_reason = hard_stop_reason(row, config)
    if stop_reason:
        opportunity_score = min(
            opportunity_score,
            float(config.get("recommendation_thresholds", {}).get("hard_stop_score_cap", 35)),
        )

    flags = build_flags(row, config, gross_profit, gross_margin, risk_score, moat_reason, stop_reason)
    recommendation = recommend(row, config, opportunity_score, gross_margin, gross_profit, moat_reason, stop_reason)
    if recommendation == "Reject" and not stop_reason and not moat_reason:
        opportunity_score = min(
            opportunity_score,
            float(config.get("recommendation_thresholds", {}).get("reject_score_cap", 45)),
        )

    return {
        "opportunity_score": round(opportunity_score, 1),
        "recommendation": recommendation,
        "gross_profit_per_unit": round(gross_profit, 2),
        "gross_margin": round(gross_margin, 4),
        "monthly_gross_profit": round(monthly_gross_profit, 2),
        "demand_score": round(demand_score, 1),
        "competition_score": round(competition_score, 1),
        "profitability_score": round(profitability_score, 1),
        "risk_score": round(risk_score, 1),
        "risk_control_score": round(risk_control_score, 1),
        "key_flags": "; ".join(flags),
        "brand_moat_reason": moat_reason,
        "hard_stop_reason": stop_reason,
        "evidence_confidence": round(row["evidence_confidence"], 1),
        "evidence_grade": confidence_grade(row["evidence_confidence"]),
        "profit_estimate_status": row.get("profit_estimate_status", "estimated_or_unverified"),
    }


def build_flags(row, config, gross_profit, gross_margin, risk_score, moat_reason="", stop_reason=""):
    flags = []
    thresholds = config.get("recommendation_thresholds", {})
    min_margin = thresholds.get("min_gross_margin_for_go", 0.3)
    min_unit_profit = thresholds.get("min_gross_profit_per_unit_for_go", 0)
    if moat_reason:
        flags.append(moat_reason)
    if stop_reason:
        flags.append(stop_reason)
    if profit_is_estimated(row):
        flags.append("supplier quote required; estimated profit is not a rejection gate")
    else:
        if gross_profit <= 0:
            flags.append("negative unit profit")
        if gross_margin < min_margin:
            flags.append(f"gross margin below {min_margin * 100:.0f}%")
        if min_unit_profit and gross_profit < min_unit_profit:
            flags.append(f"unit profit below ${min_unit_profit:.0f}")
    max_reviews_for_watch = thresholds.get("max_avg_review_count_for_watch", 800)
    if row["avg_review_count"] > max_reviews_for_watch:
        flags.append(f"review count above {max_reviews_for_watch:.0f}")
    if row["top10_review_share"] > 0.65:
        flags.append("top sellers concentrated")
    if row["keyword_cpc"] > 1.8:
        flags.append("high ad CPC")
    if row["compliance_risk"] >= 60:
        flags.append("compliance review needed")
    if row["seasonality_score"] >= 70:
        flags.append("high seasonality")
    if row["fragile_risk"] >= 60:
        flags.append("fragile logistics risk")
    if row["oversize_risk"] >= 60:
        flags.append("oversize risk")
    if row["evidence_confidence"] < thresholds.get("min_evidence_confidence_for_go", 60):
        flags.append("evidence confidence below supplier-validation threshold")
    if risk_score < 25 and not flags:
        flags.append("clean early risk profile")
    return flags


def recommend(row, config, score, gross_margin, gross_profit, moat_reason="", stop_reason=""):
    thresholds = config["recommendation_thresholds"]
    min_unit_profit = thresholds.get("min_gross_profit_per_unit_for_go", 0)
    estimated_profit = profit_is_estimated(row)
    if moat_reason or stop_reason:
        return "Reject"
    if not estimated_profit and gross_profit <= 0:
        return "Reject"
    if row["compliance_risk"] >= thresholds["hard_stop_compliance_risk"]:
        return "Reject"
    if (
        not estimated_profit
        and score >= thresholds["go_score"]
        and gross_margin >= thresholds["min_gross_margin_for_go"]
        and gross_profit >= min_unit_profit
        and row["compliance_risk"] <= thresholds["max_compliance_risk_for_go"]
        and row["evidence_confidence"] >= thresholds.get("min_evidence_confidence_for_go", 60)
    ):
        return "Go to supplier validation"
    if score >= thresholds["watch_score"]:
        if row["evidence_confidence"] < thresholds.get("min_evidence_confidence_for_watch", 30):
            return "Reject"
        if not estimated_profit:
            if gross_margin < thresholds.get("min_gross_margin_for_watch", 0):
                return "Reject"
            if gross_profit < thresholds.get("min_gross_profit_per_unit_for_watch", 0):
                return "Reject"
        if row["avg_review_count"] > thresholds.get("max_avg_review_count_for_watch", 1000000):
            return "Reject"
        if row["oversize_risk"] > thresholds.get("max_oversize_risk_for_watch", 1000000):
            return "Reject"
        return "Watch or collect more data"
    return "Reject"


def load_rows(input_path, config):
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [normalize_row(row, config) for row in reader]
    return rows


def write_csv(rows, output_path):
    fieldnames = []
    for row in rows:
        for field in row.keys():
            if field is not None and field not in fieldnames:
                fieldnames.append(field)
    for field in OUTPUT_FIELDS + ARCHIVE_META_FIELDS:
        if field not in fieldnames:
            fieldnames.append(field)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv_rows(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def archive_key(row):
    asin = str(row.get("source_asin", "") or "").strip()
    if asin:
        return f"asin:{asin}"
    listing_url = str(row.get("listing_url", "") or "").strip()
    if listing_url:
        return f"url:{listing_url}"
    title = normalize_text(row.get("product_name", ""))
    category = normalize_text(row.get("category", ""))
    return f"title:{title}|{category}"


def should_archive_candidate(row, config):
    thresholds = config.get("recommendation_thresholds", {})
    watch_score = parse_number(thresholds.get("watch_score"), 55)
    recommendation = str(row.get("recommendation", "") or "")
    return recommendation != "Reject" and parse_number(row.get("opportunity_score")) >= watch_score


def default_research_status(row):
    recommendation = str(row.get("recommendation", "") or "")
    if "supplier validation" in recommendation.lower():
        return "needs_supplier_validation"
    return "watchlist"


def write_run_snapshot(rows, input_path, output_csv_path, output_md_path, archive_dir, run_id, source_name, top_n):
    run_dir = archive_dir / "selection_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, run_dir / "selection_ranked.csv")
    write_markdown(rows, run_dir / "selection_report.md", source_name, top_n)
    if input_path.exists():
        shutil.copyfile(input_path, run_dir / "source_candidates.csv")
    meta = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(input_path),
        "output_csv": str(output_csv_path),
        "output_md": str(output_md_path),
        "candidate_count": len(rows),
        "archived_candidate_count": sum(1 for row in rows if row.get("recommendation") != "Reject"),
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def update_opportunity_library(rows, config, archive_dir, run_id):
    library_path = archive_dir / "opportunity_library.csv"
    existing_rows = read_csv_rows(library_path)
    library_by_key = {row.get("archive_key", ""): row for row in existing_rows if row.get("archive_key", "")}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    active_keys = set()

    for row in rows:
        if not should_archive_candidate(row, config):
            continue
        key = archive_key(row)
        if not key:
            continue
        active_keys.add(key)
        existing = library_by_key.get(key, {})
        current_score = parse_number(row.get("opportunity_score"))
        best_score = max(parse_number(existing.get("archive_best_score")), current_score)
        best_recommendation = existing.get("archive_best_recommendation", "")
        if current_score >= parse_number(existing.get("archive_best_score")):
            best_recommendation = row.get("recommendation", "")
        merged = {**existing, **row}
        merged.update(
            {
                "archive_key": key,
                "archive_first_seen": existing.get("archive_first_seen") or now,
                "archive_last_seen": now,
                "archive_seen_count": int(parse_number(existing.get("archive_seen_count"), 0)) + 1,
                "archive_best_score": round(best_score, 1),
                "archive_latest_score": row.get("opportunity_score", ""),
                "archive_best_recommendation": best_recommendation,
                "archive_latest_recommendation": row.get("recommendation", ""),
                "archive_status": "active_in_latest_run",
                "archive_last_run_id": run_id,
                "research_status": existing.get("research_status") or default_research_status(row),
                "archive_notes": existing.get("archive_notes", ""),
            }
        )
        library_by_key[key] = merged

    for key, row in library_by_key.items():
        if key not in active_keys:
            row["archive_status"] = "not_in_latest_run"

    library_rows = list(library_by_key.values())
    library_rows.sort(
        key=lambda row: (
            row.get("archive_status") != "active_in_latest_run",
            -parse_number(row.get("archive_best_score")),
            str(row.get("archive_last_seen", "")),
        )
    )
    if library_rows:
        library_path.parent.mkdir(parents=True, exist_ok=True)
        write_csv(library_rows, library_path)
    return library_path, len(active_keys), len(library_rows)


def archive_selection_outputs(rows, config, input_path, output_csv_path, output_md_path, archive_dir, top_n):
    run_id = os.environ.get("AMZ_WEEKLY_RUN_ID") or datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir.mkdir(parents=True, exist_ok=True)
    run_dir = write_run_snapshot(rows, input_path, output_csv_path, output_md_path, archive_dir, run_id, str(input_path), top_n)
    library_path, active_count, library_count = update_opportunity_library(rows, config, archive_dir, run_id)
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "library_path": library_path,
        "active_count": active_count,
        "library_count": library_count,
    }


def format_money(value):
    return f"${value:,.2f}"


def format_percent(value):
    return f"{value * 100:.1f}%"


def write_markdown(rows, output_path, source_name, top_n):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    selected = rows[:top_n]
    lines = [
        "# Amazon Product Selection Report",
        "",
        f"- Source: `{source_name}`",
        f"- Generated: {now}",
        f"- Candidates: {len(rows)}",
        "",
        "## Ranked Shortlist",
        "",
        "| Rank | Product | Category | Score | Recommendation | Margin | Monthly Gross Profit | Flags |",
        "| ---: | --- | --- | ---: | --- | ---: | ---: | --- |",
    ]
    for idx, row in enumerate(selected, start=1):
        lines.append(
            "| {rank} | {product} | {category} | {score} | {rec} | {margin} | {profit} | {flags} |".format(
                rank=idx,
                product=product_link(row),
                category=escape_pipe(row.get("category", "")),
                score=row["opportunity_score"],
                rec=escape_pipe(row["recommendation"]),
                margin=format_percent(row["gross_margin"]),
                profit=format_money(row["monthly_gross_profit"]),
                flags=escape_pipe(row["key_flags"]),
            )
        )

    lines.extend(
        [
            "",
            "## Next Actions",
            "",
            "1. Keep only products marked `Go to supplier validation` or `Watch or collect more data`.",
            "2. For each kept product, collect 10-20 competitor ASINs and supplier quotes.",
            "3. Replace estimated costs with real landed costs before sampling.",
            "4. Run compliance checks before moving to material and listing automation.",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def escape_pipe(value):
    return str(value).replace("|", "\\|")


def escape_markdown_link_text(value):
    return escape_pipe(value).replace("[", "\\[").replace("]", "\\]")


def product_link(row):
    title = escape_markdown_link_text(row.get("product_name", ""))
    url = row.get("listing_url") or amazon_listing_url(row.get("source_asin"))
    return f"[{title}]({url})" if url else title


def main():
    parser = argparse.ArgumentParser(description="Score Amazon product candidates from CSV.")
    parser.add_argument("--input", default="data/candidates.example.csv", help="Input candidates CSV.")
    parser.add_argument("--config", default="config/scoring_rules.json", help="Scoring rules JSON.")
    parser.add_argument("--output-csv", default="reports/selection_ranked.csv", help="Ranked CSV output.")
    parser.add_argument("--output-md", default="reports/selection_report.md", help="Markdown report output.")
    parser.add_argument("--top", type=int, default=20, help="Number of products to show in markdown.")
    parser.add_argument("--archive-dir", default="archive", help="Directory for run snapshots and opportunity library.")
    parser.add_argument("--no-archive", action="store_true", help="Disable archive snapshot and opportunity library update.")
    args = parser.parse_args()

    input_path = Path(args.input)
    config_path = Path(args.config)
    output_csv_path = Path(args.output_csv)
    output_md_path = Path(args.output_md)

    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    rows = load_rows(input_path, config)
    scored_rows = []
    for row in rows:
        scored = calculate_scores(row, config)
        scored_rows.append({**row, **scored})

    scored_rows.sort(key=lambda item: item["opportunity_score"], reverse=True)
    output_csv_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(scored_rows, output_csv_path)
    write_markdown(scored_rows, output_md_path, str(input_path), args.top)
    archive_result = None
    if not args.no_archive:
        archive_result = archive_selection_outputs(
            scored_rows,
            config,
            input_path,
            output_csv_path,
            output_md_path,
            Path(args.archive_dir),
            args.top,
        )

    print(f"Scored {len(scored_rows)} candidates.")
    print(f"CSV: {output_csv_path}")
    print(f"Report: {output_md_path}")
    if archive_result:
        print(f"Snapshot: {archive_result['run_dir']}")
        print(
            "Opportunity library: {path} ({active} active in latest run, {total} total)".format(
                path=archive_result["library_path"],
                active=archive_result["active_count"],
                total=archive_result["library_count"],
            )
        )


if __name__ == "__main__":
    main()
