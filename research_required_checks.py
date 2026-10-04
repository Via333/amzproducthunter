"""Evidence-backed mandatory checks shared by every product research entry."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean
from urllib.parse import urlparse


CHECK_TITLES = {
    "seasonality": "季节性",
    "patent": "专利与品牌集中度",
    "compliance": "声明与认证",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_products(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def monthly_series(items, today: date) -> list[dict]:
    """Keep observed, completed months; missing/invalid values are not zero."""
    months = {}
    if isinstance(items, dict):
        items = items.get("data", items)
        if isinstance(items, dict):
            items = [{"month": key, "value": value} for key, value in items.items()]
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str) and "=" in item:
            label, value = item.split("=", 1)
        elif isinstance(item, dict):
            label, value = str(item.get("month", "")), item.get("value")
        else:
            continue
        match = re.fullmatch(r"(\d{4})(?:年|-|/)(\d{1,2})月?", label.strip())
        if not match:
            continue
        year, month = map(int, match.groups())
        try:
            observed = date(year, month, 1)
            number = float(str(value).replace(",", ""))
        except (ValueError, TypeError):
            continue
        if observed >= today.replace(day=1) or not math.isfinite(number) or number < 0:
            continue
        months[observed.isoformat()[:7]] = number
    return [{"month": key, "value": value} for key, value in sorted(months.items())]


def cycle_metrics(series: list[dict]) -> dict:
    window = series[-24:]
    if len(window) < 24:
        return {}
    ordinals = [int(row["month"][:4]) * 12 + int(row["month"][5:7]) for row in window]
    if any(right - left != 1 for left, right in zip(ordinals, ordinals[1:])):
        return {}
    blocks, indices = [], {}
    for start in (0, 12):
        block = window[start:start + 12]
        baseline = mean(row["value"] for row in block)
        if baseline <= 0:
            return {}
        ranked = sorted(block, key=lambda row: row["value"], reverse=True)
        low_mean = mean(row["value"] for row in ranked[-3:])
        blocks.append({
            "start": block[0]["month"], "end": block[-1]["month"],
            "peak_months": sorted(int(row["month"][5:7]) for row in ranked[:3]),
            "trough_months": sorted(int(row["month"][5:7]) for row in ranked[-3:]),
            "peak_to_trough": round(mean(row["value"] for row in ranked[:3]) / low_mean, 2) if low_mean else None,
        })
        for row in block:
            indices.setdefault(int(row["month"][5:7]), []).append(row["value"] / baseline)
    monthly_indices = [{"month": key, "index": round(mean(value), 3)} for key, value in sorted(indices.items())]
    peak_overlap = len(set(blocks[0]["peak_months"]) & set(blocks[1]["peak_months"]))
    trough_overlap = len(set(blocks[0]["trough_months"]) & set(blocks[1]["trough_months"]))
    ratios = [row["peak_to_trough"] for row in blocks]
    changes = [right["value"] - left["value"] for left, right in zip(window, window[1:])]
    directional_trend = max(sum(value > 0 for value in changes), sum(value < 0 for value in changes)) >= len(changes) * 0.8
    if directional_trend:
        finding = "uncertain"
    elif all(value is not None and value >= 2 for value in ratios) and peak_overlap >= 2 and trough_overlap >= 2:
        finding = "strong"
    elif all(value is not None and value < 1.5 for value in ratios):
        finding = "weak"
    else:
        finding = "uncertain"
    ranked = sorted(monthly_indices, key=lambda row: row["index"], reverse=True)
    return {
        "finding": finding, "cycles": blocks, "monthly_indices": monthly_indices, "directional_trend": directional_trend,
        "peak_months": sorted(row["month"] for row in ranked[:3]),
        "trough_months": sorted(row["month"] for row in ranked[-3:]),
    }


def seasonality_check(summary: dict, sources: dict, today: date) -> dict:
    product = monthly_series(sources.get("product_sales_trend") or summary.get("trend", {}).get("monthly_sales", []), today)
    category = monthly_series(sources.get("category_sales_trend", []), today)
    product_metrics, category_metrics = cycle_metrics(product), cycle_metrics(category)
    metrics = product_metrics or category_metrics
    basis = "目标单品" if product_metrics else "类目 Top100"
    result = {
        "review_status": "reviewed" if metrics else "pending",
        "clearance": "pending", "risk": "unknown", "reviewed_on": today.isoformat(),
        "summary": "缺少连续 24 个完整月的数据，无法区分季节性、新品爬坡和短期波动。",
        "scope": basis, "product_months": len(product), "category_months": len(category),
        "product_series": product, "category_series": category,
        "metrics": metrics, "evidence": [], "sources": [], "next_steps": [],
    }
    if sources.get("retrieved_on"):
        result["evidence"].append(f"Sorftime MCP，采集于 {sources['retrieved_on']}；剔除当月和未来月，单品 {len(product)} 个月、类目 {len(category)} 个月。")
    if metrics:
        peaks = "、".join(str(value) for value in metrics["peak_months"])
        troughs = "、".join(str(value) for value in metrics["trough_months"])
        ratios = " / ".join(str(row["peak_to_trough"]) for row in metrics["cycles"])
        finding = metrics["finding"]
        if finding == "strong":
            result.update(risk="high", summary=f"{basis}存在明显季节性：高峰集中 {peaks} 月，低谷 {troughs} 月；两个周期旺淡季均值比为 {ratios} 倍。")
        elif finding == "weak":
            result.update(risk="low", summary=f"{basis}近两个周期未见强季节性，旺淡季均值比为 {ratios} 倍。")
        else:
            result.update(risk="unknown", summary=f"{basis}有波动，但两个周期没有足够一致的旺淡季模式，季节性仍不确定。")
        result["evidence"].append("每个连续 12 个月周期先除以该周期月均销量，再比较月份指数；旺淡季比使用最高三个月与最低三个月均值。")
        result["evidence"].append("周期范围：" + "；".join(f"{row['start']} 至 {row['end']}" for row in metrics["cycles"]) + "。")
        if metrics["directional_trend"]:
            result["evidence"].append("超过 80% 的月度变化同方向，可能是持续增长/衰退，不能把年度归一后的高低月份当作季节循环。")
        if not product_metrics:
            result["summary"] += " 这是类目代理证据，不能确认该新品或细分形态的全年曲线。"
        if finding == "weak" and product_metrics:
            result["clearance"] = "clear"
        else:
            result["next_steps"].append("结合至少两款成熟同形态竞品、促销和断货记录复核，并以淡季销量制定首单和补货计划。")
    else:
        result["next_steps"].append("补齐类目或成熟同形态竞品的 24 个月销量/搜索趋势。")
    try:
        fresh = 0 <= (today - date.fromisoformat(sources.get("retrieved_on", ""))).days <= 90
    except (ValueError, TypeError):
        fresh = False
    if metrics:
        end = metrics["cycles"][-1]["end"]
        fresh = fresh and today.year * 12 + today.month - (int(end[:4]) * 12 + int(end[5:7])) <= 3
    if metrics and not fresh:
        result.update(review_status="pending", clearance="pending", reviewed_on="")
        result["summary"] += " 数据采集日期缺失或超过 90 天，以上仅为历史观察，需更新核验。"
        result["next_steps"].append("补取近期趋势并更新数据采集日期。")
    return result


def brand_concentration(products: list[dict], target_form: str) -> dict:
    rows = [row for row in products if row.get("product_form") == target_form]
    groups = {}
    for row in rows:
        key = row.get("parent_asin") or row.get("asin")
        try:
            sales = max(0, float(row.get("monthly_sales") or 0))
        except (ValueError, TypeError):
            sales = 0
        if key and sales > groups.get(key, ({}, -1))[1]:
            groups[key] = (row, sales)
    brands, unknown = Counter(), 0
    for row, sales in groups.values():
        brand = str(row.get("brand") or "").strip()
        if brand.lower() in {"", "unknown", "generic", "unbranded", "n/a"}:
            unknown += sales
        else:
            brands[brand.casefold()] += sales
    total = sum(brands.values()) + unknown
    top = brands.most_common(3)
    return {
        "scope": "本次同形态样本，不代表全市场；已知父 ASIN 取最大月销去重，品牌集团归属未核实",
        "listing_count": len(groups), "brand_count": len(brands),
        "top_brand": top[0][0] if top else "",
        "top1_share": round(top[0][1] / total, 4) if total and top else None,
        "top3_share": round(sum(sales for _, sales in top) / total, 4) if total else None,
        "unknown_share": round(unknown / total, 4) if total else None,
        "summary": "销量集中度不能证明专利垄断，也不能证明不存在侵权。",
    }


def reviewed_check(review: dict, key: str, asin: str, site: str, today: date) -> dict:
    candidate = review.get(key, {})
    sources = [row for row in candidate.get("sources", []) if isinstance(row, dict) and row.get("title") and urlparse(str(row.get("url", ""))).scheme == "https" and urlparse(str(row.get("url", ""))).netloc]
    try:
        age = (today - date.fromisoformat(candidate.get("reviewed_on", ""))).days
    except (ValueError, TypeError):
        age = -1
    valid = (review.get("asin") == asin and review.get("site") == site
             and 0 <= age <= 180 and sources and candidate.get("summary")
             and candidate.get("review_status") == "reviewed")
    if not valid:
        if (key == "patent" and review.get("asin") == asin and review.get("site") == site
                and (candidate.get("risk") == "high" or candidate.get("clearance") == "blocked")):
            # Evidence expiry is not evidence that a known IP concern disappeared.
            return {**candidate, "review_status": "pending", "clearance": "blocked", "risk": "high",
                    "summary": "既有知识产权风险待复核，不能因证据过期或缺项解除阻断。" + str(candidate.get("summary", "")),
                    "sources": sources}
        return {
            "review_status": "pending", "clearance": "pending", "risk": "unknown",
            "summary": "尚未完成当前产品、目标市场的证据核验，不能按无风险处理。",
            "reviewed_on": "", "sources": [], "evidence": [],
            "next_steps": ["检索官方资料，记录检索范围、结论、来源及核验日期。"],
        }
    result = {**candidate, "sources": sources, "clearance": candidate.get("clearance", "pending")}
    if key == "patent":
        # This pipeline records screening evidence, not a legal launch approval.
        result["clearance"] = "blocked" if candidate.get("risk") == "high" or candidate.get("clearance") == "blocked" else "pending"
        result["next_steps"] = list(candidate.get("next_steps", []))
        if candidate.get("clearance") == "clear":
            result["next_steps"].append("原记录的 clear 不作为知识产权放行；需针对实际 SKU、全部配件及销售地区取得专业复核。")
    return result


def build_required_checks(summary: dict, products: list[dict], sources: dict, review: dict, today: date | None = None) -> dict:
    today = today or date.today()
    asin = str(summary.get("product", {}).get("asin") or next((row.get("asin") for row in products if row.get("competitor_type") == "seed"), ""))
    target_form = str(summary.get("exact_subform", {}).get("name") or next((row.get("product_form") for row in products if row.get("competitor_type") == "seed"), ""))
    site = sources.get("site", "US")
    source_asin = sources.get("detail", {}).get("asin")
    trend_sources = sources if not source_asin or source_asin == asin else {}
    trend_summary = summary if not source_asin or source_asin == asin else {}
    checks = {"seasonality": seasonality_check(trend_summary, trend_sources, today)}
    checks.update({key: reviewed_check(review, key, asin, site, today) for key in ("patent", "compliance")})
    checks["patent"]["brand_concentration"] = brand_concentration(products, target_form)
    complete = all(value["review_status"] == "reviewed" for value in checks.values())
    return {
        "schema_version": 1, "asin": asin, "site": site, "evaluated_on": today.isoformat(),
        "review_complete": complete,
        "launch_clearance": all(value.get("clearance") == "clear" for value in checks.values()),
        "checks": checks,
    }


def apply_research_gate(summary: dict, required: dict, business: dict) -> dict:
    summary = dict(summary)
    summary["required_checks"] = required
    summary.setdefault("market_signal_decision", summary.get("decision", ""))
    summary.setdefault("market_signal_reason", summary.get("decision_reason", ""))
    # A pending check must not downgrade an explicit eligibility rejection.
    if summary.get("archive_status") == "rejected":
        return summary
    patent = required.get("checks", {}).get("patent", {})
    if patent.get("risk") == "high" or patent.get("clearance") == "blocked":
        summary["decision"] = "知识产权高风险，暂缓立项与上架"
        summary["decision_reason"] = patent.get("summary") or "知识产权风险尚未排除，需专业复核，不能按无侵权风险放行。"
        return summary
    missing = [CHECK_TITLES[key] for key, item in required["checks"].items() if item["review_status"] != "reviewed"]
    blocks = []
    if missing:
        blocks.append("必要检查待补：" + "、".join(missing))
    if not required["launch_clearance"]:
        blocks.append("季节备货、专利结构或合规文件仍有待验证项")
    elif patent:
        # Also guard legacy/caller-supplied clear flags that bypass reviewed_check.
        blocks.append("知识产权初查不能替代实际 SKU 的专业复核，不能自动放行上架")
    if not business.get("profitability_verified"):
        blocks.append("利润未验证：缺少至少 3 条有效同款报价")
    if blocks:
        summary["decision"] = "研究待补齐，暂缓立项" if missing else "三项已初查，风险与利润待验证，暂缓立项"
        summary["decision_reason"] = "；".join(blocks) + "。"
    else:
        summary["decision"] = summary["market_signal_decision"]
        summary["decision_reason"] = summary["market_signal_reason"]
    return summary


def checks_markdown(required: dict) -> list[str]:
    lines = ["", "## 单品研究必查项", ""]
    for key, item in required["checks"].items():
        lines.extend([f"### {CHECK_TITLES[key]}", "", item["summary"], ""])
        lines.extend(f"- {value}" for value in item.get("evidence", []))
        brands = item.get("brand_concentration", {})
        if brands.get("top1_share") is not None:
            lines.append(f"- 同形态 Top1 品牌 {brands['top_brand']} 占 {brands['top1_share']:.1%}；Top3 占 {brands['top3_share']:.1%}。{brands['scope']}。")
        for document in item.get("documents", []):
            lines.append(f"- {document['name']}：{document['requirement']}；{document['status']}。")
        for finding in item.get("patents", []):
            lines.append(f"- [{finding['number']}]({finding['url']})：{finding['status']}；{finding['relevance']}")
        lines.extend(f"- 待验证：{value}" for value in item.get("next_steps", []))
        lines.extend(f"- 来源：[{value['title']}]({value['url']})" for value in item.get("sources", []))
        lines.append("")
    return lines


def refresh_required_checks(research_dir: Path) -> dict:
    summary = read_json(research_dir / "research_summary.json")
    required = build_required_checks(summary, read_products(research_dir / "top_products.csv"),
                                     read_json(research_dir / "research_sources.json"), read_json(research_dir / "risk_review.json"))
    (research_dir / "required_checks.json").write_text(json.dumps(required, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if summary:
        summary = apply_research_gate(summary, required, read_json(research_dir / "business_feasibility.json"))
        (research_dir / "research_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return required
