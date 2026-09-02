#!/usr/bin/env python3
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from product_risk import (
    infer_compliance_risk as infer_title_compliance_risk,
    infer_fragile_risk,
    infer_oversize_risk as infer_title_oversize_risk,
)


OUTPUT_FIELDS = [
    "source_strategy",
    "source_category_id",
    "source_category_name",
    "source_category_path",
    "source_asin",
    "source_parent_asin",
    "product_name",
    "category",
    "marketplace",
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
    "evidence_grade",
    "data_source_summary",
    "profit_estimate_status",
    "notes",
]


ALIASES = {
    "asin": ["asin", "ASIN"],
    "parent_asin": ["ParentAsin", "parentAsin", "parent_asin"],
    "title": ["title", "Title", "name", "Name", "product_name", "productName"],
    "price": [
        "SalesPrice",
        "salesPrice",
        "price",
        "Price",
        "current_price",
        "currentPrice",
        "buyBoxPrice",
        "salePrice",
        "listPrice",
    ],
    "fba_fee": ["FbaFee", "fbaFee", "fba_fee", "fbaFeeAmount"],
    "sales": [
        "sales",
        "Sales",
        "monthly_sales",
        "monthly_sales_volume",
        "monthlySales",
        "monthSaleVolume",
        "MonthSaleVolume",
        "ListingSalesVolumeOfMonth",
        "listingSalesVolumeOfMonth",
        "est_monthly_sales",
        "sold",
    ],
    "reviews": [
        "review_count",
        "reviewCount",
        "commentCount",
        "CommentCount",
        "RatingsCount",
        "ratingsCount",
        "reviews",
        "Reviews",
        "ratings_total",
        "ratingCount",
    ],
    "rating": [
        "rating",
        "Rating",
        "ratings",
        "Ratings",
        "avg_rating",
        "star_rating",
        "star",
        "Star",
        "stars",
    ],
    "category": ["category", "category_name", "categoryName", "product_category"],
    "brand": ["brand", "Brand"],
    "package_size": ["package_size", "packageSize", "Size", "size"],
    "search_volume": ["search_volume", "searchVolume", "volume", "monthlySearches"],
    "cpc": ["cpc", "CPC", "keyword_cpc"],
}


def parse_args():
    parser = argparse.ArgumentParser(description="Import Amazon product candidates from Sorftime CLI JSON.")
    parser.add_argument("--method", help="Sorftime API method, for example keyword_search_results or ProductRequest.")
    parser.add_argument("--payload", help="JSON payload passed to Sorftime CLI.")
    parser.add_argument("--domain", default="1", help="Sorftime domain id. US is commonly 1.")
    parser.add_argument("--from-json", help="Use an existing Sorftime JSON response instead of calling CLI.")
    parser.add_argument("--queries", help="CSV file with name,method,domain,payload,limit,category rows.")
    parser.add_argument("--defaults", default="config/import_defaults.json", help="Default values JSON.")
    parser.add_argument("--output", default="data/sorftime_candidates.csv", help="Output candidates CSV.")
    parser.add_argument("--limit", type=int, help="Maximum products to import.")
    parser.add_argument("--category", help="Fallback category name.")
    parser.add_argument("--score", action="store_true", help="Run product_selection.py after importing.")
    return parser.parse_args()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


TRANSIENT_SORFTIME_ERRORS = (
    "ECONNRESET",
    "ETIMEDOUT",
    "ESOCKETTIMEDOUT",
    "EAI_AGAIN",
    "ENOTFOUND",
    "TLS handshake timeout",
    "context deadline exceeded",
    "network timeout",
)

NON_RETRYABLE_SORFTIME_ERRORS = (
    "unauthorized",
    "forbidden",
    "too many requests",
    "rate limit",
    "rate_limit",
    "quota",
    "authentication",
    "auth failed",
    "invalid account",
    "account-sk",
    "account sk",
)

HTTP_NON_RETRYABLE_PATTERN = re.compile(r"\b(?:http\s*)?(?:status(?:code)?|code)?\s*[:=]?\s*(?:401|403|429)\b", re.IGNORECASE)
HTTP_5XX_PATTERN = re.compile(r"\b(?:http\s*)?(?:status(?:code)?\s*[:=]?\s*)?5\d\d\b", re.IGNORECASE)


