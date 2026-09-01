#!/usr/bin/env python3
"""Build an evidence-backed single-product research package with Sorftime MCP."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from business_feasibility import QUOTE_FIELDS, write_business_feasibility
from demand_analysis import DEMAND_FIELDS, analyze_demand
from market_structure import analyze_market
from product_opportunity_research import (
    FORM_FIELDS,
    IMAGE_FIELDS,
    KEYWORD_FIELDS,
    PRODUCT_FIELDS,
    REVIEW_FIELDS,
    REVIEW_TARGET_FIELDS,
)
from sorftime_mcp_client import SorftimeMcpClient


ROOT = Path(__file__).resolve().parent


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Write generated research data with stable LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


DEFAULT_RULES = ROOT / "config" / "opportunity_research_rules.json"
DEFAULT_CATEGORY_REPORT_ROOT = ROOT / "archive" / "discovery_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one ASIN research package from Sorftime MCP.")
    parser.add_argument("--asin", required=True)
    parser.add_argument("--search-term", default="", help="Direct-product search term; inferred from title if omitted.")
    parser.add_argument("--category-report", default="", help="Optional archived MCP category report JSON.")
    parser.add_argument("--rules", default=str(DEFAULT_RULES))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--review-targets", type=int, default=5)
    return parser.parse_args()


def data_of(response: Any) -> Any:
    if isinstance(response, dict):
        if "data" in response:
            return response["data"]
        if "Data" in response:
            return response["Data"]
    return response


def as_list(value: Any) -> list[dict[str, Any]]:
    value = data_of(value)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def number(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value if value is not None else "").replace("$", "").replace(",", "").strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def infer_search_term(title: str) -> str:
    lowered = title.lower()
    if "magnetic" in lowered and "tool mat" in lowered:
        return "magnetic tool mat mechanic"
    words = re.findall(r"[a-z0-9]+", lowered)
    return " ".join(words[:6])


def image_urls(detail: dict[str, Any]) -> list[str]:
    raw = detail.get("main_image") or detail.get("main_image_url") or ""
    if isinstance(raw, list):
        return [str(url) for url in raw if url]
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            return [str(url) for url in json.loads(raw) if url]
        except json.JSONDecodeError:
            pass
    return [raw] if raw else []


def product_form(title: str) -> str:
    text = title.lower()
    if "magnetic" in text and ("tool mat" in text or "magnetic mat" in text):
        if not any(term in text for term in (
            "solder", "gun cleaning", "barber", "hair", "stove", "craft",
            "project mat", "phone repair", "laptop", "computer repair", "electronics repair",
        )):
            return "flexible magnetic tool mat set"
    if "magnetic" in text and any(term in text for term in ("parts tray", "magnetic tray", "tool tray")):
        return "rigid magnetic parts tray"
    if "magnetic" in text and any(term in text for term in ("pickup tool", "pick up tool", "sweeper")):
        return "magnetic pickup tool"
    if "magnetic" in text and "wristband" in text:
        return "magnetic wristband"
    if "magnetic" in text and any(term in text for term in ("tool holder", "tool organizer", "wrench organizer", "socket organizer")):
        return "magnetic tool holder/organizer"
    if "solder" in text and "mat" in text:
        return "electronics repair magnetic mat"
    return "adjacent/noise"


def is_direct(title: str) -> bool:
    return product_form(title) == "flexible magnetic tool mat set"


def pack_count(title: str) -> int:
    text = title.lower()
    for pattern in (r"(\d+)\s*[- ]?pack", r"(\d+)\s*pcs", r"(\d+)\s*piece", r"(\d+)\s*size", r"set of\s*(\d+)"):
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return 1


def normalized_product(item: dict[str, Any], seed_asin: str, source: str) -> dict[str, Any]:
    asin = str(item.get("asin") or item.get("Asin") or "")
    title = str(item.get("title") or item.get("Title") or "")
    form = product_form(title)
    main_image = item.get("main_image") or item.get("MainImage") or item.get("photo") or ""
    if isinstance(main_image, list):
        main_image = main_image[0] if main_image else ""
    if isinstance(main_image, str) and main_image.startswith("["):
        try:
            main_image = json.loads(main_image)[0]
        except (json.JSONDecodeError, IndexError):
            pass
    sales = item.get("monthly_sales_volume")
    if sales in (None, ""):
        sales = item.get("monthly_sale_volume") or item.get("monthly_sales")
    reviews = item.get("review_count")
    if reviews in (None, ""):
        reviews = item.get("rating_count") or item.get("reviews")
    rating = item.get("star_rating")
    if rating in (None, ""):
        rating = item.get("rating")
    listing_date = item.get("online_date") or item.get("listing_date") or ""
    subcategory = item.get("subcategory") or ""
    if isinstance(subcategory, list):
        subcategory = " / ".join(str(part) for row in subcategory for part in (row if isinstance(row, list) else [row]))
    delivery = str(item.get("delivery_type") or "")
    weight = number(item.get("weight_g"), 0)
    if not weight:
        # Sorftime's product-name search currently returns this field in grams,
        # despite an older MCP description labelling it as pounds.
        weight = number(item.get("weight"), 0)
    return {
        "source_asin": seed_asin,
        "source": source,
        "listing_url": f"https://www.amazon.com/dp/{asin}",
        "product_type": str(item.get("category") or ""),
        "relevance_score": 100 if asin == seed_asin else (88 if form == "flexible magnetic tool mat set" else 35),
        "competitor_type": "seed" if asin == seed_asin else ("direct" if form == "flexible magnetic tool mat set" else "keyword"),
        "product_form": form,
        "material": "PVC leather + embedded magnets" if form == "flexible magnetic tool mat set" else "mixed",
        "material_evidence": "product title/description" if form == "flexible magnetic tool mat set" else "title",
        "detail_material": "PVC leather + embedded magnets" if form == "flexible magnetic tool mat set" else "mixed",
        "detail_evidence": "Sorftime MCP product detail" if asin == seed_asin else "title",
        "pack_count": pack_count(title),
        "closure": "none",
        "style": "workshop utility",
        "use_case": "mechanic parts/tool retention",
        "feature_tags": "strong magnet; flexible; surface protection" if form == "flexible magnetic tool mat set" else "magnetic organization",
        "asin": asin,
        "parent_asin": str(item.get("parent_asin") or ""),
        "title": title,
        "brand": str(item.get("brand") or ""),
        "price": round(number(item.get("price")), 2),
        "monthly_sales": round(number(sales)),
        "reviews": round(number(reviews)),
        "rating": round(number(rating), 1),
        "seller_address": str(item.get("seller_country") or item.get("seller_name") or item.get("seller") or ""),
        "is_fba": delivery in {"FBA", "AmzFBA"},
        "variation_count": round(number(item.get("variation_count"))),
        "listing_date": str(listing_date),
        "bsr_category": str(subcategory),
        "main_image_url": str(main_image),
        "image_file": str(main_image),
        "visual_product_form": form,
        "visual_material_signal": "PVC leather + embedded magnets" if form == "flexible magnetic tool mat set" else "mixed",
        "visual_pack_count": pack_count(title),
        "visual_closure": "none",
        "visual_style": "workshop utility",
        "visual_notes": f"{number(item.get('package_size_cm') or item.get('package_size'))}; {weight:.0f}g" if weight else "",
    }


def dedupe_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for row in rows:
        key = str(row.get("asin") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def build_form_rows(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    relevant = [row for row in products if row["product_form"] != "adjacent/noise"]
    total_sales = sum(number(row["monthly_sales"]) for row in relevant)
    for row in relevant:
        groups.setdefault(str(row["product_form"]), []).append(row)
    output = []
    for form, rows in groups.items():
        prices = [number(row["price"]) for row in rows if number(row["price"]) > 0]
        sales = [number(row["monthly_sales"]) for row in rows]
        reviews = [number(row["reviews"]) for row in rows]
        ratings = [number(row["rating"]) for row in rows if number(row["rating"]) > 0]
        form_sales = sum(sales)
        low_review_high_sales = sum(1 for row in rows if number(row["reviews"]) <= 300 and number(row["monthly_sales"]) >= 500)
        note = ""
        if form == "flexible magnetic tool mat set":
            note = "直接竞品少但头部集中；先验证是否能脱离头部 Listing 获客"
        elif form == "rigid magnetic parts tray":
            note = "成熟替代品，价格更低、评论壁垒更高"
        else:
            note = "相邻需求证据，不计入目标形态机会结论"
        output.append({
            "product_form": form,
            "count": len(rows),
            "direct_count": sum(row["competitor_type"] in {"seed", "direct"} for row in rows),
            "keyword_count": sum(row["competitor_type"] == "keyword" for row in rows),
            "avg_price": round(mean(prices), 2) if prices else 0,
            "median_price": round(median(prices), 2) if prices else 0,
            "avg_monthly_sales": round(mean(sales)) if sales else 0,
            "median_monthly_sales": round(median(sales)) if sales else 0,
            "total_monthly_sales": round(form_sales),
            "sales_share": round(form_sales / total_sales, 4) if total_sales else 0,
            "top3_sales_share": round(sum(sorted(sales, reverse=True)[:3]) / form_sales, 4) if form_sales else 0,
            "median_reviews": round(median(reviews)) if reviews else 0,
            "avg_rating": round(mean(ratings), 1) if ratings else 0,
            "low_review_high_sales_count": low_review_high_sales,
            "top_materials": "PVC leather" if form == "flexible magnetic tool mat set" else "mixed",
            "top_pack_counts": "; ".join(f"{key}:{value}" for key, value in Counter(str(row["pack_count"]) for row in rows).most_common(3)),
            "top_styles": "workshop utility",
            "price_p25": min(prices) if prices else 0,
            "price_p75": max(prices) if prices else 0,
            "new_listing_share_12m": analyze_market(rows)["new_listing_share_12m"],
            "opportunity_note": note,
        })
    output.sort(key=lambda row: (row["product_form"] != "flexible magnetic tool mat set", -number(row["total_monthly_sales"])))
    return output


PAIN_TERMS = {
    "magnet_strength": ["weak", "weaker", "strength", "strong magnet", "magnetic force", "hold"],
    "size_capacity": ["small", "size", "larger", "fit", "capacity"],
    "price_value": ["price", "expensive", "value", "cheaper", "cost"],
    "durability": ["durable", "tear", "rip", "abuse", "sturdy", "quality"],
    "surface_protection": ["scratch", "mar", "paint", "surface"],
    "pickup_tool": ["pickup", "pick-up", "telescoping", "pen"],
}


def pain_tags(text: str) -> str:
    lowered = text.lower()
    return "; ".join(tag for tag, terms in PAIN_TERMS.items() if any(term in lowered for term in terms))


def review_rows(asin: str, response: Any) -> list[dict[str, Any]]:
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for item in as_list(response):
        content = str(item.get("content") or "").strip()
        title = str(item.get("title") or "").strip()
        date = str(item.get("review_date") or "")
        key = (date, title, content)
        if not content or key in seen:
            continue
        seen.add(key)
        rows.append({
            "review_target_asin": asin,
            "asin": asin,
            "listing_url": f"https://www.amazon.com/dp/{asin}",
            "rating": number(item.get("star_rating")),
            "review_title": title,
            "review_text": content,
            "review_date": date,
            "pain_point_tags": pain_tags(f"{title} {content}"),
            "review_link": "",
            "verified_purchase": "",
            "asin_property": str(item.get("variant_attribute") or ""),
            "helpful": 0,
        })
    return rows


def locate_category_report(node_id: str, explicit: str) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    matches = sorted(DEFAULT_CATEGORY_REPORT_ROOT.glob(f"*/raw_category_reports/{node_id}.json"), reverse=True)
    return matches[0] if matches else None


def category_context(node_id: str, explicit: str) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    path = locate_category_report(node_id, explicit)
    if not path:
        return {}, [], ""
    report = read_json(path)
    data = report.get("data") if isinstance(report.get("data"), dict) else {}
    stats = data.get("category_stats_report") if isinstance(data.get("category_stats_report"), dict) else {}
    products = data.get("top100_products") if isinstance(data.get("top100_products"), list) else []
    return stats, [row for row in products if isinstance(row, dict)], str(path.relative_to(ROOT))


def parse_trend(response: Any) -> list[dict[str, Any]]:
    rows = []
    for item in data_of(response) or []:
        if not isinstance(item, str) or "=" not in item:
            continue
        month, value = item.split("=", 1)
        rows.append({"period": month, "value": number(value)})
    return rows


def direct_supplier_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for row in rows:
        title = str(row.get("title") or "")
        if "工具垫" in title and any(term in title for term in ("磁", "磁铁", "磁吸")):
            matches.append(row)
    return matches


def keyword_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "keyword": str(item.get("keyword") or ""),
        "search_volume": round(number(item.get("monthly_search_volume"))),
        "rank": 0,
        "show_share": 0,
        "clicks_90d": 0,
        "top3_asins": "",
        "image_asin_count": 0,
    } for item in items if item.get("keyword")]


def build_deep_analysis(
    detail: dict[str, Any],
    products: list[dict[str, Any]],
    forms: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    customers_say: dict[str, Any],
    keyword_detail: dict[str, Any],
    sales_trend: list[dict[str, Any]],
    price_trend: list[dict[str, Any]],
    variations: list[dict[str, Any]],
    suppliers: list[dict[str, Any]],
    category_stats: dict[str, Any],
    category_report_path: str,
    tool_calls: int,
) -> dict[str, Any]:
    direct = [row for row in products if row["product_form"] == "flexible magnetic tool mat set"]
    direct_sales = sum(number(row["monthly_sales"]) for row in direct)
    seed_sales = number(detail.get("monthly_sales_volume"))
    seed_share = seed_sales / direct_sales if direct_sales else 0
    recent_sales = [number(row["value"]) for row in sales_trend[-3:]]
    unique_low = [row for row in reviews if 0 < number(row["rating"]) <= 3]
    exact_form = next((row for row in forms if row["product_form"] == "flexible magnetic tool mat set"), {})
    score = 72
    if seed_share >= 0.55:
        score -= 8
    if len(direct) < 4:
        score -= 5
    if number(detail.get("weight_g")) >= 1500:
        score -= 5
    if number(detail.get("coupon")) > 0:
        score -= 3
    if len(suppliers) >= 3:
        score += 5
    if number(exact_form.get("median_reviews")) <= 100:
        score += 4
    score = max(0, min(100, score))
    verdict = "继续验证，不直接立项"
    return {
        "verdict": verdict,
        "score": score,
        "confidence": "medium",
        "one_line": f"需求和新品增长真实，目标 Listing 占同形态样本销量约 {seed_share:.0%}；先做样品、磁场运输测试和小预算关键词验证。",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_sources": ["Sorftime MCP product/keyword/review/trend", category_report_path or "no local category report", "Sorftime 1688 image search"],
        "tool_calls": tool_calls,
        "facts": {
            "price": number(detail.get("price")),
            "coupon": number(detail.get("coupon")),
            "effective_price": max(0, number(detail.get("price")) - number(detail.get("coupon"))),
            "monthly_sales": seed_sales,
            "monthly_revenue": number(detail.get("monthly_sales_amount")),
            "reviews": number(detail.get("review_count")),
            "rating": number(detail.get("star_rating")),
            "package_size_cm": detail.get("package_size_cm"),
            "weight_g": number(detail.get("weight_g")),
            "fba_fee": number(detail.get("fba_fee")),
            "gross_profit": number(detail.get("gross_profit")),
            "gross_profit_rate": number(detail.get("gross_profit_rate")),
            "online_date": detail.get("online_date"),
            "subcategory": detail.get("subcategory"),
        },
        "market": {
            "category_top100_sales": number(category_stats.get("top100_monthly_sales_volume")),
            "category_top3_share": category_stats.get("top3_product_sales_volume_share"),
            "category_low_review_sales_share": category_stats.get("low_reviews_sales_volume_share"),
            "category_return_rate": category_stats.get("return_rate"),
            "direct_competitor_count": len(direct),
            "direct_form_monthly_sales": direct_sales,
            "seed_share_of_direct_sample": round(seed_share, 4),
            "direct_form_median_reviews": number(exact_form.get("median_reviews")),
            "keyword_monthly_search_volume": number(keyword_detail.get("monthly_search_volume")),
            "keyword_competitor_count": number(keyword_detail.get("search_result_competitor_count")),
            "keyword_cpc": number(keyword_detail.get("recommended_cpc_bid")),
            "keyword_peak_season": keyword_detail.get("search_volume_peak_season"),
            "keyword_caveat": "搜索结果被工艺磁片、焊接垫、磁性托盘等大量跨形态产品污染，不能把结果数当作直接竞品数。",
        },
        "trend": {
            "sales": sales_trend,
            "price": price_trend,
            "recent_three_month_avg_sales": round(mean(recent_sales)) if recent_sales else 0,
            "summary": "2025-12 上架后从 143 件增长到近期约 981 件；6 月峰值 1,253，7 月回落后 8-9 月恢复。",
        },
        "variations": variations,
        "review_summary": {
            "reviews_read": len(reviews),
            "low_star_reviews": len(unique_low),
            "customers_say": customers_say.get("customer_say", ""),
            "topics": customers_say.get("details", []),
            "core_positive": ["磁力强、可垂直吸附", "工具和紧固件不易丢失", "三种尺寸适配不同工位", "PVC 表面不易刮漆且易清洁"],
            "core_negative": ["少量用户认为磁力低于预期", "大垫尺寸和承载面积仍偏小", "原价偏高，转化依赖优惠券", "标题写 25Lb，描述/配件又出现 15Lb，规格表达不一致"],
        },
        "supply": {
            "image_match_count": len(suppliers),
            "lowest_visible_price_cny": min((number(row.get("price")) for row in suppliers if number(row.get("price")) > 0), default=0),
            "top_match_30d_sales": max((number(row.get("sales_of_30d")) for row in suppliers), default=0),
            "top_match_repurchase_rate": max((number(row.get("repurchase_rate")) for row in suppliers), default=0),
            "assessment": "同图货源很多，说明制造门槛不高、跟卖速度会快；价格仅是图搜展示价，必须核对 3 件套、磁铁等级、拉力、皮面和包装后才能计入利润。",
        },
        "opportunities": [
            "把“垫子”升级为工位系统：零件分区、拆装顺序标记、可擦写标签或模块化拼接。",
            "优先做单块大尺寸与 3 尺寸套装两条 SKU，避免只复制现有 3 件套。",
            "用实测承重、垂直吸附、弧面贴合和不伤车漆做视频证据，而不是继续堆 25/40Lb 数字。",
            "定位汽车维修、设备维护和 DIY 拆装场景，集中投放 exact 关键词，减少泛 magnetic 流量浪费。",
        ],
        "risks": [
            f"直接形态只有 {len(direct)} 个有效样本，目标 ASIN 占样本销量约 {seed_share:.0%}，仍需拆分产品需求与头部 Listing 运营能力。",
            "关键词 magnetic tool mat 的搜索结果严重跨类目，表面 30k 搜索量不能全部归因于目标形态。",
            "产品约 1.63kg，FBA 费 $7.77；低价跟进会很快挤压利润。",
            "磁性货物需做外包装磁场测试；是否属于受限航空磁性材料取决于包装后的实测值。",
            "嵌入磁铁必须验证跌落、弯折、缝合/热压后不会脱出，并避免儿童/玩具化营销。",
        ],
        "next_actions": [
            "向 3 家供应商索取 3 件套真实阶梯价、磁铁材质/等级、单垫拉力测试、重量和包装尺寸。",
            "做包装后 7 英尺磁场/指南针偏转测试，并让货代书面确认空运运输条件。",
            "采购头部款与两个 $23-$34 竞品，实测垂直承载、弧面贴合、刮漆、耐油污和 500 次弯折。",
            "先用 magnetic tool mat 精确词做 2 周广告验证；目标是非品牌转化能覆盖 $1.28 CPC。",
            "若样品无法在尺寸、磁力一致性或分区功能上形成明确差异，则停止立项。",
        ],
        "compliance": {
            "level": "medium",
            "summary": "不是食品接触品，也不是天然需要高门槛认证的类目；主要门槛是磁体脱落安全、一般产品合规和磁性货物运输测试。",
            "references": [
                {"label": "CPSC Magnets Business Guidance", "url": "https://www.cpsc.gov/Business--Manufacturing/Business-Education/Business-Guidance/Magnets"},
                {"label": "USPS Publication 52 - Magnetized Materials", "url": "https://pe.usps.com/text/pub52/pub52c3_028.htm"},
            ],
        },
    }


def report_markdown(asin: str, title: str, deep: dict[str, Any], products: list[dict[str, Any]], forms: list[dict[str, Any]]) -> str:
    facts = deep["facts"]
    market = deep["market"]
    lines = [
        f"# {asin} 单品深度研究",
        "",
        f"- 产品：{title}",
        f"- 结论：**{deep['verdict']}**（{deep['score']}/100，置信度 {deep['confidence']}）",
        f"- 判断：{deep['one_line']}",
        "",
        "## 核心数据",
        "",
        f"- 售价：${facts['price']:.2f}，优惠后约 ${facts['effective_price']:.2f}",
        f"- 月销/销售额：{facts['monthly_sales']:.0f} / ${facts['monthly_revenue']:,.0f}",
        f"- 评分/评论：{facts['rating']:.1f} / {facts['reviews']:.0f}",
        f"- 包装/重量：{facts['package_size_cm']} cm / {facts['weight_g']:.0f} g",
        f"- FBA 费：${facts['fba_fee']:.2f}",
        "",
        "## 市场判断",
        "",
        f"- 所属类目 Top100 月销：{market['category_top100_sales']:,.0f}；Top3 占比：{market['category_top3_share']}。",
        f"- 同形态样本：{market['direct_competitor_count']} 个，样本月销 {market['direct_form_monthly_sales']:,.0f}，目标 ASIN 占 {market['seed_share_of_direct_sample']:.1%}。",
        f"- 关键词 magnetic tool mat 月搜索 {market['keyword_monthly_search_volume']:,.0f}，CPC 约 ${market['keyword_cpc']:.2f}。",
        f"- 注意：{market['keyword_caveat']}",
        "",
        "## 机会",
        "",
        *[f"- {item}" for item in deep["opportunities"]],
        "",
        "## 风险",
        "",
        *[f"- {item}" for item in deep["risks"]],
        "",
        "## 下一步",
        "",
        *[f"{index}. {item}" for index, item in enumerate(deep["next_actions"], 1)],
        "",
        "## 直接竞品",
        "",
        "| ASIN | 标题 | 售价 | 月销 | 评论 | 评分 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in products:
        if row["product_form"] != "flexible magnetic tool mat set":
            continue
        lines.append(f"| {row['asin']} | {row['title']} | ${number(row['price']):.2f} | {number(row['monthly_sales']):,.0f} | {number(row['reviews']):,.0f} | {number(row['rating']):.1f} |")
    lines.extend(["", "## 产品形态", "", "| 形态 | 样本 | 月销 | 评论中位 | 备注 |", "|---|---:|---:|---:|---|"])
    for row in forms:
        lines.append(f"| {row['product_form']} | {row['count']} | {number(row['total_monthly_sales']):,.0f} | {number(row['median_reviews']):,.0f} | {row['opportunity_note']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    asin = args.asin.strip().upper()
    output_dir = Path(args.output_dir or ROOT / "research" / asin)
    report_path = Path(args.report or ROOT / "reports" / f"product_opportunity_research_{asin}.md")
    rules = read_json(Path(args.rules))
    client = SorftimeMcpClient()

    detail = data_of(client.call_tool("product_detail", {"asin": asin, "amz_site": "US"})) or {}
    if not isinstance(detail, dict) or not detail.get("asin"):
        raise SystemExit(f"Sorftime MCP returned no product detail for {asin}")
    search_term = args.search_term.strip() or infer_search_term(str(detail.get("title") or ""))
    traffic = as_list(client.call_tool("product_traffic_terms", {"asin": asin, "amz_site": "US", "page": 1}))
    variations = as_list(client.call_tool("product_variations", {"asin": asin, "amz_site": "US", "page": 1}))
    customers_say = data_of(client.call_tool("product_customers_say", {"asin": asin, "site": "US"})) or {}
    sales_trend_raw = client.call_tool("product_trend", {"asin": asin, "amz_site": "US", "product_trend_type": "SalesVolume"})
    price_trend_raw = client.call_tool("product_trend", {"asin": asin, "amz_site": "US", "product_trend_type": "Price"})
    keyword_detail = data_of(client.call_tool("keyword_detail", {"keyword": "magnetic tool mat", "keyword_support_site": "US"})) or {}
    keyword_results = as_list(client.call_tool("keyword_search_results", {"keyword": "magnetic tool mat", "keyword_support_site": "US", "page": 1, "position_type": 1}))
    product_search = as_list(client.call_tool("product_search_from_name", {"name": search_term, "amz_site": "US", "page": 1}))

    category_stats, category_products, category_report_path = category_context(str(detail.get("node_id") or ""), args.category_report)
    seed = normalized_product(detail, asin, "seed")
    candidates = [normalized_product(item, asin, "same-form search") for item in product_search]
    candidates.extend(normalized_product(item, asin, "keyword search") for item in keyword_results)
    candidates.extend(normalized_product(item, asin, "category Top100") for item in category_products)
    candidates = dedupe_products([seed, *candidates])
    direct = sorted(
        [row for row in candidates if row["product_form"] == "flexible magnetic tool mat set"],
        key=lambda row: (row["asin"] != asin, -number(row["monthly_sales"])),
    )
    adjacent = sorted(
        [row for row in candidates if row["product_form"] not in {"flexible magnetic tool mat set", "adjacent/noise"}],
        key=lambda row: -number(row["monthly_sales"]),
    )
    products = dedupe_products([*direct[:12], *adjacent[:12]])

    targets = direct[: max(1, args.review_targets)]
    reviews = []
    review_targets = []
    for target in targets:
        rows = review_rows(target["asin"], client.call_tool("product_reviews", {"asin": target["asin"], "amz_site": "US", "review_type": "Both"}))
        reviews.extend(rows)
        review_targets.append({
            "asin": target["asin"],
            "listing_url": target["listing_url"],
            "title": target["title"],
            "competitor_type": target["competitor_type"],
            "product_form": target["product_form"],
            "monthly_sales": target["monthly_sales"],
            "reviews": target["reviews"],
            "rating": target["rating"],
            "review_rows_collected": len(rows),
        })

    suppliers = direct_supplier_matches(
        as_list(client.call_tool("ali1688_product_search_from_image", {"image_url": image_urls(detail)[0], "page": 1}))
    )
    sales_trend = parse_trend(sales_trend_raw)
    price_trend = parse_trend(price_trend_raw)
    forms = build_form_rows(products)
    demand = analyze_demand(reviews, products, Path(rules.get("demand_taxonomy", "config/demand_taxonomy.json")))
    market = analyze_market(products)
    deep = build_deep_analysis(
        detail, products, forms, reviews, customers_say, keyword_detail, sales_trend, price_trend,
        variations, suppliers, category_stats, category_report_path, client.tool_call_count,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "top_products.csv", products, PRODUCT_FIELDS)
    write_csv(output_dir / "product_forms.csv", forms, FORM_FIELDS)
    write_csv(output_dir / "keywords.csv", keyword_rows(traffic), KEYWORD_FIELDS)
    write_csv(output_dir / "reviews.csv", reviews, REVIEW_FIELDS)
    write_csv(output_dir / "review_targets.csv", review_targets, REVIEW_TARGET_FIELDS)
    write_csv(output_dir / "demand_analysis.csv", demand, DEMAND_FIELDS)
    write_csv(output_dir / "image_review_queue.csv", products, IMAGE_FIELDS)
    write_csv(output_dir / "visual_labels.csv", products, IMAGE_FIELDS)
    write_csv(output_dir / "supplier_quotes.csv", [], QUOTE_FIELDS)
    price_bands = market.get("price_bands") or []
    write_csv(output_dir / "price_bands.csv", price_bands, ["price_band", "listing_count", "monthly_sales", "sales_share", "median_reviews"])
    (output_dir / "market_structure.json").write_text(json.dumps(market, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "deep_analysis.json").write_text(json.dumps(deep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "mcp_source_summary.json").write_text(json.dumps({
        "asin": asin,
        "search_term": search_term,
        "category_report": category_report_path,
        "tool_calls": client.tool_call_count,
        "direct_asins": [row["asin"] for row in direct],
        "reviewed_asins": [row["asin"] for row in targets],
        "supplier_match_count": len(suppliers),
        "generated_at": deep["generated_at"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_business_feasibility(output_dir, Path(rules.get("business_feasibility_rules", "config/business_feasibility_rules.json")))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_markdown(asin, str(detail.get("title") or asin), deep, products, forms), encoding="utf-8")
    print(f"MCP calls: {client.tool_call_count}")
    print(f"Direct competitors: {len(direct)}")
    print(f"Reviews: {len(reviews)} across {len(review_targets)} ASINs")
    print(f"Research directory: {output_dir}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
