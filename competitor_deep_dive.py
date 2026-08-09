#!/usr/bin/env python3
import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from statistics import mean, median

from import_sorftime_candidates import call_sorftime, find_product_records, pick, to_float, to_money


SUMMARY_FIELDS = [
    "source_asin",
    "candidate_listing_url",
    "source_parent_asin",
    "product_name",
    "category",
    "opportunity_score",
    "candidate_price",
    "candidate_margin",
    "competitor_count",
    "direct_competitor_count",
    "keyword_competitor_count",
    "noise_competitor_count",
    "avg_relevance_score",
    "avg_competitor_price",
    "median_competitor_price",
    "avg_competitor_sales",
    "total_top_competitor_sales",
    "avg_competitor_reviews",
    "median_competitor_reviews",
    "avg_competitor_rating",
    "low_review_high_sales_count",
    "rating_gap_count",
    "top_brand",
    "top_brand_share",
    "cn_hk_seller_share",
    "fba_share",
    "avg_variation_count",
    "top_keywords",
    "deep_dive_score",
    "deep_dive_recommendation",
    "deep_dive_flags",
]


COMPETITOR_FIELDS = [
    "source_asin",
    "candidate_listing_url",
    "source_parent_asin",
    "candidate_name",
    "competitor_asin",
    "competitor_listing_url",
    "competitor_type",
    "relevance_score",
    "keyword_evidence_count",
    "evidence_keywords",
    "relevance_reasons",
    "competitor_parent_asin",
    "title",
    "brand",
    "price",
    "monthly_sales",
    "reviews",
    "rating",
    "seller_count",
    "seller_address",
    "is_fba",
    "variation_count",
    "bsr_category",
]


KEYWORD_FIELDS = [
    "source_asin",
    "candidate_listing_url",
    "source_parent_asin",
    "product_name",
    "keyword",
    "search_volume",
    "rank",
    "position",
    "show_share",
    "raw_score",
]


STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "pack",
    "pcs",
    "set",
    "kit",
    "new",
    "official",
    "premium",
    "heavy",
    "duty",
    "large",
    "small",
    "medium",
    "black",
    "white",
    "grey",
    "gray",
    "blue",
    "red",
    "green",
    "replacement",
    "replacements",
    "compatible",
}


def amazon_listing_url(asin):
    asin = str(asin or "").strip()
    return f"https://www.amazon.com/dp/{asin}" if asin else ""


def parse_args():
    parser = argparse.ArgumentParser(description="Deep dive competitors for selected Amazon candidates.")
    parser.add_argument("--rules", default="config/deep_dive_rules.json", help="Deep dive rules JSON.")
    parser.add_argument("--input", help="Input ranked candidates CSV.")
    parser.add_argument("--limit", type=int, help="Number of candidates to deep dive.")
    parser.add_argument("--asin", action="append", help="Specific source ASIN to deep dive. Can be passed more than once.")
    parser.add_argument("--output-summary", default="data/competitor_deep_dive_summary.csv")
    parser.add_argument("--output-competitors", default="data/competitor_products.csv")
    parser.add_argument("--output-keywords", default="data/competitor_keywords.csv")
    parser.add_argument("--output-report", default="reports/competitor_deep_dive.md")
    return parser.parse_args()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows, fields):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def select_candidates(rows, rules, limit, asins):
    if asins:
        asin_set = set(asins)
        selected = [row for row in rows if row.get("source_asin") in asin_set]
        return selected[:limit]

    filters = rules["candidate_filters"]
    recommendation_contains = filters.get("recommendation_contains", "")
    excluded_flags = [flag.lower() for flag in filters.get("exclude_key_flags_contains", [])]
    selected = []
    for row in rows:
        asin = row.get("source_asin", "").strip()
        if not asin:
            continue
        if recommendation_contains and recommendation_contains not in row.get("recommendation", ""):
            continue
        flags = row.get("key_flags", "").lower()
        if any(flag in flags for flag in excluded_flags):
            continue
        selected.append(row)
    return selected[:limit]


