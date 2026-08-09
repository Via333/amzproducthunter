#!/usr/bin/env python3
import argparse
import csv
import json
import re
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from competitor_deep_dive import (
    amazon_listing_url,
    classify_competitor,
    collect_competitor_evidence,
    dedupe_competitors,
    enrich_competitor_evidence,
    find_keyword_records,
    get_keyword_text,
    product_to_competitor,
)
from import_sorftime_candidates import call_sorftime, find_product_records, pick, to_float, to_money


PRODUCT_FIELDS = [
    "source_asin",
    "source",
    "listing_url",
    "product_type",
    "relevance_score",
    "competitor_type",
    "product_form",
    "material",
    "material_evidence",
    "detail_material",
    "detail_evidence",
    "pack_count",
    "closure",
    "style",
    "use_case",
    "feature_tags",
    "asin",
    "parent_asin",
    "title",
    "brand",
    "price",
    "monthly_sales",
    "reviews",
    "rating",
    "seller_address",
    "is_fba",
    "variation_count",
    "bsr_category",
    "main_image_url",
    "image_file",
    "visual_product_form",
    "visual_material_signal",
    "visual_pack_count",
    "visual_closure",
    "visual_style",
    "visual_notes",
]


FORM_FIELDS = [
    "product_form",
    "count",
    "direct_count",
    "keyword_count",
    "avg_price",
    "median_price",
    "avg_monthly_sales",
    "median_reviews",
    "avg_rating",
    "low_review_high_sales_count",
    "top_materials",
    "top_pack_counts",
    "top_styles",
    "opportunity_note",
]


KEYWORD_FIELDS = [
    "keyword",
    "search_volume",
    "rank",
    "show_share",
    "clicks_90d",
    "top3_asins",
    "image_asin_count",
]


REVIEW_FIELDS = [
    "review_target_asin",
    "asin",
    "listing_url",
    "rating",
    "review_title",
    "review_text",
    "review_date",
    "pain_point_tags",
    "review_link",
    "verified_purchase",
    "asin_property",
    "helpful",
]


REVIEW_TARGET_FIELDS = [
    "asin",
    "listing_url",
    "title",
    "competitor_type",
    "product_form",
    "monthly_sales",
    "reviews",
    "rating",
    "review_rows_collected",
]


IMAGE_FIELDS = [
    "image_index",
    "asin",
    "listing_url",
    "title",
    "product_form",
    "material",
    "pack_count",
    "main_image_url",
    "image_file",
    "visual_product_form",
    "visual_material_signal",
    "visual_pack_count",
    "visual_closure",
    "visual_style",
    "visual_notes",
]


def parse_args():
    parser = argparse.ArgumentParser(description="Build a product-level Amazon opportunity research report.")
    parser.add_argument("--asin", default=None, help="Target ASIN to research.")
    parser.add_argument("--rules", default="config/opportunity_research_rules.json")
    parser.add_argument("--output-dir", default=None, help="Directory for CSV outputs.")
    parser.add_argument("--report", default=None, help="Markdown report output.")
    parser.add_argument("--domain", default=None)
    return parser.parse_args()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_csv(path, rows, fields):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render_template(template, values):
    text = json.dumps(template, ensure_ascii=False)
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return json.loads(text)


def fetch_product_details(asins, rules, domain):
    detail_cfg = rules["product_detail"]
    batch_size = int(rules.get("product_detail_batch_size", 10))
    records = []
    seen = set()
    asins = [asin for asin in asins if asin]
    for start in range(0, len(asins), batch_size):
        batch = [asin for asin in asins[start : start + batch_size] if asin not in seen]
        if not batch:
            continue
        seen.update(batch)
        payload = render_template(detail_cfg["payload_template"], {"asin_list": ",".join(batch)})
        response = call_sorftime(detail_cfg["method"], json.dumps(payload, ensure_ascii=False), domain)
        found = find_product_records(response)
        if not found and isinstance(response.get("Data"), dict):
            found = [response["Data"]]
        records.extend(found)
    return records


def fetch_seed_product(asin, rules, domain):
    records = fetch_product_details([asin], rules, domain)
    if not records:
        raise SystemExit(f"No product detail returned for {asin}")
    return records[0]


def fetch_keywords(asin, rules, domain):
    cfg = rules["keyword_lookup"]
    payload = render_template(cfg["payload_template"], {"asin": asin})
    response = call_sorftime(cfg["method"], json.dumps(payload, ensure_ascii=False), domain)
    return find_keyword_records(response)


def fetch_category_asins(seed_product, rules, domain):
    category_cfg = rules.get("category_products", {})
    if not category_cfg.get("enabled", False):
        return []
    node_id = first_bsr_node_id(seed_product)
    if not node_id:
        return []
    asins = []
    pages = int(category_cfg.get("pages", 1))
    per_page = int(category_cfg.get("products_per_page", 30))
    for page in range(1, pages + 1):
        payload = render_template(category_cfg["payload_template"], {"node_id": node_id, "page": page})
        response = call_sorftime(category_cfg["method"], json.dumps(payload, ensure_ascii=False), domain)
        for item in find_product_records(response)[:per_page]:
            asin = pick(item, "asin", "")
            if asin:
                asins.append(asin)
    return asins