def is_non_retryable_sorftime_error(output):
    normalized = str(output or "").lower()
    return bool(HTTP_NON_RETRYABLE_PATTERN.search(normalized)) or any(
        term.lower() in normalized for term in NON_RETRYABLE_SORFTIME_ERRORS
    )


def is_transient_sorftime_error(output):
    normalized = str(output or "").lower()
    if is_non_retryable_sorftime_error(normalized):
        return False
    if any(term.lower() in normalized for term in TRANSIENT_SORFTIME_ERRORS):
        return True
    if HTTP_5XX_PATTERN.search(normalized):
        return True
    return False


def call_sorftime(method, payload, domain, max_attempts=3):
    if not shutil.which("sorftime"):
        raise RuntimeError(
            "Sorftime CLI is not installed. Install it with `npm install -g sorftime-cli` "
            "and configure your Account-SK with `sorftime add myaccount your-account-sk`."
        )
    command = ["sorftime", "api", method]
    if payload:
        parsed_payload = json.loads(payload)
        if parsed_payload:
            command.append(json.dumps(parsed_payload, ensure_ascii=False))
    command.extend(["--domain", str(domain)])
    last_output = ""
    for attempt in range(1, max_attempts + 1):
        completed = subprocess.run(command, text=True, capture_output=True)
        last_output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        if completed.returncode == 0:
            response = parse_cli_json(completed.stdout)
            validate_sorftime_response(response)
            return response
        if attempt >= max_attempts or not is_transient_sorftime_error(last_output):
            break
        delay_seconds = attempt * 2
        print(
            f"Sorftime API transient failure; retrying attempt {attempt + 1}/{max_attempts} in {delay_seconds}s.",
            file=sys.stderr,
        )
        time.sleep(delay_seconds)
    raise RuntimeError(f"Sorftime API call failed: {' '.join(command)}\n{last_output}")


def parse_cli_json(output):
    text = output.strip()
    starts = [idx for idx in [text.find("{"), text.find("[")] if idx >= 0]
    if not starts:
        raise RuntimeError(f"Sorftime CLI did not return JSON:\n{text}")
    return json.loads(text[min(starts):])


def validate_sorftime_response(response):
    """Raise when the CLI transport succeeded but Sorftime rejected the request."""

    if not isinstance(response, dict) or "Code" not in response:
        return
    code = str(response.get("Code", "")).strip()
    if code in {"", "0", "200"}:
        return
    message = str(response.get("Message") or response.get("message") or "Unknown Sorftime error").strip()
    raise RuntimeError(f"Sorftime API business error {code}: {message}")


def read_query_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def find_product_records(value):
    records = []
    walk_for_records(value, records)
    return records


def walk_for_records(value, records):
    if isinstance(value, list):
        dict_items = [item for item in value if isinstance(item, dict)]
        product_like = [item for item in dict_items if is_product_like(item)]
        if product_like:
            records.extend(product_like)
            return
        for item in value:
            walk_for_records(item, records)
    elif isinstance(value, dict):
        for item in value.values():
            walk_for_records(item, records)


def is_product_like(item):
    keys = {str(key).lower() for key in item.keys()}
    has_identity = bool(keys.intersection({"asin", "title", "name", "product_name", "productname"}))
    has_market_data = bool(keys.intersection({"price", "sales", "monthly_sales", "review_count", "reviews", "rating"}))
    return has_identity and has_market_data


def pick(item, logical_name, default=None):
    for alias in ALIASES[logical_name]:
        if alias in item and item[alias] not in (None, ""):
            return item[alias]
    lowered = {str(key).lower(): value for key, value in item.items()}
    for alias in ALIASES[logical_name]:
        value = lowered.get(alias.lower())
        if value not in (None, ""):
            return value
    return default


def to_float(value, default=0.0):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    if text.endswith("%"):
        text = text[:-1]
        scale = 0.01
    else:
        scale = 1
    text = text.replace("$", "").replace(",", "").replace("￥", "").replace("¥", "")
    try:
        return float(text) * scale
    except ValueError:
        return default