def render_payload(template, asin):
    text = json.dumps(template, ensure_ascii=False).replace("{asin}", asin)
    return json.loads(text)


def render_competitor_payload(template, asin_list):
    text = json.dumps(template, ensure_ascii=False).replace("{asin_list}", asin_list)
    return json.loads(text)


def product_to_competitor(candidate, item):
    bsr = item.get("BsrCategory") or []
    bsr_category = ""
    if isinstance(bsr, list) and bsr and isinstance(bsr[0], list):
        bsr_category = " / ".join(str(part) for part in bsr[0][:3])
    monthly_sales = pick(item, "sales")
    if monthly_sales in (None, ""):
        monthly_sales = item.get("AsinSalesCount")
    return {
        "source_asin": candidate.get("source_asin", ""),
        "candidate_listing_url": amazon_listing_url(candidate.get("source_asin")),
        "source_parent_asin": candidate.get("source_parent_asin", ""),
        "candidate_name": candidate.get("product_name", ""),
        "competitor_asin": pick(item, "asin", ""),
        "competitor_listing_url": amazon_listing_url(pick(item, "asin", "")),
        "competitor_type": "",
        "relevance_score": 0,
        "keyword_evidence_count": 0,
        "evidence_keywords": "",
        "relevance_reasons": "",
        "competitor_parent_asin": pick(item, "parent_asin", ""),
        "title": pick(item, "title", ""),
        "brand": pick(item, "brand", ""),
        "price": round(to_money(pick(item, "price"), 0), 2),
        "monthly_sales": round(to_float(monthly_sales, 0), 0),
        "reviews": round(to_float(pick(item, "reviews"), 0), 0),
        "rating": round(to_float(pick(item, "rating"), 0), 1),
        "seller_count": round(to_float(item.get("SellerCount"), 0), 0),
        "seller_address": item.get("BuyboxSellerAddress") or "",
        "is_fba": bool(item.get("IsFBA")),
        "variation_count": round(to_float(item.get("VariationASINCount"), 0), 0),
        "bsr_category": bsr_category,
    }


def dedupe_competitors(rows):
    seen = set()
    unique = []
    for row in rows:
        key = row.get("competitor_parent_asin") or row.get("competitor_asin")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def find_keyword_records(value):
    records = []
    walk_keywords(value, records)
    return records


def keyword_detail(item):
    for key in ["keyword", "Keyword"]:
        value = item.get(key)
        if isinstance(value, dict):
            return value
    return item


def walk_keywords(value, records):
    if isinstance(value, list):
        dict_items = [item for item in value if isinstance(item, dict)]
        keyword_like = [item for item in dict_items if get_keyword_text(item)]
        if keyword_like:
            records.extend(keyword_like)
            return
        for item in value:
            walk_keywords(item, records)
    elif isinstance(value, dict):
        for item in value.values():
            walk_keywords(item, records)


def get_keyword_text(item):
    for key in ["keyword", "Keyword", "query", "Query", "term", "Term"]:
        value = item.get(key)
        if isinstance(value, dict):
            nested = value.get("Keyword") or value.get("keyword")
            if nested:
                return str(nested)
        if value:
            return str(value)
    return ""


def pick_first_number(item, keys):
    nested = keyword_detail(item)
    if nested is not item:
        value = pick_first_number(nested, keys)
        if value:
            return value
    for key in keys:
        if key in item:
            value = to_float(item.get(key), 0)
            if value:
                return value
    lowered = {str(key).lower(): value for key, value in item.items()}
    for key in keys:
        value = to_float(lowered.get(key.lower()), 0)
        if value:
            return value
    return 0.0


def keyword_to_row(candidate, item):
    nested = keyword_detail(item)
    return {
        "source_asin": candidate.get("source_asin", ""),
        "candidate_listing_url": amazon_listing_url(candidate.get("source_asin")),
        "source_parent_asin": candidate.get("source_parent_asin", ""),
        "product_name": candidate.get("product_name", ""),
        "keyword": get_keyword_text(item),
        "search_volume": round(pick_first_number(nested, ["searchVolume", "SearchVolume", "volume", "Volume"]), 0),
        "rank": round(pick_first_number(nested, ["rank", "Rank", "searchRank", "SearchRank"]), 0),
        "position": str(item.get("SearchPosition") or item.get("position") or item.get("Position") or ""),
        "show_share": pick_first_number(item, ["ShowShare", "showShare", "share", "Share"]),
        "raw_score": pick_first_number(nested, ["ClickOf90D", "SalesVolumeOf90D", "score", "Score", "searches", "Searches"]),
    }