def first_bsr_node_id(product):
    bsr = product.get("BsrCategory") or []
    if isinstance(bsr, list) and bsr and isinstance(bsr[0], list) and len(bsr[0]) >= 2:
        return str(bsr[0][1])
    return ""


def keyword_to_row(item):
    nested = item.get("Keyword") if isinstance(item.get("Keyword"), dict) else item.get("keyword")
    if not isinstance(nested, dict):
        nested = item
    top3 = nested.get("Top3asin") or []
    return {
        "keyword": get_keyword_text(item),
        "search_volume": round(to_float(nested.get("SearchVolume") or nested.get("searchVolume"), 0), 0),
        "rank": round(to_float(nested.get("Rank") or nested.get("rank"), 0), 0),
        "show_share": to_float(item.get("ShowShare") or item.get("showShare"), 0),
        "clicks_90d": round(to_float(nested.get("ClickOf90D"), 0), 0),
        "top3_asins": "; ".join(str(raw).split(",", 1)[0] for raw in top3),
        "image_asin_count": len(nested.get("ImagesFromAsin") or []),
    }


def product_photo(item):
    photos = item.get("Photo") or item.get("Images") or []
    if isinstance(photos, list) and photos:
        return str(photos[0])
    if isinstance(photos, str):
        return photos
    return ""


def bsr_category_text(item):
    bsr = item.get("BsrCategory") or []
    if isinstance(bsr, list) and bsr and isinstance(bsr[0], list):
        return " / ".join(str(part) for part in bsr[0][:3])
    return ""


