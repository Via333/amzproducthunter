#!/usr/bin/env python3
"""Build one archived product research dataset from Sorftime MCP and cached Top100 data."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from business_feasibility import write_business_feasibility
from category_shape_validation import product_form_from_category_product
from demand_analysis import DEMAND_FIELDS, analyze_demand
from market_structure import analyze_market
from product_opportunity_research import (
    FORM_FIELDS,
    IMAGE_FIELDS,
    KEYWORD_FIELDS,
    PRODUCT_FIELDS,
    REVIEW_FIELDS,
    REVIEW_TARGET_FIELDS,
    apply_visual_labels,
    attach_review_counts,
    build_form_rows,
    closure,
    feature_tags,
    material_with_evidence,
    pack_count,
    prepare_image_assets,
    style,
    use_case,
    write_csv,
)
from sorftime_mcp_client import SorftimeMcpClient


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one product research run with Sorftime MCP.")
    parser.add_argument("--asin", required=True)
    parser.add_argument("--site", default="US")
    parser.add_argument("--rules", default="config/opportunity_research_rules.json")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--review-limit", type=int, default=10)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_data(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", "").replace("$", "").strip() or default)
    except (TypeError, ValueError):
        return default


def embedded_number(value: Any, *, percent: bool = False) -> float:
    matches = re.findall(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    if not matches:
        return 0.0
    number = float(matches[-1])
    return number / 100 if percent else number


def first_image(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        images = json.loads(text)
    except json.JSONDecodeError:
        return text
    return str(images[0]) if isinstance(images, list) and images else ""


def latest_cached_category_report(node_id: str) -> Path | None:
    matches = list(ROOT.glob(f"archive/discovery_runs/*/raw_category_reports/{node_id}.json"))
    matches.extend(ROOT.glob(f"archive/cache/category_reports/{node_id}.json"))
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def category_report(client: SorftimeMcpClient, node_id: str, site: str) -> tuple[dict[str, Any], Path]:
    cached = latest_cached_category_report(node_id)
    if cached:
        return load_json(cached), cached
    result = client.call_tool("category_report", {"node_id": node_id, "amz_site": site})
    report = result if isinstance(result, dict) else {"data": result}
    cache_path = ROOT / "archive" / "cache" / "category_reports" / f"{node_id}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, cache_path


def deep_product_form(product: dict[str, Any]) -> str:
    title = str(product.get("title") or "").lower()
    brand = str(product.get("brand") or "").lower()
    broad_form = product_form_from_category_product(product)
    if broad_form != "duster refill":
        return broad_form
    if "reusable" in title or "washable" in title:
        return "reusable duster refill"
    if any(term in title for term in ("handle", "extension pole", "extendable", "starter kit")):
        return "duster refill kit"
    if brand == "swiffer":
        return "brand duster refill"
    return "compatible disposable refill"


def raw_row(seed_asin: str, product: dict[str, Any], category_name: str) -> dict[str, Any]:
    asin = str(product.get("asin") or "")
    title = str(product.get("title") or "")
    form = deep_product_form(product)
    material, material_evidence, detail_material, detail_evidence = material_with_evidence(title, "")
    if "microfiber" in title.lower() or "microfibre" in title.lower():
        material = "microfiber"
        material_evidence = "title:microfiber"
    return {
        "source_asin": seed_asin,
        "source": "category_top100",
        "listing_url": f"https://www.amazon.com/dp/{asin}",
        "product_type": product.get("product_category", ""),
        "relevance_score": 100 if asin == seed_asin else (90 if form == "compatible disposable refill" else 65),
        "competitor_type": "seed" if asin == seed_asin else ("direct" if form == "compatible disposable refill" else "keyword"),
        "product_form": form,
        "material": material,
        "material_evidence": material_evidence,
        "detail_material": detail_material,
        "detail_evidence": detail_evidence,
        "pack_count": pack_count(title),
        "closure": closure(title),
        "style": style(title),
        "use_case": use_case(title),
        "feature_tags": feature_tags(title),
        "asin": asin,
        "parent_asin": "",
        "title": title,
        "brand": product.get("brand", ""),
        "price": round(to_float(product.get("price")), 2),
        "monthly_sales": round(to_float(product.get("monthly_sales_volume"))),
        "reviews": round(to_float(product.get("review_count"))),
        "rating": round(to_float(product.get("star_rating")), 1),
        "seller_address": product.get("seller_origin", ""),
        "is_fba": "FBA" in str(product.get("delivery_type") or ""),
        "variation_count": 0,
        "listing_date": product.get("online_date", ""),
        "bsr_category": f"{category_name} / {product.get('category_rank', '')}",
        "main_image_url": "",
        "image_file": "",
        "visual_product_form": "",
        "visual_material_signal": "",
        "visual_pack_count": "",
        "visual_closure": "",
        "visual_style": "",
        "visual_notes": "",
    }


def enrich_row(row: dict[str, Any], detail: dict[str, Any]) -> None:
    if not detail:
        return
    row["parent_asin"] = detail.get("parent_asin", row.get("parent_asin", ""))
    row["main_image_url"] = first_image(detail.get("main_image"))
    row["variation_count"] = detail.get("variation_count", row.get("variation_count", 0))
    row["listing_date"] = detail.get("online_date", row.get("listing_date", ""))
    row["is_fba"] = str(detail.get("delivery_type", "")).upper().endswith("FBA")
    row["seller_address"] = detail.get("seller_name", row.get("seller_address", ""))
    if row.get("asin") == detail.get("asin"):
        row["price"] = detail.get("price", row.get("price", 0))
        row["monthly_sales"] = detail.get("monthly_sales_volume", row.get("monthly_sales", 0))
        row["reviews"] = detail.get("review_count", row.get("reviews", 0))
        row["rating"] = detail.get("star_rating", row.get("rating", 0))


def select_review_rows(rows: list[dict[str, Any]], seed_asin: str, limit: int) -> list[dict[str, Any]]:
    seed = next(row for row in rows if row.get("asin") == seed_asin)
    direct = [row for row in rows if row.get("competitor_type") == "direct" and row.get("asin") != seed_asin]
    refill_adjacent = [
        row
        for row in rows
        if row.get("product_form") in {"brand duster refill", "duster refill kit", "reusable duster refill"}
    ]
    selected = [seed]
    seen = {seed_asin}

    def add(row: dict[str, Any] | None) -> None:
        if row and row.get("asin") not in seen and len(selected) < limit:
            selected.append(row)
            seen.add(str(row.get("asin")))

    add(max(direct, key=lambda row: to_float(row.get("monthly_sales")), default=None))
    add(min((row for row in direct if to_float(row.get("monthly_sales")) >= 500), key=lambda row: to_float(row.get("reviews")), default=None))
    seed_pack = to_float(seed.get("pack_count"))
    add(min(direct, key=lambda row: (abs(to_float(row.get("pack_count")) - seed_pack), -to_float(row.get("monthly_sales"))), default=None))
    add(min(direct, key=lambda row: (to_float(row.get("rating"), 5), -to_float(row.get("monthly_sales"))), default=None))
    for form in ("brand duster refill", "duster refill kit", "reusable duster refill"):
        add(max((row for row in refill_adjacent if row.get("product_form") == form), key=lambda row: to_float(row.get("monthly_sales")), default=None))
    for row in sorted(direct, key=lambda item: (-to_float(item.get("monthly_sales")), to_float(item.get("reviews")))):
        add(row)
    return selected[:limit]


def review_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        for row in rows
    ]


def review_pain_tags(text: str) -> str:
    lowered = text.lower()
    definitions = {
        "dust_retention": ["dust", "trap", "attract", "lock in"],
        "fit": ["fit", "handle", "secure", "lock"],
        "fiber_shedding": ["shed", "fiber", "fall apart", "fluffy"],
        "value": ["value", "price", "cheap", "expensive", "affordable"],
        "durability": ["durable", "tear", "rip", "quality"],
        "scent": ["scent", "smell", "unscented", "odor"],
    }
    return "; ".join(label for label, terms in definitions.items() if any(term in lowered for term in terms))


def collect_reviews(client: SorftimeMcpClient, rows: list[dict[str, Any]], site: str) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    form_by_asin = {str(row["asin"]): str(row["product_form"]) for row in rows}
    for target in rows:
        asin = str(target["asin"])
        result = client.call_tool("product_reviews", {"asin": asin, "amz_site": site, "review_type": "Both"})
        items = as_data(result)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            rating = to_float(item.get("star_rating"))
            collected.append(
                {
                    "review_target_asin": asin,
                    "asin": asin,
                    "listing_url": f"https://www.amazon.com/dp/{asin}",
                    "rating": rating,
                    "review_title": item.get("title", ""),
                    "review_text": content,
                    "review_date": item.get("review_date", ""),
                    "asin_property": item.get("variant_attribute", ""),
                    "review_link": "",
                    "pain_point_tags": review_pain_tags(f"{item.get('title', '')} {content}"),
                    "verified_purchase": "",
                    "helpful": 0,
                }
            )
    return collected


def keyword_rows(items: Any) -> list[dict[str, Any]]:
    rows = []
    for rank, item in enumerate(items if isinstance(items, list) else [], start=1):
        rows.append(
            {
                "keyword": item.get("keyword", ""),
                "search_volume": item.get("monthly_search_volume", 0),
                "rank": rank,
                "show_share": item.get("exposure_position", ""),
                "clicks_90d": "",
                "top3_asins": item.get("latest_organic_position", ""),
                "image_asin_count": item.get("recommended_bid", ""),
            }
        )
    return rows


def parse_month_values(items: Any) -> list[dict[str, Any]]:
    parsed = []
    for item in items if isinstance(items, list) else []:
        match = re.match(r"(.+?)=(.+)", str(item))
        if match:
            parsed.append({"month": match.group(1), "value": to_float(match.group(2))})
    return parsed


def shape_snapshot(form_rows: list[dict[str, Any]], seed_form: str) -> dict[str, Any]:
    return next((row for row in form_rows if row.get("product_form") == seed_form), {})


def relativize_image_paths(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        path_text = str(row.get("image_file") or "")
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_absolute():
            continue
        try:
            row["image_file"] = str(path.relative_to(ROOT))
        except ValueError:
            pass


def broad_refill_snapshot(raw_products: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in raw_products if product_form_from_category_product(row) == "duster refill"]
    sales = [to_float(row.get("monthly_sales_volume")) for row in rows]
    reviews = [to_float(row.get("review_count")) for row in rows]
    total_category_sales = sum(to_float(row.get("monthly_sales_volume")) for row in raw_products)
    return {
        "name": "duster refill",
        "count": len(rows),
        "total_monthly_sales": round(sum(sales)),
        "sales_share": round(sum(sales) / total_category_sales, 4) if total_category_sales else 0,
        "median_reviews": round(median(reviews)) if reviews else 0,
        "low_review_high_sales_count": sum(
            1
            for row in rows
            if to_float(row.get("review_count")) <= 300 and to_float(row.get("monthly_sales_volume")) >= 500
        ),
    }


def make_summary(
    seed: dict[str, Any],
    detail: dict[str, Any],
    category_stats: dict[str, Any],
    seed_shape: dict[str, Any],
    trends: dict[str, Any],
    variations: list[dict[str, Any]],
    customers_say: dict[str, Any],
    keywords: list[dict[str, Any]],
    raw_products: list[dict[str, Any]],
    category_source: str,
    tool_calls: int,
) -> dict[str, Any]:
    price = to_float(seed.get("price"))
    fba_fee = to_float(detail.get("fba_fee"))
    referral_fee = price * 0.15
    ad_rate = 0.15
    target_margin = 0.20
    max_landed_cost = max(0.0, price - fba_fee - referral_fee - price * ad_rate - price * target_margin)
    details = customers_say.get("details") if isinstance(customers_say, dict) else []
    topic_map = {str(item.get("keyword")): item for item in details or [] if isinstance(item, dict)}
    trademark_terms = [row for row in keywords if "swiffer" in str(row.get("keyword", "")).lower()]
    seed_pack = pack_count(str(seed.get("title") or ""))
    same_pack = [
        row
        for row in raw_products
        if str(row.get("asin") or "") != str(seed.get("asin") or "")
        and pack_count(str(row.get("title") or "")) == seed_pack
        and deep_product_form(row) == "compatible disposable refill"
    ]
    benchmark = max(same_pack, key=lambda row: to_float(row.get("monthly_sales_volume")), default={})
    if not benchmark:
        same_pack = [
            row
            for row in raw_products
            if str(row.get("asin") or "") != str(seed.get("asin") or "")
            and pack_count(str(row.get("title") or "")) == seed_pack
            and product_form_from_category_product(row) == "duster refill"
        ]
        benchmark = max(same_pack, key=lambda row: to_float(row.get("monthly_sales_volume")), default={})
    broad_shape = broad_refill_snapshot(raw_products)
    monthly_sales = parse_month_values(trends.get("sales", []))
    prices = parse_month_values(trends.get("price", []))
    growth = 0.0
    if len(monthly_sales) >= 2 and monthly_sales[-2]["value"]:
        growth = monthly_sales[-1]["value"] / monthly_sales[-2]["value"] - 1
    volume = 1.0
    dimensions = [to_float(value) for value in str(detail.get("package_size_cm") or "").split("*") if value]
    if len(dimensions) == 3:
        volume = dimensions[0] * dimensions[1] * dimensions[2] / 1000
    score = 50
    score += 10 if to_float(broad_shape.get("total_monthly_sales")) >= 30000 else 0
    score += 8 if to_float(broad_shape.get("low_review_high_sales_count")) >= 3 else 0
    score += 8 if to_float(seed.get("monthly_sales")) >= 1000 else 0
    score += 5 if to_float(seed.get("rating")) >= 4.3 else 0
    score -= 8 if fba_fee / price >= 0.30 else 0
    score -= 8 if embedded_number(category_stats.get("top3_brands_sales_volume_share"), percent=True) >= 0.60 else 0
    score -= 5 if len(trademark_terms) >= 8 else 0
    score = max(0, min(100, score))
    return {
        "generated_on": date.today().isoformat(),
        "decision": "有条件进入供应商验证，不直接立项",
        "decision_score": score,
        "decision_reason": "需求、增长和低评高销样本成立，但利润受包装体积/FBA 费影响，且流量高度依赖 Swiffer 兼容词。",
        "product": {
            "asin": seed.get("asin"),
            "title": seed.get("title"),
            "price": price,
            "monthly_sales": seed.get("monthly_sales"),
            "reviews": seed.get("reviews"),
            "rating": seed.get("rating"),
            "fba_fee": fba_fee,
            "fba_fee_share": fba_fee / price if price else 0,
            "package_size_cm": detail.get("package_size_cm", ""),
            "package_volume_l": round(volume, 2),
            "weight_g": detail.get("weight_g", 0),
            "online_date": detail.get("online_date", ""),
            "variation_count": detail.get("variation_count", 0),
        },
        "shape": broad_shape,
        "exact_subform": {
            "name": seed.get("product_form"),
            "count": seed_shape.get("count", 0),
            "total_monthly_sales": seed_shape.get("total_monthly_sales", 0),
            "sales_share": seed_shape.get("sales_share", 0),
            "median_reviews": seed_shape.get("median_reviews", 0),
            "low_review_high_sales_count": seed_shape.get("low_review_high_sales_count", 0),
        },
        "same_pack_benchmark": {
            "asin": benchmark.get("asin", ""),
            "title": benchmark.get("title", ""),
            "price": benchmark.get("price", 0),
            "monthly_sales": benchmark.get("monthly_sales_volume", 0),
            "reviews": benchmark.get("review_count", 0),
            "fba_fee": benchmark.get("fba_fee", 0),
            "package_size": benchmark.get("package_size", ""),
        },
        "category": {
            "node_id": detail.get("node_id", ""),
            "top100_monthly_sales": embedded_number(category_stats.get("top100_monthly_sales_volume")),
            "average_price": embedded_number(category_stats.get("average_price")),
            "median_price": embedded_number(category_stats.get("median_price")),
            "first_brand": str(category_stats.get("first_brand", "")).split(":", 1)[-1].strip(),
            "first_brand_share": round(
                embedded_number(category_stats.get("first_brand_sales_volume"))
                / embedded_number(category_stats.get("top100_monthly_sales_volume")),
                4,
            ) if embedded_number(category_stats.get("top100_monthly_sales_volume")) else 0,
            "top3_brand_share": embedded_number(category_stats.get("top3_brands_sales_volume_share"), percent=True),
            "amazon_owned_share": embedded_number(category_stats.get("amazon_owned_sales_volume_share"), percent=True),
            "high_review_sales_share": embedded_number(category_stats.get("high_reviews_sales_volume_share"), percent=True),
            "return_rate": embedded_number(category_stats.get("return_rate"), percent=True),
        },
        "trend": {"monthly_sales": monthly_sales, "price": prices, "latest_mom_growth": round(growth, 4)},
        "variations": variations,
        "customer_voice": {"summary": customers_say.get("customer_say", ""), "topics": details or []},
        "unit_economics_gate": {
            "assumed_referral_rate": 0.15,
            "assumed_ad_rate": ad_rate,
            "target_contribution_margin": target_margin,
            "max_landed_cost": round(max_landed_cost, 2),
            "note": "未含退货、仓储和促销；供应商完税到仓成本超过此值时不建议进入。",
        },
        "signals": [
            f"新品从 2026-02 的 8 件爬升到最近 {monthly_sales[-1]['value']:.0f} 件/月。" if monthly_sales else "销量趋势待补。",
            f"duster refill 广义形态有 {int(to_float(broad_shape.get('count')))} 个 Top100 样本，形态总月销 {to_float(broad_shape.get('total_monthly_sales')):,.0f}。",
            f"广义形态低评高销样本 {int(to_float(broad_shape.get('low_review_high_sales_count')))} 个，说明并非只有老链接能卖。",
            f"80P 在当前父体可见变体销量中占主导。" if variations else "变体销量待补。",
            f"评论主题中 dust retention 正向 {topic_map.get('dust retention', {}).get('positive', 0)} / 负向 {topic_map.get('dust retention', {}).get('negative', 0)}。",
            f"同为 {seed_pack}P 的 {benchmark.get('brand', '竞品')} 月销 {to_float(benchmark.get('monthly_sales_volume')):,.0f}，说明大包装不是单链接偶然。" if benchmark else "同规格对标样本待补。",
        ],
        "risks": [
            f"FBA 费 ${fba_fee:.2f}，占售价 {fba_fee / price:.1%}；包装压缩直接决定利润。" if price else "FBA 费占比待补。",
            f"前 20 个流量词中 {len(trademark_terms)} 个含 Swiffer，存在商标依赖与广告合规复核要求。",
            "耗材形态同质化高，供应链和价格容易被复制，不能只靠大包装。",
            "Sorftime 评论接口对新链接返回正文有限，需用 10 个代表竞品的评论共同验证痛点。",
            f"同规格对标价 ${to_float(benchmark.get('price')):.2f}，比目标售价低 ${max(0.0, price - to_float(benchmark.get('price'))):.2f}，存在降价压力。" if benchmark else "同规格价格压力待补。",
        ],
        "entry_strategy": [
            "优先做 80P 核心款，并保留 40P 试用装、120P 家庭/商用补充装；先验证父体变体结构再开模备货。",
            "把防掉毛、静电吸附、插柄牢固度和不散尘作为硬指标，样品需与品牌原装做盲测。",
            "压缩包装厚度，目标把同为 80P 的 FBA 费从当前 $8.60 压到约 $7.20 水平。",
            "标题可做兼容性说明，但主品牌、包装视觉和广告素材不得让消费者误认为 Swiffer 原厂。",
            "增加清洁公司/办公室场景的耗材补充叙事，弱化纯低价竞争。",
        ],
        "validation_gates": [
            f"80P 完税到仓成本不高于 ${max_landed_cost:.2f}（按 15% 广告费和 20% 贡献毛利倒推）。",
            "完成至少 3 家供应商报价，确认单片克重、纤维密度、插柄尺寸公差和压缩包装尺寸。",
            "30 次装卸适配测试、10 次抖落测试和标准粉尘吸附对比中不得明显弱于主流竞品。",
            "由商标/平台合规人员审核 Compatible with Swiffer 的标题、包装和广告用法。",
            "若售价降到 $24.99 后仍无法满足利润门槛，则淘汰。",
        ],
        "provenance": {"category_source": category_source, "mcp_tool_calls": tool_calls},
    }


def write_report(path: Path, summary: dict[str, Any], forms: list[dict[str, Any]], targets: list[dict[str, Any]]) -> None:
    product = summary["product"]
    shape = summary["shape"]
    exact = summary["exact_subform"]
    unit = summary["unit_economics_gate"]
    lines = [
        f"# {product['asin']} 单品深度研究",
        "",
        f"**结论：{summary['decision']}（{summary['decision_score']}/100）**",
        "",
        summary["decision_reason"],
        "",
        "## 产品快照",
        "",
        f"- 售价：${product['price']:.2f}；月销：{to_float(product['monthly_sales']):,.0f}；评论：{to_float(product['reviews']):,.0f}；评分：{to_float(product['rating']):.1f}",
        f"- FBA 费：${product['fba_fee']:.2f}（售价占比 {product['fba_fee_share']:.1%}）",
        f"- 包装：{product['package_size_cm']} cm，约 {product['package_volume_l']:.2f} L，重量 {to_float(product['weight_g']):,.0f} g",
        f"- 上架：{product['online_date']}；父体变体：{int(to_float(product['variation_count']))} 个",
        "",
        "## 形态与类目判断",
        "",
        f"广义形态 **{shape['name']}**：Top100 中 {int(to_float(shape['count']))} 个，形态总月销 {to_float(shape['total_monthly_sales']):,.0f}，销量占比 {to_float(shape['sales_share']):.1%}，评论中位 {to_float(shape['median_reviews']):,.0f}，低评高销 {int(to_float(shape['low_review_high_sales_count']))} 个。",
        f"目标精确子形态 **{exact['name']}**：{int(to_float(exact['count']))} 个，月销 {to_float(exact['total_monthly_sales']):,.0f}，评论中位 {to_float(exact['median_reviews']):,.0f}。",
        "",
        "| 细分形态 | 数量 | 月销合计 | 销量占比 | 评论中位 | 低评高销 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in forms:
        lines.append(
            f"| {row.get('product_form', '')} | {int(to_float(row.get('count')))} | {to_float(row.get('total_monthly_sales')):,.0f} | {to_float(row.get('sales_share')):.1%} | {to_float(row.get('median_reviews')):,.0f} | {int(to_float(row.get('low_review_high_sales_count')))} |"
        )
    lines.extend(["", "## 成立信号", ""])
    lines.extend(f"- {item}" for item in summary["signals"])
    lines.extend(["", "## 核心风险", ""])
    lines.extend(f"- {item}" for item in summary["risks"])
    lines.extend(["", "## 切入方案", ""])
    lines.extend(f"- {item}" for item in summary["entry_strategy"])
    lines.extend(["", "## 利润门槛", ""])
    lines.extend(
        [
            f"按售价 ${product['price']:.2f}、FBA ${product['fba_fee']:.2f}、15% referral、15% 广告和 20% 目标贡献毛利倒推，**80P 完税到仓成本上限约 ${unit['max_landed_cost']:.2f}**。",
            "",
            unit["note"],
            "",
            "## 继续验证条件",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in summary["validation_gates"])
    lines.extend(["", "## 评论样本", "", "| ASIN | 形态 | 月销 | Listing 评论 | 已读取 |", "|---|---|---:|---:|---:|"])
    for row in targets:
        lines.append(
            f"| [{row['asin']}]({row['listing_url']}) | {row['product_form']} | {to_float(row['monthly_sales']):,.0f} | {to_float(row['reviews']):,.0f} | {int(to_float(row['review_rows_collected']))} |"
        )
    lines.extend(
        [
            "",
            "## 数据说明",
            "",
            f"- 类目来源：`{summary['provenance']['category_source']}`",
            f"- 本次 Sorftime MCP 调用：{summary['provenance']['mcp_tool_calls']} 次",
            "- 评论接口只返回近一年可用正文，数量不等于 Listing 总评论数。",
            "- 成本上限为倒推门槛，不是供应商报价。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    asin = args.asin.strip().upper()
    rules = load_json(ROOT / args.rules)
    output_dir = ROOT / (args.output_dir or f"research/{asin}")
    report_path = ROOT / (args.report or f"reports/product_opportunity_research_{asin}.md")
    output_dir.mkdir(parents=True, exist_ok=True)

    client = SorftimeMcpClient()
    detail = as_data(client.call_tool("product_detail", {"asin": asin, "amz_site": args.site}))
    if not isinstance(detail, dict) or not detail.get("asin"):
        raise SystemExit(f"No product detail returned for {asin}")
    node_id = str(detail.get("node_id") or "")
    report, report_source = category_report(client, node_id, args.site)
    report_data = as_data(report)
    if not isinstance(report_data, dict):
        raise SystemExit(f"Invalid category report for node {node_id}")
    raw_products = report_data.get("top100_products") or []
    category_stats = report_data.get("category_stats_report") or {}
    category_name = str(detail.get("subcategory") or "").split(" (Rank:")[0] or str(detail.get("category") or "")
    product_rows = [raw_row(asin, item, category_name) for item in raw_products if isinstance(item, dict)]
    if not any(row.get("asin") == asin for row in product_rows):
        product_rows.insert(0, raw_row(asin, detail, category_name))
    seed = next(row for row in product_rows if row.get("asin") == asin)
    seed["competitor_type"] = "seed"
    seed["relevance_score"] = 100
    enrich_row(seed, detail)

    selected_rows = select_review_rows(product_rows, asin, args.review_limit)
    for row in selected_rows:
        if row.get("asin") == asin:
            continue
        competitor_detail = as_data(client.call_tool("product_detail", {"asin": row["asin"], "amz_site": args.site}))
        if isinstance(competitor_detail, dict):
            enrich_row(row, competitor_detail)

    form_order = {
        "compatible disposable refill": 1,
        "brand duster refill": 2,
        "duster refill kit": 3,
        "reusable duster refill": 4,
    }
    product_rows.sort(
        key=lambda row: (
            0 if row.get("asin") == asin else form_order.get(str(row.get("product_form")), 9),
            -to_float(row.get("monthly_sales")),
        )
    )

    image_rows, contact_sheet = prepare_image_assets(product_rows, output_dir, rules)
    relativize_image_paths(product_rows)
    relativize_image_paths(image_rows)
    apply_visual_labels(product_rows)
    form_rows = build_form_rows(product_rows, rules)
    market = analyze_market(product_rows)
    targets = review_targets(selected_rows)
    reviews = collect_reviews(client, selected_rows, args.site)
    attach_review_counts(targets, reviews)
    demand = analyze_demand(reviews, product_rows, ROOT / rules["demand_taxonomy"])
    traffic = as_data(client.call_tool("product_traffic_terms", {"asin": asin, "amz_site": args.site, "page": 1}))
    customers_say = as_data(client.call_tool("product_customers_say", {"asin": asin, "site": args.site}))
    variations = as_data(client.call_tool("product_variations", {"asin": asin, "amz_site": args.site, "page": 1}))
    sales_trend = as_data(client.call_tool("product_trend", {"asin": asin, "amz_site": args.site, "product_trend_type": "SalesVolume"}))
    price_trend = as_data(client.call_tool("product_trend", {"asin": asin, "amz_site": args.site, "product_trend_type": "Price"}))
    keywords = keyword_rows(traffic)

    write_csv(output_dir / "top_products.csv", product_rows, PRODUCT_FIELDS)
    write_csv(output_dir / "product_forms.csv", form_rows, FORM_FIELDS)
    write_csv(output_dir / "keywords.csv", keywords, KEYWORD_FIELDS)
    write_csv(output_dir / "reviews.csv", reviews, REVIEW_FIELDS)
    write_csv(output_dir / "review_targets.csv", targets, REVIEW_TARGET_FIELDS)
    write_csv(output_dir / "image_review_queue.csv", image_rows, IMAGE_FIELDS)
    write_csv(output_dir / "visual_labels.csv", image_rows, IMAGE_FIELDS)
    write_csv(output_dir / "demand_analysis.csv", demand, DEMAND_FIELDS)
    (output_dir / "market_structure.json").write_text(json.dumps(market, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(
        output_dir / "price_bands.csv",
        market.get("price_bands", []),
        ["price_band", "listing_count", "monthly_sales", "sales_share", "median_reviews"],
    )
    write_business_feasibility(output_dir, ROOT / rules["business_feasibility_rules"])

    seed_shape = shape_snapshot(form_rows, str(seed.get("product_form") or ""))
    summary = make_summary(
        seed,
        detail,
        category_stats,
        seed_shape,
        {"sales": sales_trend, "price": price_trend},
        variations if isinstance(variations, list) else [],
        customers_say if isinstance(customers_say, dict) else {},
        keywords,
        raw_products,
        str(report_source.relative_to(ROOT)),
        client.tool_call_count,
    )
    (output_dir / "research_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(report_path, summary, form_rows, targets)

    print(f"Research products: {len(product_rows)}")
    print(f"Review targets: {len(targets)}; review rows: {len(reviews)}")
    print(f"MCP tool calls: {client.tool_call_count}")
    print(f"Contact sheet: {contact_sheet or '-'}")
    print(f"Report: {report_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