def extract_competitor_asins(keyword_records, candidate):
    return [item["asin"] for item in collect_competitor_evidence(keyword_records, candidate)]


def collect_competitor_evidence(keyword_records, candidate):
    candidate_asin = candidate.get("source_asin", "")
    candidate_parent = candidate.get("source_parent_asin", "")
    evidence = {}

    def add_evidence(asin, keyword, source, weight):
        asin = str(asin or "").strip()
        if not asin or asin in {candidate_asin, candidate_parent}:
            return
        item = evidence.setdefault(asin, {"asin": asin, "keywords": [], "sources": set(), "weight": 0})
        if keyword and keyword not in item["keywords"]:
            item["keywords"].append(keyword)
        item["sources"].add(source)
        item["weight"] += weight

    for item in keyword_records:
        keyword = get_keyword_text(item)
        nested = keyword_detail(item)
        for raw in nested.get("Top3asin") or []:
            asin = str(raw).split(",", 1)[0].strip()
            add_evidence(asin, keyword, "top3", 3)
        for asin in nested.get("ImagesFromAsin") or []:
            add_evidence(asin, keyword, "search_image", 1)

    rows = list(evidence.values())
    rows.sort(key=lambda item: (item["weight"], len(item["keywords"]), item["asin"]), reverse=True)
    return rows


def enrich_competitor_evidence(row, evidence_by_asin):
    evidence = evidence_by_asin.get(row.get("competitor_asin"), {})
    keywords = evidence.get("keywords", [])
    row["keyword_evidence_count"] = len(keywords)
    row["evidence_keywords"] = "; ".join(keywords[:8])
    return row


def text_tokens(value):
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value).lower())
        if len(token) > 1 and token not in STOPWORDS
    }


def token_overlap_ratio(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left))


def price_proximity_score(candidate_price, competitor_price):
    candidate_price = to_float(candidate_price, 0)
    competitor_price = to_float(competitor_price, 0)
    if candidate_price <= 0 or competitor_price <= 0:
        return 0.0
    ratio = min(candidate_price, competitor_price) / max(candidate_price, competitor_price)
    return ratio * 15


def classify_competitor(candidate, row, rules):
    cfg = rules.get("classification", {})
    direct_threshold = to_float(cfg.get("direct_threshold"), 58)
    keyword_threshold = to_float(cfg.get("keyword_threshold"), 32)

    if row.get("competitor_parent_asin") and row.get("competitor_parent_asin") == candidate.get("source_parent_asin"):
        row["competitor_type"] = "direct"
        row["relevance_score"] = 95
        row["relevance_reasons"] = "same parent ASIN"
        return row

    candidate_tokens = text_tokens(candidate.get("product_name", ""))
    competitor_tokens = text_tokens(row.get("title", ""))
    category_tokens = text_tokens(candidate.get("category", ""))
    competitor_category_tokens = text_tokens(row.get("bsr_category", ""))

    title_overlap = token_overlap_ratio(candidate_tokens, competitor_tokens)
    reverse_overlap = token_overlap_ratio(competitor_tokens, candidate_tokens)
    category_overlap = bool(category_tokens & competitor_category_tokens)
    anchor_overlap = candidate_tokens & competitor_tokens
    evidence_count = to_float(row.get("keyword_evidence_count"), 0)

    score = title_overlap * 45
    score += reverse_overlap * 20
    if category_overlap:
        score += 12
    score += price_proximity_score(candidate.get("target_price") or candidate.get("candidate_price"), row.get("price"))
    score += min(10, evidence_count * 2)
    if len(anchor_overlap) >= 3:
        score += 8

    reasons = [
        f"title overlap {title_overlap:.0%}",
        f"reverse overlap {reverse_overlap:.0%}",
    ]
    if category_overlap:
        reasons.append("category match")
    if evidence_count:
        reasons.append(f"{int(evidence_count)} keyword signals")
    if len(anchor_overlap) >= 3:
        reasons.append("shared product terms")

    score = round(min(100, score), 1)
    if score >= direct_threshold and len(anchor_overlap) >= int(cfg.get("min_shared_terms_for_direct", 3)):
        competitor_type = "direct"
    elif score >= keyword_threshold:
        competitor_type = "keyword"
    else:
        competitor_type = "noise"

    row["competitor_type"] = competitor_type
    row["relevance_score"] = score
    row["relevance_reasons"] = "; ".join(reasons)
    return row