def to_money(value, default=0.0):
    amount = to_float(value, default)
    if abs(amount) >= 300:
        return amount / 100
    return amount


def build_candidate(item, defaults, category_override=None):
    raw_price = pick(item, "price")
    raw_sales = pick(item, "sales")
    raw_reviews = pick(item, "reviews")
    raw_rating = pick(item, "rating")
    raw_search_volume = pick(item, "search_volume")
    raw_cpc = pick(item, "cpc")
    price = to_money(raw_price, 0)
    if price <= 0:
        price = 19.99
    cost = round(price * to_float(defaults["cost_rate"], 0.28), 2)
    shipping = round(price * to_float(defaults["shipping_rate"], 0.08), 2)
    reported_fba_fee = to_money(pick(item, "fba_fee"), 0)
    fba_fee = reported_fba_fee if reported_fba_fee > 0 else max(to_float(defaults["min_fba_fee"], 3.5), price * to_float(defaults["fba_fee_rate"], 0.18))
    fba_fee = round(fba_fee, 2)
    category = category_override or pick(item, "category", defaults["category"])
    asin = pick(item, "asin", "")
    parent_asin = pick(item, "parent_asin", "")
    brand = pick(item, "brand", "")
    notes = "Imported from Sorftime"
    if asin:
        notes += f"; ASIN {asin}"
    if parent_asin:
        notes += f"; parent {parent_asin}"
    if brand:
        notes += f"; brand {brand}"

    observed = {
        "price": to_money(raw_price, 0) > 0,
        "sales": raw_sales not in (None, ""),
        "reviews": raw_reviews not in (None, ""),
        "rating": raw_rating not in (None, ""),
        "fba_fee": reported_fba_fee > 0,
        "search_volume": raw_search_volume not in (None, ""),
        "cpc": raw_cpc not in (None, ""),
    }
    completeness_weights = {
        "price": 15,
        "sales": 25,
        "reviews": 15,
        "rating": 10,
        "fba_fee": 10,
        "search_volume": 15,
        "cpc": 10,
    }
    market_completeness = sum(weight for key, weight in completeness_weights.items() if observed[key])
    profit_confidence = 35 if observed["fba_fee"] else 20
    evidence_confidence = round(market_completeness * 0.75 + profit_confidence * 0.25, 1)
    evidence_grade = "A" if evidence_confidence >= 80 else "B" if evidence_confidence >= 60 else "C" if evidence_confidence >= 40 else "D"
    observed_fields = ",".join(key for key, value in observed.items() if value) or "none"
    estimated_fields = ",".join(key for key, value in observed.items() if not value)

    return {
        "source_asin": asin,
        "source_parent_asin": parent_asin,
        "product_name": pick(item, "title", asin or "Untitled product"),
        "category": category,
        "marketplace": defaults["marketplace"],
        "target_price": round(price, 2),
        "cost": cost,
        "shipping": shipping,
        "fba_fee": fba_fee,
        "referral_fee_rate": defaults["referral_fee_rate"],
        "est_monthly_sales": round(to_float(raw_sales, 0), 0),
        "avg_review_count": round(to_float(raw_reviews, 0), 0),
        "avg_rating": round(to_float(raw_rating, 0), 1),
        "top10_review_share": defaults["top10_review_share"],
        "keyword_search_volume": round(to_float(raw_search_volume, defaults["keyword_search_volume"]), 0),
        "keyword_cpc": to_float(raw_cpc, defaults["keyword_cpc"]),
        "seasonality_score": infer_seasonality_score(item, defaults),
        "differentiation_score": defaults["differentiation_score"],
        "compliance_risk": infer_compliance_risk(item, defaults, category),
        "fragile_risk": infer_fragile_risk(
            pick(item, "title", ""),
            category,
            brand,
            defaults["fragile_risk"],
        ),
        "oversize_risk": infer_oversize_risk(item, defaults, category),
        "market_data_completeness": market_completeness,
        "profit_data_confidence": profit_confidence,
        "evidence_confidence": evidence_confidence,
        "evidence_grade": evidence_grade,
        "data_source_summary": f"observed:{observed_fields}; estimated:{estimated_fields}",
        "profit_estimate_status": "estimated_default_costs",
        "notes": notes,
    }