def pack_count(title):
    text = str(title).lower()
    patterns = [
        r"set of\s+(\d+)",
        r"(\d+)\s*[- ]?pack",
        r"(\d+)\s*pcs",
        r"(\d+)\s*pieces",
        r"(\d+)\s*count",
        r"(\d+)\s*ct",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return 1


def first_matching_label(text, rules):
    lowered = str(text).lower()
    for label, terms in rules:
        if any(term in lowered for term in terms):
            return label
    return "unknown"


def detail_text(item):
    parts = [
        item.get("Property", ""),
        item.get("Description", ""),
        item.get("ProductInfo", ""),
        json.dumps(item.get("Feature", ""), ensure_ascii=False),
    ]
    return " ".join(str(part) for part in parts if part)


def text_material(text):
    rules = [
        ("beeswax", ["beeswax", "bee wax", "beewax", "waxed"]),
        ("linen", ["linen"]),
        ("cotton", ["cotton", "canvas"]),
        ("paper", ["paper", "kraft"]),
        ("plastic", ["plastic", "poly", "polyethylene", "opp", "cellophane"]),
        ("silicone", ["silicone"]),
        ("wood/bamboo", ["wood", "bamboo"]),
        ("stainless steel", ["stainless steel"]),
    ]
    return first_matching_label(text, rules)


def material_with_evidence(title, details):
    title_lower = str(title).lower()
    detail_lower = str(details).lower()
    candidates = {
        "beeswax": ["beeswax", "bee wax", "beewax", "waxed"],
        "linen": ["linen"],
        "cotton": ["cotton", "canvas"],
        "paper": ["paper", "kraft"],
        "plastic": ["plastic", "poly", "polyethylene", "opp", "cellophane"],
        "silicone": ["silicone"],
        "wood/bamboo": ["wood", "bamboo"],
        "stainless steel": ["stainless steel"],
        "brass": ["brass", "nickel plated"],
        "metal": ["metal", "zinc", "aluminum", "aluminium"],
    }
    scores = Counter()
    evidence = defaultdict(list)
    for label, terms in candidates.items():
        for term in terms:
            if term in title_lower:
                scores[label] += 3
                evidence[label].append(f"title:{term}")
            if term in detail_lower:
                scores[label] += 2
                evidence[label].append(f"detail:{term}")
    if not scores:
        return "unknown", "", "unknown", ""
    material_label, _ = scores.most_common(1)[0]
    detail_label = text_material(details)
    return material_label, "; ".join(evidence[material_label][:6]), detail_label, "; ".join(evidence.get(detail_label, [])[:6])


def product_form(title):
    rules = [
        ("4-way hose splitter", ["4 way hose splitter", "4-way hose splitter", "four way hose splitter"]),
        ("3-way hose splitter", ["3 way hose splitter", "3-way hose splitter", "3 way garden hose splitter", "3-way garden hose splitter"]),
        ("2-way hose splitter", ["2 way hose splitter", "2-way hose splitter", "garden y hose splitter", "y hose splitter"]),
        ("hose splitter manifold", ["hose manifold", "water hose manifold", "outlet manifold"]),
        ("garden hose splitter", ["hose splitter", "spigot splitter", "faucet splitter", "water hose splitter", "hose bib splitter"]),
        ("hose connector/adapter", ["hose connector", "hose adapter", "garden hose adapter", "hose quick connect"]),
        ("beeswax bread bag", ["beeswax bread bag", "beeswax linen", "beeswax sourdough"]),
        ("disposable bakery bread bag", ["clear window", "bakery paper", "paper packaging", "paper bags", "stickers"]),
        ("linen/cotton bread bag", ["linen bread bag", "cotton bread bag", "sourdough bag", "bread bag"]),
        ("plastic bread bag", ["plastic bread bag", "poly bread bag", "clear bread bag"]),
        ("bread box", ["bread box", "breadboxes"]),
        ("banneton/proofing", ["banneton", "proofing basket", "proofing baskets"]),
        ("beeswax wrap", ["beeswax wrap", "food wrap"]),
        ("bread storage adjacent", ["bread storage", "bread keeper", "bread container"]),
    ]
    return first_matching_label(title, rules)


def closure(title):
    rules = [
        ("zipper", ["zipper", "zip"]),
        ("drawstring", ["drawstring"]),
        ("roll top", ["roll top", "roll-top"]),
        ("lid", ["lid"]),
        ("none stated", ["bread bag", "wrap"]),
    ]
    return first_matching_label(title, rules)


def style(title):
    rules = [
        ("farmhouse/natural", ["farmhouse", "natural", "linen", "homesteading"]),
        ("patterned", ["pattern", "print", "striped", "checkered"]),
        ("clear/minimal", ["clear", "minimal"]),
        ("giftable", ["gift", "present"]),
        ("vintage", ["vintage", "retro"]),
    ]
    return first_matching_label(title, rules)


def use_case(title):
    rules = [
        ("sourdough", ["sourdough"]),
        ("homemade bread", ["homemade bread", "artisan bread"]),
        ("baguette", ["baguette"]),
        ("sandwich loaf", ["sandwich", "loaf"]),
        ("general kitchen storage", ["storage", "fresh"]),
    ]
    return first_matching_label(title, rules)


def feature_tags(title):
    text = str(title).lower()
    tags = []
    checks = {
        "reusable": ["reusable"],
        "washable": ["washable", "wash"],
        "organic": ["organic"],
        "breathable": ["breathable"],
        "keeps fresh": ["keep fresh", "keeps fresh", "fresh"],
        "freezer": ["freezer"],
        "extra large": ["xl", "extra large", "large"],
        "eco": ["eco", "sustainable"],
    }
    for tag, terms in checks.items():
        if any(term in text for term in terms):
            tags.append(tag)
    return "; ".join(tags)


def raw_product_to_row(seed_asin, source, item, candidate_context=None, rules=None):
    asin = pick(item, "asin", "")
    title = pick(item, "title", "")
    details = detail_text(item)
    material_label, material_evidence, detail_material, detail_evidence = material_with_evidence(title, details)
    monthly_sales = pick(item, "sales")
    if monthly_sales in (None, ""):
        monthly_sales = item.get("AsinSalesCount")
    row = {
        "source_asin": seed_asin,
        "source": source,
        "listing_url": amazon_listing_url(asin),
        "product_type": item.get("ProductType", ""),
        "relevance_score": 100 if asin == seed_asin else 0,
        "competitor_type": "seed" if asin == seed_asin else "",
        "product_form": product_form(title),
        "material": material_label,
        "material_evidence": material_evidence,
        "detail_material": detail_material,
        "detail_evidence": detail_evidence,
        "pack_count": pack_count(title),
        "closure": closure(title),
        "style": style(title),
        "use_case": use_case(title),
        "feature_tags": feature_tags(title),
        "asin": asin,
        "parent_asin": pick(item, "parent_asin", ""),
        "title": title,
        "brand": pick(item, "brand", ""),
        "price": round(to_money(pick(item, "price"), 0), 2),
        "monthly_sales": round(to_float(monthly_sales, 0), 0),
        "reviews": round(to_float(pick(item, "reviews"), 0), 0),
        "rating": round(to_float(pick(item, "rating"), 0), 1),
        "seller_address": item.get("BuyboxSellerAddress") or "",
        "is_fba": bool(item.get("IsFBA")),
        "variation_count": round(to_float(item.get("VariationASINCount"), 0), 0),
        "bsr_category": bsr_category_text(item),
        "main_image_url": product_photo(item),
        "image_file": "",
        "visual_product_form": "",
        "visual_material_signal": "",
        "visual_pack_count": "",
        "visual_closure": "",
        "visual_style": "",
        "visual_notes": "",
    }
    if candidate_context and rules and asin != seed_asin:
        comp_row = product_to_competitor(candidate_context, item)
        comp_row["keyword_evidence_count"] = row.get("keyword_evidence_count", 0)
        classify_competitor(candidate_context, comp_row, rules)
        row["relevance_score"] = comp_row.get("relevance_score", 0)
        row["competitor_type"] = comp_row.get("competitor_type", "")
    return row


def build_product_rows(seed_product, competitor_records, evidence_by_asin, rules):
    seed_asin = pick(seed_product, "asin", "")
    candidate_context = {
        "source_asin": seed_asin,
        "source_parent_asin": pick(seed_product, "parent_asin", ""),
        "product_name": pick(seed_product, "title", ""),
        "target_price": to_money(pick(seed_product, "price"), 0),
        "category": pick(seed_product, "category", ""),
    }
    rows = [raw_product_to_row(seed_asin, "seed", seed_product)]
    for item in competitor_records:
        asin = pick(item, "asin", "")
        row = raw_product_to_row(seed_asin, "keyword/category", item)
        comp_row = product_to_competitor(candidate_context, item)
        enrich_competitor_evidence(comp_row, evidence_by_asin)
        classify_competitor(candidate_context, comp_row, rules)
        row["relevance_score"] = comp_row.get("relevance_score", 0)
        row["competitor_type"] = comp_row.get("competitor_type", "")
        if evidence_by_asin.get(asin):
            row["source"] = "keyword"
        rows.append(row)
    rows = dedupe_product_rows(rows)
    rows.sort(key=lambda item: (item["competitor_type"] != "seed", -to_float(item["relevance_score"], 0), -to_float(item["monthly_sales"], 0)))
    return rows[: int(rules["competitors_to_analyze"]) + 1]


def dedupe_product_rows(rows):
    seen = set()
    unique = []
    for row in rows:
        key = row.get("parent_asin") or row.get("asin")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def build_form_rows(product_rows, rules):
    thresholds = rules["opportunity_thresholds"]
    relevant = [row for row in product_rows if row.get("competitor_type") in {"seed", "direct", "keyword"}]
    by_form = defaultdict(list)
    for row in relevant:
        by_form[row["product_form"]].append(row)

    rows = []
    for form, items in by_form.items():
        prices = [to_float(item["price"], 0) for item in items if to_float(item["price"], 0) > 0]
        sales = [to_float(item["monthly_sales"], 0) for item in items]
        reviews = [to_float(item["reviews"], 0) for item in items]
        ratings = [to_float(item["rating"], 0) for item in items if to_float(item["rating"], 0) > 0]
        low_review_high_sales = [
            item
            for item in items
            if to_float(item["reviews"], 0) <= thresholds["low_reviews"]
            and to_float(item["monthly_sales"], 0) >= thresholds["high_sales"]
        ]
        row = {
            "product_form": form,
            "count": len(items),
            "direct_count": sum(1 for item in items if item["competitor_type"] in {"seed", "direct"}),
            "keyword_count": sum(1 for item in items if item["competitor_type"] == "keyword"),
            "avg_price": round(avg(prices), 2),
            "median_price": round(med(prices), 2),
            "avg_monthly_sales": round(avg(sales), 0),
            "median_reviews": round(med(reviews), 0),
            "avg_rating": round(avg(ratings), 1),
            "low_review_high_sales_count": len(low_review_high_sales),
            "top_materials": top_counts(item["material"] for item in items),
            "top_pack_counts": top_counts(str(item["pack_count"]) for item in items),
            "top_styles": top_counts(item["style"] for item in items),
            "opportunity_note": opportunity_note(form, items, thresholds),
        }
        rows.append(row)
    seed_form = next((row["product_form"] for row in product_rows if row.get("competitor_type") == "seed"), "")
    rows.sort(
        key=lambda item: (
            item["product_form"] != seed_form,
            -to_float(item["direct_count"], 0),
            item["product_form"] == "unknown",
            -to_float(item["avg_monthly_sales"], 0),
            to_float(item["median_reviews"], 0),
        )
    )
    return rows


def opportunity_note(form, items, thresholds):
    avg_sales = avg([to_float(item["monthly_sales"], 0) for item in items])
    median_reviews = med([to_float(item["reviews"], 0) for item in items])
    avg_rating = avg([to_float(item["rating"], 0) for item in items if to_float(item["rating"], 0) > 0])
    notes = []
    if len(items) <= thresholds["low_supply_count"] and avg_sales >= thresholds["high_sales"]:
        notes.append("供给少但需求可见")
    if median_reviews <= thresholds["low_reviews"] and avg_sales >= thresholds["high_sales"]:
        notes.append("评论门槛低")
    if avg_rating and avg_rating <= thresholds["rating_gap"] and avg_sales >= thresholds["high_sales"]:
        notes.append("评分改良空间")
    pack_counts = Counter(item["pack_count"] for item in items)
    top_pack, top_pack_count = pack_counts.most_common(1)[0] if pack_counts else (0, 0)
    top_pack_number = to_float(top_pack, 0)
    if top_pack_number >= 2 and top_pack_count >= max(2, len(items) // 2):
        notes.append(f"{top_pack}-pack 已是主流，需靠材质/清洗/尺寸差异化")
    elif any(to_float(pack, 0) >= 2 for pack in pack_counts) and pack_counts.get(1, 0) >= max(1, len(items) // 2):
        notes.append("多件套可能仍有切入空间")
    if not notes:
        notes.append("需要供应商和评论验证")
    return "；".join(notes)


def top_counts(values, limit=3):
    counts = Counter(value for value in values if value and value != "unknown")
    return "; ".join(f"{key}:{count}" for key, count in counts.most_common(limit))


def avg(values):
    values = [value for value in values if value is not None]
    return mean(values) if values else 0.0


def med(values):
    values = [value for value in values if value is not None]
    return median(values) if values else 0.0


def find_review_records(value):
    records = []
    walk_reviews(value, records)
    return records


def walk_reviews(value, records):
    if isinstance(value, list):
        dict_items = [item for item in value if isinstance(item, dict)]
        review_like = [item for item in dict_items if review_text(item)]
        if review_like:
            records.extend(review_like)
            return
        for item in value:
            walk_reviews(item, records)
    elif isinstance(value, dict):
        for item in value.values():
            walk_reviews(item, records)


def review_text(item):
    for key in ["review_text", "reviewText", "content", "Content", "body", "Body", "Text", "text"]:
        value = item.get(key)
        if value:
            return str(value)
    return ""


def review_title(item):
    for key in ["title", "Title", "review_title", "reviewTitle", "summary", "Summary"]:
        value = item.get(key)
        if value:
            return str(value)
    return ""


def review_rating(item):
    for key in ["rating", "Rating", "star", "Star", "stars", "Stars"]:
        value = item.get(key)
        if value:
            return to_float(value, 0)
    return 0


def collect_reviews(asins, rules, domain):
    cfg = rules.get("review_lookup", {})
    if not cfg.get("enabled", False):
        return []
    rows = []
    pages = int(cfg.get("pages", 1))
    max_asins = int(cfg.get("max_asins", len(asins)))
    attempted = 0
    for asin in asins:
        if attempted >= max_asins:
            break
        attempted += 1
        for page in range(1, pages + 1):
            payload = render_template(cfg["payload_template"], {"asin": asin, "page": page})
            response = call_sorftime(cfg["method"], json.dumps(payload, ensure_ascii=False), domain)
            for item in find_review_records(response):
                text = review_text(item)
                review_asin = str(item.get("Asin") or item.get("asin") or asin)
                rows.append(
                    {
                        "review_target_asin": asin,
                        "asin": review_asin,
                        "listing_url": amazon_listing_url(review_asin),
                        "rating": review_rating(item),
                        "review_title": review_title(item),
                        "review_text": text,
                        "review_date": item.get("ReviewsDate")
                        or item.get("reviewsDate")
                        or item.get("date")
                        or item.get("Date")
                        or item.get("reviewDate")
                        or "",
                        "pain_point_tags": pain_tags(text, rules),
                        "review_link": item.get("ReviewsLink") or item.get("reviewsLink") or "",
                        "verified_purchase": item.get("IsVP") if item.get("IsVP") is not None else item.get("isVP", ""),
                        "asin_property": item.get("AsinProperty") or item.get("asinProperty") or "",
                        "helpful": item.get("Helpful") or item.get("helpful") or 0,
                    }
                )
    return rows


def build_review_targets(product_rows, max_review_asins):
    if max_review_asins <= 0:
        return []

    relevant_rows = [
        row
        for row in product_rows
        if row.get("competitor_type") in {"seed", "direct"} and row.get("product_form") != "unknown"
    ]
    secondary_rows = [
        row
        for row in product_rows
        if row.get("competitor_type") == "keyword" and row.get("product_form") != "unknown"
    ]
    seed = next((row for row in relevant_rows if row.get("competitor_type") == "seed"), None)
    seed_form = seed.get("product_form") if seed else ""

    selected = []
    seen = set()

    def add_target(row):
        asin = row.get("asin")
        if not asin or asin in seen or len(selected) >= max_review_asins:
            return
        seen.add(asin)
        selected.append(row)

    if seed:
        add_target(seed)

    by_form = defaultdict(list)
    for row in relevant_rows + secondary_rows:
        by_form[row.get("product_form", "")].append(row)

    # Give each adjacent product form at least one representative before filling the
    # remaining quota with closest direct competitors from the seed form.
    form_candidates = []
    for form, rows in by_form.items():
        if not form or form == seed_form:
            continue
        representative = max(
            rows,
            key=lambda row: (
                row.get("competitor_type") == "direct",
                to_float(row.get("monthly_sales"), 0),
                to_float(row.get("relevance_score"), 0),
                -to_float(row.get("reviews"), 0),
            ),
        )
        form_candidates.append((form, representative))
    form_candidates.sort(
        key=lambda item: (
            -to_float(item[1].get("monthly_sales"), 0),
            -to_float(item[1].get("relevance_score"), 0),
            item[0],
        )
    )
    for _, row in form_candidates:
        add_target(row)

    for row in relevant_rows:
        if row.get("product_form") == seed_form:
            add_target(row)

    for row in secondary_rows:
        add_target(row)

    return [
        {
            "asin": row["asin"],
            "listing_url": row["listing_url"],
            "title": row["title"],
            "competitor_type": row["competitor_type"],
            "product_form": row["product_form"],
            "monthly_sales": row["monthly_sales"],
            "reviews": row["reviews"],
            "rating": row["rating"],
            "review_rows_collected": 0,
        }
        for row in selected
    ]


def attach_review_counts(review_targets, review_rows):
    counts = Counter(row.get("review_target_asin") or row.get("asin") for row in review_rows)
    for row in review_targets:
        row["review_rows_collected"] = counts.get(row["asin"], 0)


def pain_tags(text, rules):
    lowered = str(text).lower()
    tags = []
    for tag, terms in rules.get("pain_point_terms", {}).items():
        if any(term in lowered for term in terms):
            tags.append(tag)
    return "; ".join(tags)


def painpoint_hypotheses(product_rows, review_rows, rules):
    if review_rows:
        counts = Counter()
        for row in review_rows:
            for tag in row["pain_point_tags"].split(";"):
                tag = tag.strip()
                if tag:
                    counts[tag] += 1
        return [f"{tag}: {count} reviews" for tag, count in counts.most_common(8)]

    relevant_titles = " ".join(row["title"] for row in product_rows[:30]).lower()
    hypotheses = []
    if "beeswax" in relevant_titles:
        hypotheses.append("蜂蜡气味和清洗说明需要验证")
    if "sourdough" in relevant_titles or "loaf" in relevant_titles:
        hypotheses.append("尺寸是否适配圆形 sourdough 和高吐司")
    if "zip" in relevant_titles or "zipper" in relevant_titles:
        hypotheses.append("拉链耐用性、面包屑外漏")
    if "bread bag" in relevant_titles:
        hypotheses.append("保鲜平衡：透气 vs 面包变干")
    hypotheses.append("如果宣传 airtight，需验证发霉/潮气风险")
    hypotheses.append("2-pack 已常见，3-pack/礼品化是否能支撑溢价")
    return hypotheses


def build_recommendations(seed_product, form_rows, product_rows, review_rows, rules):
    recommendations = []
    seed_form = next((row["product_form"] for row in product_rows if row.get("competitor_type") == "seed"), "")
    seed_form_row = next((row for row in form_rows if row["product_form"] == seed_form), None)
    if seed_form_row:
        recommendations.append(
            f"主线优先研究 {seed_form_row['product_form']}：直接/同形态样本 {seed_form_row['direct_count']} 个，评论中位数 {seed_form_row['median_reviews']}，月销均值 {seed_form_row['avg_monthly_sales']}。"
        )
    if form_rows:
        best = max(form_rows, key=lambda row: to_float(row["avg_monthly_sales"], 0))
        if best["product_form"] != seed_form:
            recommendations.append(
                f"相邻高需求形态是 {best['product_form']}，月销均值 {best['avg_monthly_sales']}，但它不是同款竞品，适合参考包装/关键词，不应直接替代目标产品。"
            )
    multi_pack_forms = [row for row in form_rows if "多件套" in row["opportunity_note"] or "已是主流" in row["opportunity_note"]]
    if multi_pack_forms:
        recommendations.append(
            f"套装方向：{multi_pack_forms[0]['product_form']} 里 {multi_pack_forms[0]['top_pack_counts']}，不要只做普通 2-pack，优先验证 2-size combo、3-pack 或 gift set。"
        )
    low_review_forms = [row for row in form_rows if "评论门槛低" in row["opportunity_note"]]
    if low_review_forms:
        recommendations.append(f"低评论切入：{low_review_forms[0]['product_form']} 仍有评论缺口，适合继续做供应商报价和差评验证。")
    recommendations.append("供应商必须验证：蜂蜡涂层、棉麻比例、可清洗边界、食品接触合规、拉链/可拆内衬成本。")
    recommendations.append("图片队列用于下一步视觉聚类：套装数量、闭合方式、自然/farmhouse 风格、是否礼品化。")
    if not review_rows:
        recommendations.append("评论明细还没拉到，最终规格决策前必须补齐 Top ASIN 差评和好评扫描。")
    return recommendations


def prepare_image_assets(product_rows, output_dir, rules):
    cfg = rules.get("image_analysis", {})
    if not cfg.get("enabled", False):
        return [], ""
    image_dir = Path(output_dir) / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    existing_labels = load_existing_visual_labels(Path(output_dir) / "visual_labels.csv")
    top_n = int(cfg.get("top_n", 40))
    image_rows = []
    for index, row in enumerate(product_rows[:top_n], start=1):
        image_path = image_dir / f"{index:02d}_{safe_filename(row['asin'])}.jpg"
        if row.get("main_image_url"):
            download_image(row["main_image_url"], image_path)
        row["image_file"] = str(image_path) if image_path.exists() else ""
        saved = existing_labels.get(row["asin"], {})
        for field in [
            "visual_product_form",
            "visual_material_signal",
            "visual_pack_count",
            "visual_closure",
            "visual_style",
            "visual_notes",
        ]:
            row[field] = saved.get(field, row.get(field, ""))
        image_rows.append(
            {
                "image_index": index,
                "asin": row["asin"],
                "listing_url": row["listing_url"],
                "title": row["title"],
                "product_form": row["product_form"],
                "material": row["material"],
                "pack_count": row["pack_count"],
                "main_image_url": row["main_image_url"],
                "image_file": row["image_file"],
                "visual_product_form": row["visual_product_form"],
                "visual_material_signal": row["visual_material_signal"],
                "visual_pack_count": row["visual_pack_count"],
                "visual_closure": row["visual_closure"],
                "visual_style": row["visual_style"],
                "visual_notes": row["visual_notes"],
            }
        )
    contact_sheet = Path(output_dir) / "image_contact_sheet.jpg"
    make_contact_sheet(image_rows, contact_sheet, int(cfg.get("tile_size", 220)), int(cfg.get("columns", 4)))
    return image_rows, str(contact_sheet) if contact_sheet.exists() else ""


def load_existing_visual_labels(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {row.get("asin", ""): row for row in rows if row.get("asin")}


def download_image(url, path):
    if path.exists():
        return
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read()
        path.write_bytes(data)
    except Exception:
        return


def make_contact_sheet(image_rows, output_path, tile_size, columns):
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return
    rows = (len(image_rows) + columns - 1) // columns
    if rows <= 0:
        return
    label_height = 48
    sheet = Image.new("RGB", (columns * tile_size, rows * (tile_size + label_height)), "white")
    draw = ImageDraw.Draw(sheet)
    for idx, row in enumerate(image_rows):
        x = (idx % columns) * tile_size
        y = (idx // columns) * (tile_size + label_height)
        image_path = row.get("image_file")
        if image_path and Path(image_path).exists():
            try:
                image = Image.open(image_path).convert("RGB")
                image.thumbnail((tile_size - 12, tile_size - 12))
                ix = x + (tile_size - image.width) // 2
                iy = y + (tile_size - image.height) // 2
                sheet.paste(image, (ix, iy))
            except Exception:
                pass
        label = f"{int(row['image_index']):02d} {row['asin']} | {row['product_form']} | {row['pack_count']}p"
        draw.text((x + 6, y + tile_size + 6), label[:42], fill="black")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=92)


def safe_filename(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or ""))[:80]


def visual_summary(image_rows):
    labeled = [row for row in image_rows if row.get("visual_product_form")]
    if not labeled:
        return []
    return [
        f"已视觉标注 {len(labeled)} 张主图",
        f"视觉形态：{top_counts((row.get('visual_product_form') for row in labeled), 6)}",
        f"视觉材质信号：{top_counts((row.get('visual_material_signal') for row in labeled), 6)}",
        f"视觉套装数量：{top_counts((str(row.get('visual_pack_count')) for row in labeled), 6)}",
        f"视觉闭合方式：{top_counts((row.get('visual_closure') for row in labeled), 6)}",
        f"视觉风格：{top_counts((row.get('visual_style') for row in labeled), 6)}",
    ]


def write_report(
    path,
    seed_product,
    keyword_rows,
    product_rows,
    form_rows,
    review_rows,
    recommendations,
    rules,
    image_rows=None,
    review_targets=None,
):
    seed_asin = pick(seed_product, "asin", "")
    review_targets = review_targets or []
    covered_review_targets = [row for row in review_targets if to_float(row.get("review_rows_collected"), 0) > 0]
    lines = [
        "# 单品机会深度研究",
        "",
        f"- 目标 ASIN：[{seed_asin}]({amazon_listing_url(seed_asin)})",
        f"- 产品：{pick(seed_product, 'title', '')}",
        f"- 竞品池产品数：{max(0, len(product_rows) - 1)}",
        f"- 关键词数：{len(keyword_rows)}",
        f"- 评论明细数：{len(review_rows)}",
        f"- 评论覆盖：{len(covered_review_targets)}/{len(review_targets)} 个目标 ASIN",
        "- 图片识别：已生成主图 contact sheet 和视觉标签表；材质字段已结合标题、属性、描述、详情页文本判断",
        "",
        "## 形态结构",
        "",
        "| 产品形态 | 数量 | 直接/关键词 | 均价 | 月销均值 | 评论中位数 | 评分 | 材质 | 套装 | 机会备注 |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in form_rows[:12]:
        lines.append(
            "| {form} | {count} | {direct}/{keyword} | ${price} | {sales} | {reviews} | {rating} | {materials} | {packs} | {note} |".format(
                form=escape_pipe(row["product_form"]),
                count=row["count"],
                direct=row["direct_count"],
                keyword=row["keyword_count"],
                price=row["avg_price"],
                sales=row["avg_monthly_sales"],
                reviews=row["median_reviews"],
                rating=row["avg_rating"],
                materials=escape_pipe(row["top_materials"]),
                packs=escape_pipe(row["top_pack_counts"]),
                note=escape_pipe(row["opportunity_note"]),
            )
        )

    lines.extend(["", "## 视觉识别摘要", ""])
    summary_lines = visual_summary(image_rows or [])
    if summary_lines:
        for item in summary_lines:
            lines.append(f"- {item}")
    else:
        lines.append("- 已生成图片队列，等待视觉标签回填。")

    lines.extend(["", "## 初步痛点", ""])
    for item in painpoint_hypotheses(product_rows, review_rows, rules):
        lines.append(f"- {item}")

    if review_targets:
        lines.extend(["", "## 评论覆盖 ASIN", ""])
        lines.extend(
            [
                "| ASIN | 类型 | 形态 | 月销 | Listing评论 | 已读评论 |",
                "| --- | --- | --- | ---: | ---: | ---: |",
            ]
        )
        for row in review_targets:
            lines.append(
                "| [{asin}]({url}) | {ctype} | {form} | {sales} | {reviews} | {read} |".format(
                    asin=row["asin"],
                    url=row["listing_url"],
                    ctype=row["competitor_type"],
                    form=escape_pipe(row["product_form"]),
                    sales=row["monthly_sales"],
                    reviews=row["reviews"],
                    read=row["review_rows_collected"],
                )
            )

    lines.extend(["", "## 切入建议", ""])
    for item in recommendations:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## 下一步",
            "",
            "1. 对未返回评论的 ASIN 判断是否需要启动 Sorftime ProductReviewsCollection 实时采集。",
            "2. 对图片队列做视觉聚类，确认图案、材质、套装数量、包装形态。",
            "3. 找 3-5 个供应商验证材质、尺寸、清洗方式、食品接触合规和打样成本。",
            "4. 根据评论痛点反推产品规格，再进入素材和 listing 自动化。",
            "",
        ]
    )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def escape_pipe(value):
    return str(value).replace("|", "\\|")


def main():
    args = parse_args()
    rules = load_json(args.rules)
    asin = args.asin or rules["target_asin"]
    domain = args.domain or rules["domain"]
    output_dir = Path(args.output_dir or f"research/{asin}")
    report_path = Path(args.report or f"reports/product_opportunity_research_{asin}.md")

    seed_product = fetch_seed_product(asin, rules, domain)
    keyword_records = fetch_keywords(asin, rules, domain)
    keyword_rows = [keyword_to_row(item) for item in keyword_records if get_keyword_text(item)][: int(rules["keywords_per_product"])]

    evidence = collect_competitor_evidence(keyword_records, {"source_asin": asin, "source_parent_asin": pick(seed_product, "parent_asin", "")})
    category_asins = fetch_category_asins(seed_product, rules, domain)
    asin_pool = [item["asin"] for item in evidence]
    asin_pool.extend(category_asins)
    asin_pool = dedupe_asins(asin_pool, exclude={asin})[: int(rules["competitor_candidate_pool_size"])]
    evidence_by_asin = {item["asin"]: item for item in evidence}

    competitor_records = fetch_product_details(asin_pool, rules, domain)
    product_rows = build_product_rows(seed_product, competitor_records, evidence_by_asin, rules)
    form_rows = build_form_rows(product_rows, rules)
    max_review_asins = int(rules.get("review_lookup", {}).get("max_asins", 10))
    review_targets = build_review_targets(product_rows, max_review_asins)
    review_asins = [row["asin"] for row in review_targets]
    review_rows = collect_reviews(review_asins, rules, domain)
    attach_review_counts(review_targets, review_rows)
    recommendations = build_recommendations(seed_product, form_rows, product_rows, review_rows, rules)

    image_rows, contact_sheet = prepare_image_assets(product_rows, output_dir, rules)

    write_csv(output_dir / "top_products.csv", product_rows, PRODUCT_FIELDS)
    write_csv(output_dir / "product_forms.csv", form_rows, FORM_FIELDS)
    write_csv(output_dir / "keywords.csv", keyword_rows, KEYWORD_FIELDS)
    write_csv(output_dir / "reviews.csv", review_rows, REVIEW_FIELDS)
    write_csv(output_dir / "review_targets.csv", review_targets, REVIEW_TARGET_FIELDS)
    write_csv(output_dir / "image_review_queue.csv", image_rows, IMAGE_FIELDS)
    write_csv(output_dir / "visual_labels.csv", image_rows, IMAGE_FIELDS)
    write_report(
        report_path,
        seed_product,
        keyword_rows,
        product_rows,
        form_rows,
        review_rows,
        recommendations,
        rules,
        image_rows,
        review_targets,
    )

    print(f"Research products: {output_dir / 'top_products.csv'}")
    print(f"Product forms: {output_dir / 'product_forms.csv'}")
    print(f"Keywords: {output_dir / 'keywords.csv'}")
    print(f"Reviews: {output_dir / 'reviews.csv'}")
    print(f"Review targets: {output_dir / 'review_targets.csv'}")
    print(f"Image queue: {output_dir / 'image_review_queue.csv'}")
    print(f"Visual labels: {output_dir / 'visual_labels.csv'}")
    if contact_sheet:
        print(f"Contact sheet: {contact_sheet}")
    print(f"Report: {report_path}")


def dedupe_asins(asins, exclude=None):
    exclude = exclude or set()
    seen = set()
    unique = []
    for asin in asins:
        asin = str(asin or "").strip()
        if not asin or asin in exclude or asin in seen:
            continue
        seen.add(asin)
        unique.append(asin)
    return unique


if __name__ == "__main__":
    main()