def competitor_sort_key(row):
    type_rank = {"direct": 0, "keyword": 1, "noise": 2}
    return (
        type_rank.get(row.get("competitor_type"), 3),
        -to_float(row.get("relevance_score"), 0),
        -to_float(row.get("keyword_evidence_count"), 0),
        -to_float(row.get("monthly_sales"), 0),
    )


def fetch_competitors_from_asins(asins, rules, domain):
    records = []
    detail_cfg = rules["competitor_detail"]
    batch_size = int(detail_cfg.get("batch_size", 10))
    limit = int(rules.get("competitor_candidate_pool_size", rules["competitors_per_product"]))
    for start in range(0, min(len(asins), limit), batch_size):
        batch = asins[start : start + batch_size]
        payload = render_competitor_payload(detail_cfg["payload_template"], ",".join(batch))
        response = call_sorftime(detail_cfg["method"], json.dumps(payload), domain)
        found = find_product_records(response)
        if not found and isinstance(response.get("Data"), dict):
            found = [response["Data"]]
        records.extend(found)
    return records


def avg(values):
    values = [value for value in values if value is not None]
    return mean(values) if values else 0.0


def med(values):
    values = [value for value in values if value is not None]
    return median(values) if values else 0.0


def build_summary(candidate, competitors, keywords, rules):
    thresholds = rules["thresholds"]
    if not competitors:
        flags = ["no competitor data"]
        if not keywords:
            flags.append("no keyword data")
        if to_float(candidate.get("gross_margin"), 0) < thresholds["min_margin"]:
            flags.append("candidate margin below 30%")
        return {
            "source_asin": candidate.get("source_asin", ""),
            "candidate_listing_url": amazon_listing_url(candidate.get("source_asin")),
            "source_parent_asin": candidate.get("source_parent_asin", ""),
            "product_name": candidate.get("product_name", ""),
            "category": candidate.get("category", ""),
            "opportunity_score": candidate.get("opportunity_score", ""),
            "candidate_price": candidate.get("target_price", ""),
            "candidate_margin": candidate.get("gross_margin", ""),
            "competitor_count": 0,
            "direct_competitor_count": 0,
            "keyword_competitor_count": 0,
            "noise_competitor_count": 0,
            "avg_relevance_score": 0,
            "avg_competitor_price": 0,
            "median_competitor_price": 0,
            "avg_competitor_sales": 0,
            "total_top_competitor_sales": 0,
            "avg_competitor_reviews": 0,
            "median_competitor_reviews": 0,
            "avg_competitor_rating": 0,
            "low_review_high_sales_count": 0,
            "rating_gap_count": 0,
            "top_brand": "",
            "top_brand_share": 0,
            "cn_hk_seller_share": 0,
            "fba_share": 0,
            "avg_variation_count": 0,
            "top_keywords": ", ".join(row["keyword"] for row in keywords[:8] if row.get("keyword")),
            "deep_dive_score": 0,
            "deep_dive_recommendation": "Insufficient data",
            "deep_dive_flags": "; ".join(flags),
        }
    direct_competitors = [row for row in competitors if row.get("competitor_type") == "direct"]
    keyword_competitors = [row for row in competitors if row.get("competitor_type") == "keyword"]
    noise_competitors = [row for row in competitors if row.get("competitor_type") == "noise"]
    min_direct = int(rules.get("classification", {}).get("min_direct_competitors_for_direct_market", 3))
    market_competitors = direct_competitors if len(direct_competitors) >= min_direct else direct_competitors + keyword_competitors
    if not market_competitors:
        return {
            "source_asin": candidate.get("source_asin", ""),
            "candidate_listing_url": amazon_listing_url(candidate.get("source_asin")),
            "source_parent_asin": candidate.get("source_parent_asin", ""),
            "product_name": candidate.get("product_name", ""),
            "category": candidate.get("category", ""),
            "opportunity_score": candidate.get("opportunity_score", ""),
            "candidate_price": candidate.get("target_price", ""),
            "candidate_margin": candidate.get("gross_margin", ""),
            "competitor_count": len(competitors),
            "direct_competitor_count": 0,
            "keyword_competitor_count": 0,
            "noise_competitor_count": len(noise_competitors),
            "avg_relevance_score": round(avg([to_float(row.get("relevance_score"), 0) for row in competitors]), 1),
            "avg_competitor_price": 0,
            "median_competitor_price": 0,
            "avg_competitor_sales": 0,
            "total_top_competitor_sales": 0,
            "avg_competitor_reviews": 0,
            "median_competitor_reviews": 0,
            "avg_competitor_rating": 0,
            "low_review_high_sales_count": 0,
            "rating_gap_count": 0,
            "top_brand": "",
            "top_brand_share": 0,
            "cn_hk_seller_share": 0,
            "fba_share": 0,
            "avg_variation_count": 0,
            "top_keywords": ", ".join(row["keyword"] for row in keywords[:8] if row.get("keyword")),
            "deep_dive_score": 0,
            "deep_dive_recommendation": "Insufficient data",
            "deep_dive_flags": "no relevant competitor data",
        }

    prices = [to_float(row["price"], 0) for row in market_competitors if to_float(row["price"], 0) > 0]
    sales = [to_float(row["monthly_sales"], 0) for row in market_competitors]
    reviews = [to_float(row["reviews"], 0) for row in market_competitors]
    ratings = [to_float(row["rating"], 0) for row in market_competitors if to_float(row["rating"], 0) > 0]
    variations = [to_float(row["variation_count"], 0) for row in market_competitors]
    brands = [row["brand"] for row in market_competitors if row.get("brand")]
    brand_counts = Counter(brands)
    top_brand, top_brand_count = brand_counts.most_common(1)[0] if brand_counts else ("", 0)
    top_brand_share = top_brand_count / len(market_competitors) if market_competitors else 0
    cn_hk_sellers = [row for row in market_competitors if row.get("seller_address") in {"CN", "HK"}]
    fba_rows = [row for row in market_competitors if row.get("is_fba")]
    low_review_high_sales = [
        row
        for row in market_competitors
        if to_float(row["reviews"], 0) <= thresholds["low_review"]
        and to_float(row["monthly_sales"], 0) >= thresholds["high_sales"]
    ]
    rating_gap = [row for row in market_competitors if 0 < to_float(row["rating"], 0) <= 4.2]

    flags = []
    if med(reviews) >= thresholds["review_wall"]:
        flags.append("review wall")
    if top_brand_share >= thresholds["brand_concentration"]:
        flags.append("brand concentrated")
    if market_competitors and len(cn_hk_sellers) / len(market_competitors) >= thresholds["cn_seller_share"]:
        flags.append("CN/HK sellers active")
    if direct_competitors and len(direct_competitors) < min_direct:
        flags.append("thin direct competitor set")
    if to_float(candidate.get("gross_margin"), 0) < thresholds["min_margin"]:
        flags.append("candidate margin below 30%")
    if not low_review_high_sales:
        flags.append("no low-review high-sales gap")

    demand_score = min(100, avg(sales) / 3000 * 100)
    accessibility_score = max(0, 100 - med(reviews) / 1500 * 100)
    concentration_score = max(0, 100 - top_brand_share * 100)
    margin_score = min(100, to_float(candidate.get("gross_margin"), 0) / 0.45 * 100)
    gap_score = min(100, len(low_review_high_sales) * 20 + len(rating_gap) * 8)
    deep_dive_score = (
        demand_score * 0.25
        + accessibility_score * 0.25
        + concentration_score * 0.2
        + margin_score * 0.2
        + gap_score * 0.1
    )

    recommendation = "Reject"
    if deep_dive_score >= 70 and "review wall" not in flags:
        recommendation = "Supplier validation"
    elif deep_dive_score >= 55:
        recommendation = "Keep watching"

    top_keywords = ", ".join(row["keyword"] for row in keywords[:8] if row.get("keyword"))

    return {
        "source_asin": candidate.get("source_asin", ""),
        "candidate_listing_url": amazon_listing_url(candidate.get("source_asin")),
        "source_parent_asin": candidate.get("source_parent_asin", ""),
        "product_name": candidate.get("product_name", ""),
        "category": candidate.get("category", ""),
        "opportunity_score": candidate.get("opportunity_score", ""),
        "candidate_price": candidate.get("target_price", ""),
        "candidate_margin": candidate.get("gross_margin", ""),
        "competitor_count": len(competitors),
        "direct_competitor_count": len(direct_competitors),
        "keyword_competitor_count": len(keyword_competitors),
        "noise_competitor_count": len(noise_competitors),
        "avg_relevance_score": round(avg([to_float(row.get("relevance_score"), 0) for row in competitors]), 1),
        "avg_competitor_price": round(avg(prices), 2),
        "median_competitor_price": round(med(prices), 2),
        "avg_competitor_sales": round(avg(sales), 0),
        "total_top_competitor_sales": round(sum(sales), 0),
        "avg_competitor_reviews": round(avg(reviews), 0),
        "median_competitor_reviews": round(med(reviews), 0),
        "avg_competitor_rating": round(avg(ratings), 1),
        "low_review_high_sales_count": len(low_review_high_sales),
        "rating_gap_count": len(rating_gap),
        "top_brand": top_brand,
        "top_brand_share": round(top_brand_share, 3),
        "cn_hk_seller_share": round(len(cn_hk_sellers) / len(market_competitors), 3) if market_competitors else 0,
        "fba_share": round(len(fba_rows) / len(market_competitors), 3) if market_competitors else 0,
        "avg_variation_count": round(avg(variations), 1),
        "top_keywords": top_keywords,
        "deep_dive_score": round(deep_dive_score, 1),
        "deep_dive_recommendation": recommendation,
        "deep_dive_flags": "; ".join(flags),
    }