def infer_compliance_risk(item, defaults, category_override=None):
    title = pick(item, "title", "")
    category = category_override or pick(item, "category", "")
    brand = pick(item, "brand", "")
    text = " ".join(str(part) for part in [title, category, brand]).lower()
    risk = infer_title_compliance_risk(title, category, brand, defaults["compliance_risk"])
    high_risk_terms = [
        "supplement",
        "vitamin",
        "nutrition",
        "protein",
        "allergy",
        "relief",
        "medical",
        "adhesive bandage",
        "battery",
        "wifi",
        "bluetooth",
        "smart plug",
        "matter",
        "fcc",
        "etl",
        "mlb",
        "magic:",
        "trading card",
        "solar powered",
        "solar light",
        "solar lights",
        "electric bike pump",
        "bike air pump",
        "hunting",
        "gunner",
        "shooting",
    ]
    if any(term in text for term in high_risk_terms):
        risk = max(risk, 65)
    return risk


def infer_seasonality_score(item, defaults):
    text = str(pick(item, "title", "")).lower()
    score = to_float(defaults["seasonality_score"], 20)
    if any(term in text for term in ["christmas", "halloween", "valentine", "mothers day", "easter"]):
        score = max(score, 70)
    if any(term in text for term in ["hunting", "decoy", "turkey", "deer season"]):
        score = max(score, 85)
    return score


def infer_oversize_risk(item, defaults, category_override=None):
    title = pick(item, "title", "")
    category = category_override or pick(item, "category", "")
    brand = pick(item, "brand", "")
    package_size = pick(item, "package_size", "")
    score = infer_title_oversize_risk(
        " ".join(str(part) for part in [title, package_size]),
        category,
        brand,
        defaults["oversize_risk"],
    )
    text = " ".join(
        str(part) for part in [title, category, brand, package_size]
    ).lower()
    weight = to_float(item.get("Weight"), 0)
    if weight >= 5000:
        score = max(score, 85)
    elif weight >= 2000:
        score = max(score, 65)
    elif weight >= 1000:
        score = max(score, 45)
    size = item.get("Size")
    if isinstance(size, list) and size:
        dimensions = []
        for raw in size:
            dimensions.extend(to_float(part, 0) for part in str(raw).replace("，", ",").split(","))
        if dimensions and max(dimensions) >= 45:
            score = max(score, 60)
    return score


def dedupe_candidates(candidates):
    seen = set()
    unique = []
    for candidate in candidates:
        key = candidate.get("source_parent_asin") or candidate.get("source_asin") or (candidate["product_name"], candidate["target_price"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def write_candidates(candidates, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(candidate)


def run_scoring(output_path):
    command = ["python3", "product_selection.py", "--input", str(output_path)]
    subprocess.run(command, check=True)


def main():
    args = parse_args()
    defaults = load_json(args.defaults)
    limit = args.limit or int(defaults["limit"])
    output_path = Path(args.output)
    candidates = []

    if args.queries:
        for row in read_query_rows(args.queries):
            method = row["method"].strip()
            payload = row["payload"].strip()
            domain = row.get("domain") or args.domain
            row_limit = int(row.get("limit") or limit)
            category = row.get("category") or args.category
            response = call_sorftime(method, payload, domain)
            records = find_product_records(response)
            candidates.extend(build_candidate(item, defaults, category) for item in records[:row_limit])
    else:
        if args.from_json:
            response = load_json(args.from_json)
        else:
            if not args.method or not args.payload:
                raise SystemExit("Provide --method and --payload, or use --from-json / --queries.")
            response = call_sorftime(args.method, args.payload, args.domain)
        records = find_product_records(response)
        candidates.extend(build_candidate(item, defaults, args.category) for item in records[:limit])

    candidates = dedupe_candidates(candidates)[:limit]
    if not candidates:
        raise SystemExit("No product-like records found in Sorftime response. Save the raw JSON and check field names.")

    write_candidates(candidates, output_path)
    print(f"Imported {len(candidates)} candidates into {output_path}")
    if args.score:
        run_scoring(output_path)


if __name__ == "__main__":
    main()