def write_report(path, summaries):
    lines = [
        "# 竞品深挖报告",
        "",
        f"- 已分析产品数：{len(summaries)}",
        "",
        "## 候选排序",
        "",
        "| 排名 | 产品 | 深挖分 | 建议 | 直接/关键词/噪音 | 相关度 | 评论中位数 | 月销量均值 | 头部品牌占比 | 风险点 |",
        "| ---: | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for idx, row in enumerate(sorted(summaries, key=lambda item: to_float(item["deep_dive_score"], 0), reverse=True), start=1):
        lines.append(
            "| {idx} | {product} | {score} | {rec} | {mix} | {relevance} | {reviews} | {sales} | {brand_share:.0%} | {flags} |".format(
                idx=idx,
                product=product_link(row),
                score=row["deep_dive_score"],
                rec=escape_pipe(translate_recommendation(row["deep_dive_recommendation"])),
                mix="{}/{}/{}".format(
                    row.get("direct_competitor_count", 0),
                    row.get("keyword_competitor_count", 0),
                    row.get("noise_competitor_count", 0),
                ),
                relevance=row.get("avg_relevance_score", 0),
                reviews=row["median_competitor_reviews"],
                sales=row["avg_competitor_sales"],
                brand_share=to_float(row["top_brand_share"], 0),
                flags=escape_pipe(translate_flags(row["deep_dive_flags"])),
            )
        )
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            "1. 只保留 `供应商验证` 或强 `继续观察` 的产品。",
            "2. 用真实供应商报价、头程、FBA 费用替换当前估算成本。",
            "3. 对保留下来的 ASIN 做差评提取，再进入素材和 listing 自动化。",
            "",
        ]
    )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def escape_pipe(value):
    return str(value).replace("|", "\\|")


def escape_markdown_link_text(value):
    return escape_pipe(value).replace("[", "\\[").replace("]", "\\]")


def product_link(row):
    title = escape_markdown_link_text(row["product_name"][:120])
    url = row.get("candidate_listing_url") or amazon_listing_url(row.get("source_asin"))
    return f"[{title}]({url})" if url else title


def translate_recommendation(value):
    mapping = {
        "Supplier validation": "供应商验证",
        "Keep watching": "继续观察",
        "Reject": "淘汰",
        "Insufficient data": "数据不足",
    }
    return mapping.get(value, value)


def translate_flags(value):
    replacements = {
        "review wall": "评论墙",
        "brand concentrated": "品牌集中",
        "CN/HK sellers active": "中港卖家活跃",
        "candidate margin below 30%": "候选毛利低于30%",
        "no low-review high-sales gap": "没有低评论高销量缺口",
        "no competitor data": "无竞品数据",
        "no keyword data": "无关键词数据",
        "no relevant competitor data": "无相关竞品数据",
        "thin direct competitor set": "直接竞品数量偏少",
    }
    parts = [part.strip() for part in str(value or "").split(";") if part.strip()]
    return "；".join(replacements.get(part, part) for part in parts)


def main():
    args = parse_args()
    rules = load_json(args.rules)
    input_path = args.input or rules["input"]
    limit = args.limit or int(rules["limit"])
    domain = rules["domain"]
    candidates = select_candidates(read_csv(input_path), rules, limit, args.asin)
    if not candidates:
        raise SystemExit("No candidates selected for deep dive.")

    all_competitors = []
    all_keywords = []
    summaries = []
    for candidate in candidates:
        asin = candidate["source_asin"]
        keyword_payload = render_payload(rules["keyword_lookup"]["payload_template"], asin)
        keyword_response = call_sorftime(rules["keyword_lookup"]["method"], json.dumps(keyword_payload), domain)
        keyword_records = find_keyword_records(keyword_response)
        keyword_rows = [keyword_to_row(candidate, item) for item in keyword_records]
        keyword_rows = [row for row in keyword_rows if row.get("keyword")][: int(rules["keywords_per_product"])]

        competitor_evidence = collect_competitor_evidence(keyword_records, candidate)
        pool_size = int(rules.get("competitor_candidate_pool_size", rules["competitors_per_product"]))
        competitor_asins = [item["asin"] for item in competitor_evidence[:pool_size]]
        evidence_by_asin = {item["asin"]: item for item in competitor_evidence}
        competitor_records = fetch_competitors_from_asins(competitor_asins, rules, domain) if competitor_asins else []
        competitor_rows = []
        for item in competitor_records:
            row = product_to_competitor(candidate, item)
            enrich_competitor_evidence(row, evidence_by_asin)
            classify_competitor(candidate, row, rules)
            competitor_rows.append(row)
        competitor_rows = dedupe_competitors(competitor_rows)
        competitor_rows.sort(key=competitor_sort_key)
        competitor_rows = competitor_rows[: int(rules["competitors_per_product"])]

        all_competitors.extend(competitor_rows)
        all_keywords.extend(keyword_rows)
        summaries.append(build_summary(candidate, competitor_rows, keyword_rows, rules))
        print(f"{asin}: {len(competitor_rows)} competitors, {len(keyword_rows)} keywords")

    summaries.sort(key=lambda item: to_float(item["deep_dive_score"], 0), reverse=True)
    write_csv(args.output_summary, summaries, SUMMARY_FIELDS)
    write_csv(args.output_competitors, all_competitors, COMPETITOR_FIELDS)
    write_csv(args.output_keywords, all_keywords, KEYWORD_FIELDS)
    write_report(args.output_report, summaries)
    print(f"Summary: {args.output_summary}")
    print(f"Competitors: {args.output_competitors}")
    print(f"Keywords: {args.output_keywords}")
    print(f"Report: {args.output_report}")


if __name__ == "__main__":
    main()
