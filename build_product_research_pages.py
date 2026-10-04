#!/usr/bin/env python3
"""Build standalone HTML pages for archived product research outputs."""

from __future__ import annotations

import csv
import json
import re
import shutil
from html import escape
from pathlib import Path
from urllib.parse import quote
from research_required_checks import CHECK_TITLES, apply_research_gate, build_required_checks


import os
from workflow_storage import atomic_write_text
ROOT = Path(os.environ.get("AMZ_WORKFLOW_ROOT") or Path(__file__).resolve().parent)
INDEX_PATH = ROOT / "archive" / "product_research_index.csv"
WEB_RESEARCH_DIR = ROOT / "web" / "research"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def to_float(value: object, default: float = 0.0) -> float:
    try:
        text = str(value or "").replace("$", "").replace(",", "").strip()
        return float(text) if text else default
    except ValueError:
        return default


def fmt_int(value: object) -> str:
    number = to_float(value)
    return f"{number:,.0f}" if number else "-"


def fmt_money(value: object) -> str:
    number = to_float(value)
    return f"${number:,.2f}" if number else "-"


def fmt_cny(value: object) -> str:
    number = to_float(value)
    return f"¥{number:,.2f}" if number else "-"


def number_or_none(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace("$", "").replace("¥", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def input_number(value: object) -> str:
    number = number_or_none(value)
    return "" if number is None else f"{number:g}"


def build_profit_calculator_defaults(
    seed: dict[str, str],
    business: dict,
    rules: dict,
    product_summary: dict | None = None,
) -> dict[str, float | None]:
    assumptions = business.get("assumptions") if isinstance(business.get("assumptions"), dict) else {}

    def confirmed_cost(key: str, source_key: str) -> float | None:
        source = str(assumptions.get(source_key) or "").strip().lower()
        if source in {"quote_median", "product_detail", "confirmed", "manual"}:
            return number_or_none(business.get(key))
        return None

    price = number_or_none(business.get("target_price"))
    if price is None:
        price = number_or_none(seed.get("price"))
    referral_rate = number_or_none(assumptions.get("referral_fee_rate"))
    if referral_rate is None:
        referral_rate = number_or_none(rules.get("referral_fee_rate"))
    ad_rate = number_or_none(assumptions.get("ad_cost_rate"))
    if ad_rate is None:
        ad_rate = number_or_none(rules.get("target_ad_cost_rate"))
    return_rate = number_or_none(rules.get("assumed_return_rate"))
    return_loss_rate = number_or_none(rules.get("assumed_return_loss_rate"))

    fba_fee = confirmed_cost("fba_fee", "fba_fee")
    if fba_fee is None:
        fba_fee = number_or_none((product_summary or {}).get("fba_fee"))

    return {
        "selling_price_usd": price,
        "procurement_cost_cny": number_or_none(business.get("procurement_cost_cny")),
        "cny_per_usd": number_or_none(business.get("cny_per_usd")) or number_or_none(rules.get("planning_cny_per_usd")),
        "packaging_cost_usd": confirmed_cost("packaging_cost", "packaging"),
        "freight_cost_usd": confirmed_cost("freight_cost", "freight"),
        "duty_cost_usd": confirmed_cost("duty_cost", "duty"),
        "fba_fee_usd": fba_fee,
        "other_cost_usd": None,
        "referral_rate_pct": referral_rate * 100 if referral_rate is not None else None,
        "ad_rate_pct": ad_rate * 100 if ad_rate is not None else None,
        "return_loss_rate_pct": (
            return_rate * return_loss_rate * 100
            if return_rate is not None and return_loss_rate is not None
            else None
        ),
        "coupon_rate_pct": (
            number_or_none(rules.get("assumed_coupon_rate")) * 100
            if number_or_none(rules.get("assumed_coupon_rate")) is not None
            else None
        ),
        "storage_rate_pct": (
            number_or_none(rules.get("assumed_storage_rate")) * 100
            if number_or_none(rules.get("assumed_storage_rate")) is not None
            else None
        ),
        "monthly_sales": number_or_none(seed.get("monthly_sales")),
    }


def short(text: str, limit: int = 92) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def asset_path(path_text: str) -> str:
    if not path_text:
        return ""
    if path_text.startswith(("https://", "http://")):
        return path_text
    path = Path(path_text)
    if path.is_absolute():
        try:
            path = path.relative_to(ROOT)
        except ValueError:
            return path.as_posix()
    return f"../../{path.as_posix()}"


def render_form_example(product: dict[str, str] | None) -> str:
    from research_images import image_urls

    if not product:
        return '<span class="snapshot-example-empty">暂无对应样本</span>'
    asin = str(product["asin"])
    title = str(product.get("title") or asin)
    image = product.get("web_image_url") or asset_path(product.get("image_file", ""))
    image = image or next(iter(image_urls(product.get("main_image_url"))), "")
    thumbnail = (f'<img src="{escape(image)}" width="88" height="88" loading="lazy" '
                 f'decoding="async" alt="{escape(title)}">' if image else
                 '<span class="snapshot-image-empty">待补图</span>')
    return (f'<a class="snapshot-example" data-example-asin="{escape(asin)}" '
            f'href="https://www.amazon.com/dp/{quote(asin, safe="")}" target="_blank" rel="noreferrer">'
            f'<span class="snapshot-thumb">{thumbnail}</span><span class="snapshot-example-meta">'
            f'<b>{escape(asin)}</b><span class="snapshot-example-title" title="{escape(title)}">'
            f'{escape(title)}</span></span></a>')


def render_forms(forms: list[dict[str, str]], products=()) -> str:
    from research_images import representative_products

    examples = representative_products(products)
    rows = []
    for row in forms:
        rows.append(
            """
            <tr>
              <td><b><a href="#product-gallery" data-snapshot-form="{form}">{form}</a></b><span>{note}</span>{example}</td>
              <td class="num">{count}</td>
              <td class="num">{direct}/{keyword}</td>
              <td class="num">{price}</td>
              <td class="num">{total_sales}<span>中位 {median_sales}</span></td>
              <td class="num">{sales_share}</td>
              <td class="num">{reviews}</td>
              <td>{materials}<span>{packs}</span></td>
            </tr>
            """.format(
                form=escape(row.get("product_form", "")),
                example=render_form_example(examples.get(row.get("product_form", "").strip())),
                note=escape(row.get("opportunity_note", "")),
                count=fmt_int(row.get("count")),
                direct=fmt_int(row.get("direct_count")),
                keyword=fmt_int(row.get("keyword_count")),
                price=fmt_money(row.get("avg_price")),
                total_sales=fmt_int(row.get("total_monthly_sales") or row.get("avg_monthly_sales")),
                median_sales=fmt_int(row.get("median_monthly_sales") or row.get("avg_monthly_sales")),
                sales_share=f"{to_float(row.get('sales_share')) * 100:.1f}%" if row.get("sales_share") else "-",
                reviews=fmt_int(row.get("median_reviews")),
                materials=escape(row.get("top_materials", "") or "-"),
                packs=escape(row.get("top_pack_counts", "")),
            )
        )
    return "\n".join(rows)


def render_price_bands(rows: list[dict[str, str]]) -> str:
    return "\n".join(
        "<tr><td>{band}</td><td class=\"num\">{count}</td><td class=\"num\">{sales}</td><td class=\"num\">{share:.1f}%</td><td class=\"num\">{reviews}</td></tr>".format(
            band=escape(row.get("price_band", "")),
            count=fmt_int(row.get("listing_count")),
            sales=fmt_int(row.get("monthly_sales")),
            share=to_float(row.get("sales_share")) * 100,
            reviews=fmt_int(row.get("median_reviews")),
        )
        for row in rows
    )


REVIEW_THEME_LABELS = {
    "durability": "耐用性",
    "size_fit": "尺寸与适配",
    "ease_of_use": "使用便利性",
    "performance": "功能表现",
    "material_quality": "材质与做工",
    "cleaning_maintenance": "清洗与维护",
    "value_quantity": "价格与套装价值",
    "instructions_setup": "说明与安装",
    "packaging_delivery": "包装与配送",
    "other": "其他反馈",
}
REVIEW_TYPE_LABELS = {
    "delight": "超出预期",
    "expected": "常规需求",
    "basic": "基础需求未满足",
    "reverse": "使用负担",
    "fulfillment_noise": "履约与售后",
    "uncategorized": "需求待分类",
}
REVIEW_CONTEXT_LABELS = {
    "family": "家庭用户", "older_adult": "老年用户",
    "professional": "专业用户", "beginner": "新手",
    "daily_home": "日常家用", "travel": "旅行",
    "gift": "送礼", "storage": "收纳", "outdoor": "户外",
}
def review_context_labels(value: str) -> str:
    return "、".join(REVIEW_CONTEXT_LABELS.get(term.strip(), term.strip())
                    for term in value.split(";") if term.strip()) or "未识别"


def render_review_asins(value: str) -> str:
    parts = []
    for token in value.split(";"):
        asin = token.strip()
        if re.fullmatch(r"[A-Z0-9]{10}", asin):
            parts.append(f'<a href="https://www.amazon.com/dp/{asin}" target="_blank" rel="noreferrer">{asin}</a>')
        elif asin:
            parts.append(escape(asin))
    return " · ".join(parts) or "未记录"


def render_review_translation(excerpt: str, entry: dict | None) -> str:
    if not excerpt.strip():
        return ""
    translation = str((entry or {}).get("translation_zh") or "").strip()
    if not translation:
        return '<p class="review-translation pending">中文翻译待补充</p>'
    label = "中文翻译（原文截断）" if entry.get("source_truncated") else "中文翻译"
    return (f'<p class="review-translation" lang="zh-CN"><span class="review-language">{label}</span>'
            f'{escape(translation)}</p>')


def render_demand(rows: list[dict[str, str]], translations: dict | None = None) -> str:
    # Exact source text prevents a changed excerpt from reusing an obsolete translation.
    lookup = {entry["source_excerpt"]: entry for entry in (translations or {}).get("entries", [])
              if isinstance(entry, dict) and isinstance(entry.get("source_excerpt"), str)}
    return "\n".join(
        """
        <tr>
          <td data-label="形态 / 需求"><b>{form}</b><span>{dtype}</span></td>
          <td data-label="主题">{theme}</td><td data-label="提及次数" class="num">{count}</td>
          <td data-label="用户">{profiles}</td><td data-label="场景">{scenes}</td>
          <td data-label="评论摘录" class="review-excerpt"><p class="review-original">{excerpt}</p>{translation}<span>同主题涉及 ASIN：{asins}</span></td>
        </tr>
        """.format(
            form=escape(row.get("product_form", "") or "形态未记录"),
            dtype=escape(REVIEW_TYPE_LABELS.get(row.get("demand_type", ""), row.get("demand_type") or "需求待分类")),
            theme=escape(REVIEW_THEME_LABELS.get(row.get("theme", ""), row.get("theme") or "主题未记录")),
            count=fmt_int(row.get("mention_count")),
            profiles=escape(review_context_labels(row.get("user_profiles", ""))),
            scenes=escape(review_context_labels(row.get("use_scenes", ""))),
            excerpt=escape(row.get("evidence_excerpt", "") or "未保存原文摘录"),
            translation=render_review_translation(row.get("evidence_excerpt", ""),
                                                  lookup.get(row.get("evidence_excerpt", ""))),
            asins=render_review_asins(row.get("example_asins", "")),
        )
        for row in rows
    )


def render_insight_citation(citation: dict, translations: dict | None = None) -> str:
    sources = []
    for source in citation.get("sources", []):
        rating = to_float(source.get("rating"))
        rating_text = f"原评分 {rating:g} 星" if 0 < rating <= 5 else "原评分未提供"
        sources.append(f'{render_review_asins(source.get("asin", ""))} · {rating_text} · '
                       f'{escape(str(source.get("review_date") or "日期未记录"))}')
    quote = str(citation.get("quote") or citation.get("review_text") or citation.get("review_title") or "")
    translation_entry = citation
    if not citation.get("translation_zh"):
        for entry in (translations or {}).get("entries", []):
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source_excerpt") or "")
            if source and (source == quote or (entry.get("source_truncated") and quote.startswith(source))):
                quote, translation_entry = source, entry
                break
    translation = render_review_translation(quote, translation_entry)
    return f'''<div class="review-citation">
      <p class="review-source">{'<br>'.join(sources)}</p>
      <p class="review-original">{escape(quote)}</p>{translation}
      <details class="review-full"><summary>完整评论</summary>
        <b>{escape(str(citation.get('review_title') or '无标题'))}</b>
        <p class="review-original">{escape(str(citation.get('review_text') or '正文未提供'))}</p>
      </details></div>'''


def render_semantic_insights(items: list[dict]) -> str:
    rows = []
    for item in items:
        details = ""
        if item["kind"] == "pain":
            basis = "用户明确提出" if item["need_basis"] == "explicit_request" else "具体使用受阻"
            details = f'<p class="review-basis">需求依据：{basis}</p>'
            for field, label in (("scenario_zh", "使用场景"), ("need_zh", "需要什么"),
                                 ("gap_zh", "尚未满足"), ("validation_zh", "仍需验证")):
                details += f'<p class="review-need"><span>{label}</span>{escape(item[field])}</p>'
        citations = [render_insight_citation(source) for source in item["evidence"]]
        evidence = citations[0]
        if len(citations) > 1:
            evidence += f'<details class="review-more"><summary>其余 {len(citations) - 1} 段原文</summary>{"".join(citations[1:])}</details>'
        rows.append(f'''<tr>
          <td data-label="观点 / 需求"><b>{escape(item['summary_zh'])}</b>{details}</td>
          <td data-label="已核引用" class="num">{item['mention_count']} 条</td>
          <td data-label="原文与中文" class="review-excerpt">{evidence}</td>
        </tr>''')
    return "".join(rows)


def render_review_evidence(rows: list[dict[str, str]], translations: dict | None = None,
                           analysis: dict | None = None) -> str:
    groups = (analysis or {}).get("groups", {key: [] for key in ("positive", "negative", "pain")})

    def table(items: list[dict[str, str]], label: str, empty: str) -> str:
        if not items:
            return f'<p class="review-empty">{escape(empty)}</p>'
        return f'''<div class="table-wrap review-table-wrap" role="region" aria-label="{escape(label)}证据" tabindex="0">
          <table class="review-table review-insight-table">
            <thead><tr><th scope="col">观点 / 需求</th><th scope="col" class="num">已核引用</th><th scope="col">原文与中文</th></tr></thead>
            <tbody>{render_semantic_insights(items)}</tbody>
          </table>
        </div>'''

    definitions = (
        ("positive", "好评", "已满足的需求：用户明确认可的功能、品质或使用体验，低星评论也可能包含。", "尚未整理出已核验的正面体验，不按高星数量代替。"),
        ("negative", "差评", "现有产品没做好的地方：如材质、做工、密封或清洗体验；高星评论里的缺点同样计入。", "尚未整理出已核验的产品不足，不代表产品没有缺点。"),
        ("pain", "痛点", "用户实际需要、但当前产品尚未满足的需求。须有具体场景与缺口证据，可来自任意星级；不是差评的子集，也不自动等于市场机会。", "尚未整理出有场景和未满足证据的需求，不把一般抱怨改名为痛点。"),
    )
    tabs, panels = [], []
    for index, (key, label, note, empty) in enumerate(definitions):
        tabs.append(f'''<button type="button" id="review-tab-{key}" role="tab" aria-controls="review-panel-{key}" aria-selected="{'true' if index == 0 else 'false'}" tabindex="{0 if index == 0 else -1}">{label}<span>{len(groups[key])} 项</span></button>''')
        panels.append(f'''<div id="review-panel-{key}" role="tabpanel" aria-labelledby="review-tab-{key}" tabindex="0"{'' if index == 0 else ' hidden'}>
          <p class="review-note">{note}</p>{table(groups[key], label, empty)}
        </div>''')
    pending, coverage = "", ""
    if analysis is not None:
        coverage = (f"原始 {analysis['raw_count']} 条，按标题和正文去重后 {analysis['unique_count']} 条；"
                    f"以上观点已核引用 {analysis['cited_count']} 条。引用为选取的证据，不是穷尽频次或独立买家数。")
        if analysis.get("reviewed_on"):
            coverage += f" 观点核验 {escape(analysis['reviewed_on'])}，不等于市场数据刷新。"
        if analysis["pending"]:
            raw = "".join(render_insight_citation(item, translations) for item in analysis["pending"])
            pending = f'''<details class="review-unclassified"><summary>其他原文（尚未纳入以上观点）· {len(analysis['pending'])} 条</summary>
              <div class="review-pending">{raw}</div></details>'''
        if analysis.get("errors"):
            coverage += " " + escape(" ".join(analysis["errors"]))
        if analysis["status"] in ("pending", "stale"):
            coverage += " 本页具体观点待语义核验，旧星级和关键词分类不作为三类结论。"
    elif rows:
        pending = f'''<details class="review-unclassified"><summary>旧主题统计（待语义核验）· {len(rows)} 项</summary>
          <div class="table-wrap review-table-wrap"><table class="review-table"><tbody>{render_demand(rows, translations)}</tbody></table></div></details>'''
    return f'''<section id="review-evidence" data-review-evidence>
      <h2>用户需求与评论证据</h2>
      <div class="review-tabs" role="tablist" aria-label="评论分类">{''.join(tabs)}</div>
      <p class="review-note">按具体观点分类，不按星级分类；同一条评论可同时贡献优点、缺点与未满足需求。页签计数为观点数。</p>
      {''.join(panels)}<p class="review-note">{coverage}</p>{pending}
    </section>'''


def render_review_targets(rows: list[dict[str, str]]) -> str:
    html = []
    for row in rows:
        html.append(
            """
            <tr>
              <td><a href="{url}" target="_blank" rel="noreferrer">{asin}</a><span>{title}</span></td>
              <td>{form}</td>
              <td class="num">{sales}</td>
              <td class="num">{reviews}</td>
              <td class="num">{read}</td>
            </tr>
            """.format(
                url=escape(row.get("listing_url", "")),
                asin=escape(row.get("asin", "")),
                title=escape(short(row.get("title", ""), 70)),
                form=escape(row.get("product_form", "")),
                sales=fmt_int(row.get("monthly_sales")),
                reviews=fmt_int(row.get("reviews")),
                read=fmt_int(row.get("review_rows_collected")),
            )
        )
    return "\n".join(html)


def render_gallery(products: list[dict[str, str]]) -> str:
    from research_images import image_urls

    html = []
    for row in products:
        image = row.get("web_image_url", "") or asset_path(row.get("image_file", ""))
        image = image or next(iter(image_urls(row.get("main_image_url"))), "")
        form = row.get("product_form") or row.get("visual_product_form", "")
        label = row.get("product_form_label") or form
        missing = {"no_image": "Sorftime 暂缺图片", "detail_failed": "详情获取失败",
                   "download_failed": "图片下载失败"}.get(row.get("image_status"), "待补图")
        search_text = " ".join(
            str(row.get(field) or "")
            for field in ("asin", "title", "visual_product_form", "product_form", "product_form_label", "visual_material_signal", "material")
        ).lower()
        html.append(
            """
            <article class="tile" data-gallery-item data-asin="{asin}" data-has-image="{has_image}" data-form="{form_attr}" data-search="{search_attr}">
              <a href="{url}" target="_blank" rel="noreferrer">
                {image_html}
                <b>{asin}</b>
                <span>{title}</span>
              </a>
              <p>{form} · {material} · {pack} pack</p>
            </article>
            """.format(
                url=escape(row.get("listing_url", "")),
                image_html=f'<img src="{escape(image)}" loading="lazy" decoding="async" alt="{escape(row.get("asin", ""))}">' if image else f'<div class="image-empty">{missing}</div>',
                has_image="true" if image else "false",
                asin=escape(row.get("asin", "")),
                title=escape(short(row.get("title", ""), 72)),
                form=escape(label),
                form_attr=escape(form),
                search_attr=escape(search_text),
                material=escape(row.get("visual_material_signal") or row.get("material", "")),
                pack=escape(str(row.get("visual_pack_count") or row.get("pack_count") or "-")),
            )
        )
    return "\n".join(html)


def render_gallery_form_options(products: list[dict[str, str]]) -> str:
    forms = []
    seen = set()
    for row in products:
        form = str(row.get("product_form") or row.get("visual_product_form") or "").strip()
        if form and form not in seen:
            label = row.get("product_form_label") or form
            forms.append((form, label))
            seen.add(form)
    return "".join(f'<option value="{escape(form)}">{escape(label)}</option>' for form, label in forms)


def render_gallery_scopes(snapshot_rows, current_rows):
    html = []
    for scope, rows in (("snapshot", snapshot_rows), ("current", current_rows)):
        hidden = " hidden" if scope == "current" else ""
        html.append(f'<template id="galleryOptions-{scope}"><option value="">全部产品形态</option>'
                    f'{render_gallery_form_options(rows)}</template>'
                    f'<div class="gallery" data-gallery-scope="{scope}"{hidden}>'
                    f'{render_gallery(rows)}</div>')
    return "".join(html)


def render_summary_list(items: list[object]) -> str:
    return "".join(f"<li>{escape(str(item))}</li>" for item in items)


def prepare_web_assets(asin: str, products: list[dict[str, str]], research_dir: Path) -> str:
    asset_dir = WEB_RESEARCH_DIR / "assets" / asin
    asset_dir.mkdir(parents=True, exist_ok=True)
    for row in products:
        source_text = row.get("image_file", "")
        if not source_text:
            continue
        source = Path(source_text)
        if not source.is_absolute():
            source = ROOT / source
        if not source.exists():
            continue
        destination = asset_dir / source.name
        shutil.copyfile(source, destination)
        row["web_image_url"] = f"assets/{asin}/{destination.name}"
    contact_source = research_dir / "image_contact_sheet.jpg"
    if not contact_source.exists():
        return ""
    contact_destination = asset_dir / "image_contact_sheet.jpg"
    shutil.copyfile(contact_source, contact_destination)
    return f"assets/{asin}/{contact_destination.name}"


def render_bullets(items: list[object]) -> str:
    return "".join(f"<li>{escape(str(item))}</li>" for item in items)


def render_deep_analysis(deep: dict) -> str:
    if not deep:
        return ""
    facts = deep.get("facts") or {}
    market = deep.get("market") or {}
    trend = deep.get("trend") or {}
    review = deep.get("review_summary") or {}
    supply = deep.get("supply") or {}
    compliance = deep.get("compliance") or {}
    references = "".join(
        f'<a href="{escape(str(row.get("url", "")))}" target="_blank" rel="noreferrer">{escape(str(row.get("label", "参考")))}</a>'
        for row in compliance.get("references", [])
        if isinstance(row, dict) and row.get("url")
    )
    recent_sales = trend.get("recent_three_month_avg_sales") or 0
    return f"""
    <section class="verdict-panel">
      <div>
        <span class="eyebrow">深度研究结论</span>
        <h2>{escape(str(deep.get('verdict', '待复核')))}</h2>
        <p>{escape(str(deep.get('one_line', '')))}</p>
      </div>
      <div class="score-box"><span>机会分</span><strong>{fmt_int(deep.get('score'))}</strong><small>置信度 {escape(str(deep.get('confidence', '-')))}</small></div>
    </section>

    <section>
      <h2>产品与趋势</h2>
      <div class="kpis">
        <div class="kpi"><span>优惠后售价</span><strong>{fmt_money(facts.get('effective_price'))}</strong></div>
        <div class="kpi"><span>当前月销</span><strong>{fmt_int(facts.get('monthly_sales'))}</strong></div>
        <div class="kpi"><span>近 3 月均销</span><strong>{fmt_int(recent_sales)}</strong></div>
        <div class="kpi"><span>FBA 费 / 重量</span><strong>{fmt_money(facts.get('fba_fee'))} / {fmt_int(facts.get('weight_g'))}g</strong></div>
      </div>
      <p class="subtle">{escape(str(trend.get('summary', '')))}</p>
    </section>

    <section>
      <h2>真实竞争结构</h2>
      <div class="kpis">
        <div class="kpi"><span>同形态样本</span><strong>{fmt_int(market.get('direct_competitor_count'))}</strong></div>
        <div class="kpi"><span>同形态样本月销</span><strong>{fmt_int(market.get('direct_form_monthly_sales'))}</strong></div>
        <div class="kpi"><span>目标 ASIN 占样本销量</span><strong>{to_float(market.get('seed_share_of_direct_sample')) * 100:.1f}%</strong></div>
        <div class="kpi"><span>关键词搜索量 / CPC</span><strong>{fmt_int(market.get('keyword_monthly_search_volume'))} / {fmt_money(market.get('keyword_cpc'))}</strong></div>
      </div>
      <p class="warning">{escape(str(market.get('keyword_caveat', '')))}</p>
    </section>

    <section class="analysis-grid">
      <article><h2>可切入点</h2><ul>{render_bullets(deep.get('opportunities', []))}</ul></article>
      <article><h2>主要风险</h2><ul>{render_bullets(deep.get('risks', []))}</ul></article>
      <article><h2>评论结论</h2><p>{escape(str(review.get('customers_say', '')))}</p><ul>{render_bullets(review.get('core_negative', []))}</ul></article>
      <article><h2>供应链与合规</h2><p>{escape(str(supply.get('assessment', '')))}</p><p>{escape(str(compliance.get('summary', '')))}</p><div class="reference-links">{references}</div></article>
    </section>

    <section>
      <h2>下一步验证</h2>
      <ol class="action-list">{render_bullets(deep.get('next_actions', []))}</ol>
    </section>
    """


def render_required_checks(required: dict) -> str:
    articles = []
    for key, item in required["checks"].items():
        status = "已初查" if item.get("review_status") == "reviewed" else "待补检"
        risk = {"high": "高风险", "medium": "需验证", "low": "较低风险", "unknown": "证据不足"}.get(item.get("risk"), "需验证")
        evidence = '<ul>' + render_bullets(item.get("evidence", [])) + '</ul>'
        sources = " ".join(f'<a href="{escape(row["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{escape(row["title"])}</a>' for row in item.get("sources", []))
        extras = ""
        brands = item.get("brand_concentration", {})
        if brands.get("top1_share") is not None:
            extras += f'<p>同形态 Top1 品牌：{escape(brands["top_brand"])}，占 {brands["top1_share"]:.1%}；Top3 占 {brands["top3_share"]:.1%}。</p><p class="subtle">{escape(brands["scope"])}。{escape(brands["summary"])}</p>'
        for patent in item.get("patents", []):
            extras += f'<p><a href="{escape(patent["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{escape(patent["number"])}</a> · {escape(patent["status"])}<br>{escape(patent["relevance"])}</p>'
        documents = item.get("documents", [])
        if documents:
            extras += '<div class="table-wrap"><table class="check-documents"><thead><tr><th>文件或要求</th><th>适用性</th><th>当前证据</th></tr></thead><tbody>'
            extras += "".join(f'<tr><td>{escape(row["name"])}</td><td>{escape(row["requirement"])}</td><td>{escape(row["status"])}</td></tr>' for row in documents)
            extras += '</tbody></table></div>'
        if key == "seasonality" and item.get("metrics"):
            series = item["metrics"]["monthly_indices"]
            extras += '<div class="season-indices" role="img" aria-label="按年度月均销量归一后的月份指数，1代表该周期平均水平">'
            extras += "".join(f'<div><strong>{row["index"]:.2f}</strong><div class="season-bar"><i style="height:{min(row["index"] * 45, 100):.1f}%"></i></div><span>{row["month"]}月</span></div>' for row in series)
            extras += '</div><p class="subtle">月份指数：1 = 该周期月均销量。数据范围为上方注明的单品或类目。</p>'
        articles.append(f'<article id="check-{key}" class="required-check"><h3>{CHECK_TITLES[key]} <span class="check-status">{status} · {risk}</span></h3><p>{escape(item["summary"])}</p>{evidence}{extras}<ul>{render_bullets(item.get("next_steps", []))}</ul><div class="reference-links">{sources}</div><p class="subtle">核验日期：{escape(item.get("reviewed_on") or "尚未核验")}</p></article>')
    return '<section id="required-checks" class="required-checks"><h2>单品研究必查项</h2>' + "".join(articles) + '</section>'


def render_supplier_evidence(quotes: list[dict[str, str]], sources: dict) -> str:
    raw = sources.get("supplier_candidates")
    search_status = (
        f"以图搜索已返回 {len(raw)} 条原始结果；商品线索、规格候选与有效同款报价分开记录。"
        if isinstance(raw, list) else "未记录 1688 原始检索结果，不能据此判断没有货源。"
    )
    rows = []
    for quote in quotes:
        if quote.get("source_platform") != "1688":
            continue
        product_id = str(quote.get("source_product_id") or "")
        supplier = escape(str(quote.get("supplier") or product_id))
        if product_id.isdigit():
            supplier = f'<a href="https://detail.1688.com/offer/{product_id}.html" target="_blank" rel="noreferrer">{supplier}</a>'
        sku = quote.get("source_sku_name") or "未核对到具体 SKU"
        status = {
            "candidate": "规格待核", "same_product": "同款已核对",
            "same_product_component": "同款组件已核对", "material_mismatch": "材质不匹配",
            "dimension_mismatch": "尺寸不匹配", "rejected": "已排除", "unavailable": "未返回报价",
        }.get(quote.get("match_status"), "未核验")
        rows.append(
            f'<tr><td>{supplier}<div class="subtle">{escape(str(quote.get("quoted_at") or ""))}</div></td>'
            f'<td>{escape(str(sku))}<div class="subtle">{escape(str(quote.get("source_sku_id") or ""))}</div></td>'
            f'<td class="num">{fmt_cny(quote.get("source_unit_price"))}</td>'
            f'<td><strong>{status}</strong><div>{escape(str(quote.get("match_reason") or ""))}</div></td></tr>'
        )
    table = (
        '<div class="table-wrap"><table><thead><tr><th>1688 供应商 / 查询日期</th><th>SKU / 规格</th>'
        '<th>报价 CNY</th><th>核对结果</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>'
        if rows else ""
    )
    return '<div id="supplierEvidence"><h3>1688 检索与规格核对</h3><p class="subtle">' + escape(search_status) + '</p>' + table + '</div>'


def render_landscape_products(products: list[dict], image_lookup: dict) -> str:
    from research_images import image_urls
    from research_landscape import listing_date, optional_number

    cards = []
    for product in products:
        asin = str(product["asin"])
        title = str(product.get("title") or asin)
        cached = image_lookup.get(asin, {})
        urls = image_urls(cached.get("main_image_url"))
        image = cached.get("web_image_url", "") or next(iter(urls), "")
        visual = (
            f'<img src="{escape(image, quote=True)}" alt="{escape(title, quote=True)}" '
            'width="120" height="120" loading="lazy" decoding="async">'
            if image else '<span class="landscape-image-empty">待补图</span>'
        )
        reason = str(product.get("excluded_reason") or "")
        pending = str(product.get("pending_reason") or "")
        status = product.get("screening_status", "")
        if reason == "missing or invalid category evidence":
            reason, pending, status = "", "缺少有效类目信息，需补证据后判断", "needs_evidence"
        if reason:
            assessment = f'<p class="landscape-product-risk">已排除：{escape(reason)}</p>'
        elif pending:
            assessment = f'<p class="landscape-product-pending">待补证据：{escape(pending)}</p>'
        elif status == "eligible":
            assessment = '<p class="landscape-product-eligible">通过基础筛选，尚非机会结论</p>'
        else:
            assessment = ''
        categories = {}
        for entry in product.get("category_evidence", []):
            if entry.get("category_path"):
                categories.setdefault(entry["category_path"], []).append(entry.get("path") or "研究快照")
        category_html = ''.join(
            f'<p class="landscape-product-category" title="{escape("来源：" + "；".join(sorted(set(paths))), quote=True)}">'
            f'类目：{escape(category)}</p>' for category, paths in categories.items()
        )
        sales = optional_number(product.get("monthly_sales"))
        price = optional_number(product.get("price"))
        online_date = listing_date(product.get("online_date"))
        metrics = []
        dates = set()
        for field, label, value in (
            ("monthly_sales", "月销", f"{sales:,.0f}" if sales is not None else "未提供"),
            ("price", "价格", f"${price:,.2f}" if price is not None else "未提供"),
            ("online_date", "上架", online_date or "未提供"),
        ):
            source = product.get("metric_sources", {}).get(field, {})
            retrieved = listing_date(source.get("retrieved_on"))
            if retrieved:
                dates.add(retrieved)
            detail = f'来源：{source.get("path") or "研究快照"}；采集日期：{retrieved or "未记录"}'
            metrics.append(f'<span class="landscape-product-metric" title="{escape(detail, quote=True)}">{label} {value}</span>')
        snapshot = " / ".join(sorted(dates)) or "日期未记录"
        cards.append(
            f'<article class="landscape-product" data-reference-asin="{escape(asin, quote=True)}">'
            f'<a class="landscape-product-link" href="https://www.amazon.com/dp/{escape(asin, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer" title="{escape(title, quote=True)}">'
            f'<span class="landscape-thumb">{visual}</span><span class="landscape-product-info">'
            f'<strong class="landscape-product-title">{escape(title)}</strong>'
            f'<span class="landscape-asin">{escape(asin)}</span>'
            + "".join(metrics)
            + f'<span class="landscape-snapshot">采集 {snapshot}</span></span></a>'
            + assessment + category_html
            + '</article>'
        )
    return '<div class="landscape-products">' + "".join(cards) + '</div>'


def render_landscape(landscape: dict, gallery_rows=()) -> str:
    image_lookup = {row["asin"]: row for row in gallery_rows if row.get("asin")}
    lines = []
    for form in landscape.get("forms", []):
        packs = "".join(
            f'<tr><td>{escape(str(p["quantity"]))} 件/组件</td><td>{fmt_int(p["count"])}</td>'
            f'<td>{fmt_int(p["monthly_sales"])}</td><td>{fmt_money(p["median_price"])}</td></tr>'
            for p in form["packs"]
        )
        references = render_landscape_products(form["products"], image_lookup)
        target = ' · 当前研究形态' if form["is_target"] else ''
        lines.append(f'''<tr class="landscape-metrics"><td><strong>{escape(form["label"])}{target}</strong>
          <p class="subtle">{escape(form["next_check"])}</p></td>
          <td class="num" data-label="样本 / 通过基础筛选">{form["count"]} / {form["eligible_count"]}
          <div class="subtle">待补 {form.get("pending_count", 0)} · 排除 {form.get("excluded_count", 0)}</div></td>
          <td class="num" data-label="总月销">{fmt_int(form["monthly_sales"])}</td><td class="num" data-label="组内销量份额">{form["sales_share"]:.1%}</td>
          <td class="num" data-label="单品月销中位">{fmt_int(form["median_monthly_sales"])}</td><td class="num" data-label="价格中位">{fmt_money(form["median_price"])}</td>
          <td class="num" data-label="评论中位">{fmt_int(form["median_reviews"])}</td>
          <td class="num" data-label="新品达标 / 新品">{form["new_entrants"]["new_entrant_success_count"]} / {form["new_entrants"]["new_entrant_count"]}</td></tr>
          <tr class="landscape-detail-row"><td colspan="8"><details class="landscape-details">
          <summary>套装分布与参考产品</summary><table class="landscape-packs">
          <thead><tr><th>标题数量</th><th>样本</th><th>月销</th><th>价格中位</th></tr></thead><tbody>{packs}</tbody></table>
          {references}</details></td></tr>''')
    coverage = "；".join(
        f'{"类目 Top100" if source["kind"] == "category_top100" else "关键词 " + source.get("keyword", "")}：{source["count"]} 条'
        for source in landscape.get("sources", [])
    ) or "仅现有研究样本，尚无类目/关键词遍历记录"
    keyword_note = "关键词产品搜索尚未记录。" if landscape.get("keyword_coverage") == "not_recorded" else ""
    return f'''<section id="product-landscape"><h2>同用途产品全景：{escape(landscape.get("family_label", "用途待确认"))}</h2>
      <p>{escape(coverage)}。去重后 {landscape["source_product_count"]} 个商品，标题归入同用途组 {landscape["related_product_count"]} 个，
      其他用途或尚未确认 {landscape["other_or_unresolved_count"]} 个。{keyword_note}</p>
      <p class="subtle">{escape(landscape["scope_note"])} {escape(landscape["decision_note"])}</p>
      <div class="table-wrap"><table class="landscape-summary"><thead><tr><th>形态 / 待核切入点</th><th>样本 / 通过基础筛选</th>
      <th>总月销</th><th>组内销量份额</th><th>单品月销中位</th><th>价格中位</th><th>评论中位</th><th>新品达标 / 新品</th></tr></thead>
      <tbody>{"".join(lines) or '<tr><td colspan="8">用途尚未确认，需先核对标题和主图。</td></tr>'}</tbody></table></div></section>'''


def render_new_entrant_analysis(analysis: dict, gallery_rows=()) -> str:
    from research_images import image_urls

    lookup = {r["asin"]: r for r in gallery_rows if r.get("asin")}
    labels = {
        "sales_scope_unverified": "子款销量待确认", "brand_dependent": "品牌依赖较高",
        "price_pressure": "低价与利润待核", "inherited_listing": "老链接扩款",
        "differentiation_to_validate": "差异化待验证", "needs_attribution": "起量归因待核验",
        "screening_pending": "基础筛选未通过", "needs_refresh": "数据待刷新",
        "independent_entry_supported": "有独立切入证据",
    }
    source_labels = {"detail": "详情", "variations": "变体销量", "price": "价格趋势", "sales": "销量趋势", "reviews": "评论"}
    cards = []
    for p in analysis["products"]:
        asin, note = p["asin"], p["review_notes"]
        title = str(p.get("title") or asin)
        cached = lookup.get(asin, {})
        url = cached.get("web_image_url") or next(iter(image_urls(cached.get("main_image_url"))), "")
        visual = (f'<img src="{escape(url, quote=True)}" alt="{escape(title, quote=True)}" width="120" height="120" loading="lazy">'
                  if url else '<span class="landscape-image-empty">待补图</span>')
        factors = ''.join(f'<div><h4>{escape(f["label"])}</h4><p>{escape(f["finding"])}</p></div>' for f in note.get("factors", []))
        if not factors:
            factors = '<p>尚未完成品牌、功能差异、价格与流量证据核验；不能仅按销量高或标题有新功能判断机会。</p>'
        bullets = ''.join(f'<li>{escape(x)}</li>' for x in note.get("evidence_notes", []))
        next_steps = ''.join(f'<li>{escape(x)}</li>' for x in note.get("next_checks", []))
        price_by_month = {x["month"]: x["value"] for x in p["price_series"]}
        sales_by_month = {x["month"]: x["value"] for x in p["sales_series"]}
        months = sorted(set(price_by_month) | set(sales_by_month))
        rows = ''.join(
            f'<tr><td>{m}{"（上架首月）" if m == p["online_date"][:7] else ""}</td>'
            f'<td>{"$" + format(price_by_month[m], ".2f") if m in price_by_month else "未提供"}</td>'
            f'<td>{format(sales_by_month[m], ",.0f") if m in sales_by_month else "未提供"}</td></tr>' for m in months
        )
        trend = (f'<table class="entrant-trends"><thead><tr><th>月份</th><th>均价</th><th>链接口径销量</th></tr></thead><tbody>{rows}</tbody></table>'
                 if rows else '<p>暂无有效历史趋势。</p>')
        sources = []
        for s in note.get("sources", []):
            link = s.get("url", "")
            label = escape(s["title"])
            if link.startswith("https://"):
                label = f'<a href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">{label}</a>'
            sources.append(f'<li>{label} {escape(s.get("note", ""))}</li>')
        source_dates = "；".join(f'{source_labels[s["kind"]]} {s["retrieved_on"]}' for s in p["sources"])
        badge = f'{p["child_sales_badge"]:,.0f}' if p["child_sales_badge"] is not None else "未取得"
        headline = note.get("main_driver") or labels[p["entry_gate"]]
        cards.append(f'''<article class="entrant-review" data-entrant-asin="{asin}">
          <header class="entrant-header"><a class="landscape-thumb" href="https://www.amazon.com/dp/{asin}" target="_blank" rel="noopener noreferrer">{visual}</a>
          <div><h3>{escape(p["brand"])} · {escape(headline)}</h3>
          <a href="https://www.amazon.com/dp/{asin}" target="_blank" rel="noopener noreferrer" title="{escape(title, quote=True)}">{escape(title)}</a>
          <p class="subtle">{asin} · {escape(p["form_label"])} · 上架 {p["online_date"]}</p></div></header>
          <div class="entrant-metrics"><div>初筛月销（口径待核）<strong>{p["monthly_sales"]:,.0f}</strong><small>快照 {escape(p["snapshot_date"])}</small></div>
          <div>目标子款公开销量档位<strong>{badge}</strong><small>非精确月销，近15天内捕获</small></div>
          <div>快照售价<strong>{fmt_money(p["price"])}</strong><small>{escape(str(p.get("pack_count") or "未注明"))} 件/组件</small></div></div>
          <p class="entrant-conclusion"><strong>{labels[p["entry_gate"]]}：</strong>{escape(p["entry_conclusion"])}</p>
          <div class="entrant-factors">{factors}</div>
          <p class="subtle">{escape(note.get("confidence", "证据不足，未做归因"))}</p>
          {f'<h4>待核切入口</h4><ul>{next_steps}</ul>' if next_steps else ''}
          <details class="entrant-evidence"><summary>趋势、评论口径与来源</summary>
          <p>父体 {escape(p["parent_asin"] or "未提供")}；返回变体 {p["variants_returned"]} / {p["variation_total"] or "未提供"}。
          评论样本 {p["reviews_returned"]} 条，其中 {p["reviews_before_launch"]} 条早于本 ASIN 上架；最新评论 {escape(p["latest_review_date"] or "未提供")}。</p>
          <p class="subtle">趋势未证实为该子款独立销量，不能用于计算子款增长率；当月已剔除，上架首月可能不完整。</p>
          {trend}<ul>{bullets}</ul><ul>{''.join(sources)}</ul>
          <p class="subtle">{escape(source_dates or "尚未采集归因证据")}；分析 {analysis["as_of"]}，不等于市场数据已刷新。</p>
          </details></article>''')
    implications = ''.join(f'<li><strong>{escape(f["label"])}</strong>：{f["count"]} 个初筛新品，{escape(f["conclusion"])}</li>' for f in analysis["forms"])
    empty = '<p>本次同用途样本中没有符合条件的记录；缺失上架时间或销量的商品未计入，不代表该形态没有机会。</p>'
    return f'''<section id="new-entrant-analysis"><h2>近半年新品起量核验</h2>
      <p>{analysis["window_start"]} 至 {analysis["as_of"]} 上架、快照月销 &gt; 300：共 {len(analysis["products"])} 个，
      已完成证据归因 {sum(p["reviewed"] for p in analysis["products"])} 个。</p>
      <p class="subtle">{escape(analysis["method_note"])}</p><ul>{implications}</ul>
      {''.join(cards) or empty}<p class="subtle">{escape(analysis["limits"])}</p></section>'''


def build_page(index_row: dict[str, str]) -> str:
    from review_insights import compile_review_insights
    from supplier_screening import build_screening, render_supplier_screening
    from research_landscape import build_landscape
    from research_images import gallery_products
    from research_dimensions import analyze_dimensions
    from research_bundles import analyze_bundles
    from research_dimension_view import render_dimensions, render_bundles
    from new_entrant_research import analyze_new_entrants, apply_new_entrant_gate, write_json

    asin = index_row.get("asin", "")
    research_dir = ROOT / (index_row.get("research_dir") or f"research/{asin}")
    supplier_screening_html = render_supplier_screening(build_screening(research_dir), asin)
    forms = read_csv(research_dir / "product_forms.csv")
    products = read_csv(research_dir / "top_products.csv")
    reviews = read_csv(research_dir / "reviews.csv")
    review_insights = compile_review_insights(reviews, read_json(research_dir / "review_insights.json"))
    targets = read_csv(research_dir / "review_targets.csv")
    demand = read_csv(research_dir / "demand_analysis.csv")
    price_bands = read_csv(research_dir / "price_bands.csv")
    market = read_json(research_dir / "market_structure.json")
    business = read_json(research_dir / "business_feasibility.json")
    supplier_evidence_html = render_supplier_evidence(
        read_csv(research_dir / "supplier_quotes.csv"), read_json(research_dir / "research_sources.json"),
    )
    business_rules = read_json(ROOT / "config" / "business_feasibility_rules.json")
    summary = read_json(research_dir / "research_summary.json")
    landscape = build_landscape(ROOT, research_dir)
    entrants = analyze_new_entrants(ROOT, research_dir, landscape)
    if research_dir.is_dir():
        write_json(research_dir / "new_entrant_analysis.json", entrants)
        (research_dir / "product_landscape.json").write_text(json.dumps(landscape, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    required = build_required_checks(summary, products, read_json(research_dir / "research_sources.json"), read_json(research_dir / "risk_review.json"))
    if summary:
        summary = apply_research_gate(summary, required, business)
        summary = apply_new_entrant_gate(summary, entrants)
    contact_sheet = prepare_web_assets(asin, products, research_dir)
    gallery_rows = gallery_products(products, landscape, read_json(research_dir / "image_manifest.json"))
    prepare_web_assets(asin, gallery_rows, research_dir)
    snapshot_gallery_rows = gallery_products(products, landscape,
        read_json(research_dir / "image_manifest.json"), scope="snapshot")
    prepare_web_assets(asin, snapshot_gallery_rows, research_dir)
    dimensions = analyze_dimensions(ROOT, research_dir, landscape, reviews)
    bundles = analyze_bundles(ROOT, research_dir, landscape, products, reviews)
    if research_dir.is_dir():
        write_json(research_dir / "dimension_analysis.json", dimensions)
        write_json(research_dir / "bundle_analysis.json", bundles)
    # research_summary.json is the current generic MCP format. deep_analysis.json
    # may be a legacy, product-specific artifact from an older research engine.
    deep = {} if summary else read_json(research_dir / "deep_analysis.json")
    seed = next((row for row in products if row.get("competitor_type") == "seed"), products[0] if products else {})
    profit_defaults = build_profit_calculator_defaults(
        seed,
        business,
        business_rules,
        summary.get("product", {}),
    )
    profit_defaults_json = json.dumps(profit_defaults, ensure_ascii=False)
    title = index_row.get("title") or seed.get("title") or asin
    listing_url = index_row.get("listing_url") or seed.get("listing_url") or f"https://www.amazon.com/dp/{asin}"
    report_source = ROOT / (index_row.get("report_path") or f"reports/product_opportunity_research_{asin}.md")
    report_link = f"reports/{asin}.md"
    if report_source.is_file():
        report_destination = WEB_RESEARCH_DIR / report_link
        report_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report_source, report_destination)
    contact_html = (
        f'<section><h2>主图总览</h2><img class="contact" src="{escape(contact_sheet)}" alt="{escape(asin)} image contact sheet"></section>'
        if contact_sheet
        else ""
    )
    low_reviews = sum(1 for row in reviews if 0 < to_float(row.get("rating")) <= 3)
    high_reviews = sum(1 for row in reviews if to_float(row.get("rating")) >= 4)
    generated = index_row.get("last_researched", "")
    top10_share = to_float(market.get("top10_sales_share")) * 100
    new_share = to_float(market.get("new_listing_share_12m")) * 100
    quote_range = business.get("procurement_price_range_cny") or []
    quote_range_text = (
        f"{fmt_cny(quote_range[0])}-{fmt_cny(quote_range[1])}"
        if isinstance(quote_range, list) and len(quote_range) == 2
        else "-"
    )
    profitability_verified = bool(business.get("profitability_verified"))
    status_labels = {
        "1688_cost_benchmarked": "1688 同款成本基准已建立",
        "needs_1688_same_product_quotes": "利润未验证",
    }
    source_labels = {
        "1688_same_product_median": "采购成本采用 1688 同款中位数",
        "missing_1688_same_product_median": "缺少 1688 同款中位数",
    }
    business_status_text = status_labels.get(str(business.get("status") or ""), str(business.get("status") or "利润未验证"))
    business_source_text = source_labels.get(str(business.get("data_source") or ""), str(business.get("data_source") or "缺少 1688 同款中位数"))
    quote_links = [url for url in business.get("quote_urls", []) if isinstance(url, str) and url.startswith("https://")]
    quote_link_label = "1688 有效证据："
    if not quote_links:
        quote_links = [
            url for url in business.get("candidate_quote_urls", [])
            if isinstance(url, str) and url.startswith("https://")
        ]
        quote_link_label = "1688 待核规格候选："
    quote_links_html = "".join(
        f'<a href="{escape(url)}" target="_blank" rel="noreferrer">报价 {index}</a>'
        for index, url in enumerate(quote_links[:6], 1)
    )
    quote_links_html = (
        f'<div class="reference-links"><span>{quote_link_label}</span>{quote_links_html}<span>共 {len(quote_links)} 条</span></div>'
        if quote_links_html
        else ""
    )
    profitability_notice = "" if profitability_verified else (
        '<section class="decision rejected"><strong>利润未验证，不能据此立项</strong>'
        '<span>尚未取得至少 3 条经过去重、同款且核对到目标规格 SKU 的 1688 报价；商品最低展示价不计入利润。</span></section>'
    )
    archive_status = index_row.get("status", "")
    archive_notes = index_row.get("notes", "")
    if archive_status == "rejected":
        decision_html = (
            '<section class="decision rejected"><strong>当前结论：已淘汰</strong>'
            f'<span>{escape(archive_notes or "未通过当前风险规则")}</span></section>'
        )
    elif summary:
        decision_score = summary.get("decision_score", "-")
        score_label = f"{decision_score}/100" if number_or_none(decision_score) is not None else str(decision_score)
        decision_html = (
            '<section class="decision"><strong>当前结论：{decision}（{score}）</strong>'
            '<span>{reason}</span></section>'
        ).format(
            decision=escape(str(summary.get("decision", "已研究，待复核"))),
            score=escape(score_label),
            reason=escape(str(summary.get("decision_reason", ""))),
        )
    else:
        decision_html = (
            '<section class="decision"><strong>档案状态：已研究，待结合当前机会池复核</strong>'
            f'<span>{escape(archive_notes)}</span></section>'
        )
    if "Listing 有效性需复核" in archive_notes:
        listing_action = '<span class="pill disabled">Listing 不可用/待复核</span>'
    else:
        listing_action = f'<a class="pill" href="{escape(listing_url)}" target="_blank" rel="noreferrer">打开 Listing</a>'

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <link rel="stylesheet" href="../supplier-screening.css?v=1">
  <link rel="stylesheet" href="../research-dimensions.css?v=1">
  <script src="../research-dimensions.js?v=1" defer></script>
  <link rel="stylesheet" href="../review-evidence.css?v=3">
  <script src="../vendor/lucide.min.js" defer></script>
  <script src="../supplier-screening.js?v=1" defer></script>
  <script src="../review-evidence.js?v=1" defer></script>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(asin)} 单品研究</title>
  <link rel="icon" href="data:,">
  <style>
    body {{ margin: 0; background: #f6f7f9; color: #172033; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ max-width: 1320px; margin: 0 auto; padding: 24px; }}
    a {{ color: #2364d2; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .top {{ display: flex; justify-content: space-between; gap: 16px; margin-bottom: 18px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; line-height: 1.2; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    section {{ background: #fff; border: 1px solid #d9dee8; border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    .subtle, td span, .tile p {{ color: #657184; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .pill {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 5px 9px; background: #e8f6ef; color: #147d54; font-weight: 700; }}
    .pill.disabled {{ background: #edf0f4; color: #657184; cursor: default; }}
    .kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .kpi {{ border: 1px solid #d9dee8; border-radius: 8px; padding: 12px; background: #f9fafb; }}
    .kpi span {{ display: block; color: #657184; font-size: 12px; }}
    .kpi strong {{ display: block; margin-top: 4px; font-size: 24px; }}
    .table-wrap {{ overflow: auto; border: 1px solid #d9dee8; border-radius: 8px; }}
    table {{ width: 100%; min-width: 900px; border-collapse: collapse; background: #fff; }}
    th, td {{ padding: 10px 11px; border-bottom: 1px solid #d9dee8; text-align: left; vertical-align: top; }}
    th {{ background: #f2f5f9; color: #405066; font-size: 12px; }}
    td span {{ display: block; font-size: 12px; margin-top: 2px; }}
    .num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
    .snapshot-forms td:first-child {{ min-width: 260px; width: 32%; }}
    .snapshot-example {{ display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 12px; align-items: center; max-width: 380px; margin-top: 10px; }}
    .snapshot-example span {{ margin: 0; }}
    .snapshot-thumb, .snapshot-thumb img, .snapshot-image-empty {{ width: 88px; height: 88px; box-sizing: border-box; }}
    .snapshot-thumb img {{ display: block; object-fit: contain; background: #fff; }}
    .snapshot-image-empty {{ display: grid; place-items: center; background: #f2f5f9; font-size: 12px; }}
    .snapshot-example-meta {{ min-width: 0; }}
    .snapshot-example-meta b {{ color: #2364d2; font-size: 12px; overflow-wrap: anywhere; }}
    .snapshot-example-title {{ display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; font-size: 12px; line-height: 1.4; margin-top: 4px !important; }}
    .snapshot-example-empty {{ padding-top: 8px; }}
    .landscape-summary {{ table-layout: fixed; min-width: 1000px; }}
    .landscape-summary > thead > tr > th:first-child {{ width: 30%; }}
    .landscape-summary td:first-child {{ overflow-wrap: anywhere; }}
    .landscape-details > summary {{ cursor: pointer; padding: 8px 0; }}
    .landscape-details > summary:focus-visible, .landscape-product-link:focus-visible {{ outline: 2px solid #2563df; outline-offset: 2px; }}
    .landscape-packs {{ min-width: 0; max-width: 600px; table-layout: fixed; margin: 8px 0 14px; }}
    .landscape-products {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr)); gap: 12px; }}
    .landscape-product {{ border: 1px solid #d9dee8; border-radius: 6px; padding: 12px; min-width: 0; }}
    .landscape-product-link {{ display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 12px; align-items: start; }}
    .landscape-thumb, .landscape-thumb img, .landscape-image-empty {{ width: 120px; height: 120px; display: block; margin: 0; }}
    .landscape-thumb img {{ object-fit: contain; background: white; }}
    .landscape-image-empty {{ display: grid; place-items: center; background: #f5f6f8; color: #657184; font-size: 12px; }}
    .landscape-product-info {{ margin: 0; min-width: 0; }}
    .landscape-product-metric {{ color: #172033; font-variant-numeric: tabular-nums; }}
    .landscape-snapshot {{ color: #657184; overflow-wrap: anywhere; }}
    .landscape-product-title {{ color: #2563df; font-size: 13px; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }}
    .landscape-product-risk {{ font-size: 12px; color: #9f3935; margin: 10px 0 0; overflow-wrap: anywhere; }}
    .landscape-product-pending {{ font-size: 12px; color: #875600; margin: 10px 0 0; overflow-wrap: anywhere; }}
    .landscape-product-eligible {{ font-size: 12px; color: #087355; margin: 10px 0 0; }}
    .landscape-product-category {{ font-size: 11px; color: #647187; margin: 6px 0 0; overflow-wrap: anywhere; }}
    .entrant-review {{ border: 1px solid #d9dee8; border-radius: 6px; padding: 18px; margin: 18px 0; min-width: 0; overflow-wrap: anywhere; }}
    .entrant-header {{ display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 16px; align-items: start; }}
    .entrant-header h3 {{ font-size: 17px; margin: 0 0 8px; }}
    .entrant-header > div > a {{ display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }}
    .entrant-header img {{ object-fit: contain; }}
    .entrant-metrics {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 16px 0; padding: 12px 0; border-block: 1px solid #d9dee8; font-size: 12px; }}
    .entrant-metrics strong {{ display: block; font-size: 22px; margin: 4px 0; font-variant-numeric: tabular-nums; }}
    .entrant-metrics small {{ color: #657184; }}
    .entrant-conclusion {{ color: #875600; }}
    .entrant-factors {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 24px; }}
    .entrant-factors h4, .entrant-review h4 {{ font-size: 14px; margin: 12px 0 6px; }}
    .entrant-factors p {{ margin: 0; }}
    .entrant-evidence summary {{ cursor: pointer; padding: 10px 0; }}
    .entrant-evidence summary:focus-visible {{ outline: 2px solid #2563df; }}
    .entrant-trends {{ min-width: 0; max-width: 640px; table-layout: fixed; }}
    @media (max-width: 640px) {{
      .entrant-header {{ grid-template-columns: 80px minmax(0, 1fr); gap: 10px; }}
      .entrant-header .landscape-thumb, .entrant-header .landscape-thumb img, .entrant-header .landscape-image-empty {{ width: 80px; height: 80px; }}
      .entrant-review {{ padding: 12px; }}
      .entrant-factors {{ grid-template-columns: minmax(0, 1fr); }}
      .entrant-metrics {{ gap: 8px; }}
      .entrant-metrics strong {{ font-size: 20px; }}
    }}
    @media (max-width: 1050px) {{
      .landscape-summary {{ display: block; min-width: 0; }}
      .landscape-summary > thead {{ display: none; }}
      .landscape-summary > tbody {{ display: block; }}
      .landscape-metrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .landscape-metrics > td:first-child {{ grid-column: 1 / -1; }}
      .landscape-metrics > td {{ min-width: 0; white-space: normal; }}
      .landscape-metrics > td[data-label] {{ text-align: left; }}
      .landscape-metrics > td[data-label]::before {{ content: attr(data-label); display: block; font-size: 12px; color: #657184; margin-bottom: 4px; }}
      .landscape-detail-row, .landscape-detail-row > td {{ display: block; min-width: 0; }}
    }}
    @media (max-width: 480px) {{
      .landscape-product-link {{ grid-template-columns: 96px minmax(0, 1fr); gap: 10px; }}
      .landscape-thumb, .landscape-thumb img, .landscape-image-empty {{ width: 96px; height: 96px; }}
      .landscape-packs th, .landscape-packs td {{ padding: 8px 5px; font-size: 12px; }}
    }}
    .gallery {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; }}
    .gallery-toolbar {{ display: grid; grid-template-columns: minmax(180px, 0.7fr) minmax(180px, 0.8fr) minmax(220px, 1fr); gap: 10px; align-items: center; margin-bottom: 14px; }}
    .gallery-toolbar select, .gallery-toolbar input {{ width: 100%; min-width: 0; box-sizing: border-box; border: 1px solid #cbd3df; border-radius: 7px; padding: 10px 11px; background: #fff; color: #172033; font: inherit; }}
    .gallery-count {{ grid-column: 1 / -1; color: #657184; font-variant-numeric: tabular-nums; }}
    .gallery-empty {{ padding: 28px 12px; border: 1px dashed #cbd3df; border-radius: 8px; text-align: center; color: #657184; background: #f9fafb; }}
    [hidden] {{ display: none !important; }}
    .tile {{ border: 1px solid #d9dee8; border-radius: 8px; background: #fff; overflow: hidden; }}
    .tile img, .image-empty {{ display: block; width: 100%; aspect-ratio: 1 / 1; object-fit: contain; background: #f7f8fa; border-bottom: 1px solid #d9dee8; }}
    .image-empty {{ display: grid; place-items: center; color: #657184; }}
    .tile a, .tile p {{ display: block; padding: 8px; }}
    .tile a {{ color: #172033; font-weight: 650; }}
    .tile b, .tile span {{ display: block; }}
    .tile span {{ color: #657184; font-size: 12px; margin-top: 3px; }}
    .contact {{ width: 100%; max-height: 760px; object-fit: contain; border: 1px solid #d9dee8; border-radius: 8px; background: #fff; }}
    .status {{ font-weight: 750; color: #147d54; }}
    .decision {{ display: flex; align-items: baseline; gap: 12px; border-left: 4px solid #d99a18; }}
    .decision strong {{ font-size: 17px; }}
    .decision span {{ color: #657184; }}
    .decision.rejected {{ border-left-color: #c83d3d; background: #fff8f7; }}
    .decision.rejected strong {{ color: #a62828; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }}
    .summary-grid article {{ border-left: 3px solid #d9dee8; padding-left: 14px; }}
    .summary-grid h3 {{ margin: 0 0 8px; font-size: 15px; }}
    .summary-grid ul {{ margin: 0; padding-left: 18px; }}
    .summary-grid li {{ margin: 0 0 7px; color: #405066; }}
    .verdict-panel {{ display: flex; justify-content: space-between; gap: 24px; border-left: 4px solid #d99a18; }}
    .eyebrow {{ color: #657184; font-size: 12px; font-weight: 750; }}
    .score-box {{ min-width: 120px; text-align: right; }}
    .score-box span, .score-box small {{ display: block; color: #657184; }}
    .score-box strong {{ display: block; font-size: 40px; line-height: 1.1; }}
    .analysis-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0; padding: 0; overflow: hidden; }}
    .analysis-grid article {{ padding: 18px; border-right: 1px solid #d9dee8; border-bottom: 1px solid #d9dee8; }}
    .analysis-grid article:nth-child(2n) {{ border-right: 0; }}
    .analysis-grid ul, .action-list {{ margin: 8px 0 0; padding-left: 20px; }}
    .analysis-grid li, .action-list li {{ margin: 6px 0; }}
    .warning {{ padding: 10px 12px; border-left: 3px solid #d99a18; background: #fff8e8; color: #6d4a06; }}
    .reference-links {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .required-checks {{ overflow-wrap: anywhere; }}
    .required-check {{ padding: 16px 0; border-top: 1px solid #d9dee8; }}
    .required-check h3 {{ font-size: 17px; margin: 0 0 10px; }}
    .required-check p, .required-check li {{ color: #405066; }}
    .check-status {{ font-size: 13px; font-weight: 500; color: #936000; margin-left: 8px; }}
    .check-documents {{ min-width: 0; table-layout: fixed; }}
    .season-indices {{ display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 6px; margin: 18px 0 8px; }}
    .season-indices > div {{ min-width: 0; text-align: center; font-variant-numeric: tabular-nums; font-size: 12px; }}
    .season-bar {{ height: 95px; display: flex; align-items: end; justify-content: center; margin: 6px 0; }}
    .season-bar i {{ display: block; width: 70%; background: #33847a; border-radius: 3px 3px 0 0; }}
    .business-heading {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }}
    .business-heading p {{ margin: 0; }}
    .calculator {{ margin-top: 20px; padding-top: 18px; border-top: 1px solid #d9dee8; }}
    .calculator-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }}
    .calculator-head h3 {{ margin: 0 0 4px; font-size: 17px; }}
    .calculator-head p {{ margin: 0; color: #657184; }}
    .calc-button {{ flex: 0 0 auto; border: 1px solid #b8c1cf; border-radius: 7px; padding: 8px 11px; background: #fff; color: #24334a; font: inherit; font-weight: 700; cursor: pointer; }}
    .calc-button:hover {{ background: #f2f5f9; }}
    .profit-inputs {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .profit-field {{ min-width: 0; }}
    .profit-field > span {{ display: block; margin-bottom: 5px; color: #405066; font-size: 12px; font-weight: 750; }}
    .profit-field small {{ display: block; min-height: 36px; margin-top: 4px; color: #657184; font-size: 11px; line-height: 1.35; }}
    .profit-control {{ display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; border: 1px solid #cbd3df; border-radius: 7px; background: #fff; overflow: hidden; }}
    .profit-control:focus-within {{ border-color: #2364d2; box-shadow: 0 0 0 2px rgba(35, 100, 210, 0.12); }}
    .profit-control > span {{ padding: 0 9px; color: #657184; font-size: 12px; }}
    .profit-control input {{ width: 100%; min-width: 0; box-sizing: border-box; border: 0; outline: 0; padding: 10px 0; background: transparent; color: #172033; font: inherit; font-variant-numeric: tabular-nums; }}
    .profit-results {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }}
    .profit-result {{ min-width: 0; border-top: 3px solid #d9dee8; padding: 10px 2px 0; }}
    .profit-result span {{ display: block; color: #657184; font-size: 12px; }}
    .profit-result strong {{ display: block; margin-top: 3px; font-size: 22px; font-variant-numeric: tabular-nums; }}
    .profit-result.primary {{ border-top-color: #2364d2; }}
    .profit-result.partial strong {{ color: #9a5b00; }}
    .profit-result.positive strong {{ color: #147d54; }}
    .profit-result.negative strong {{ color: #a62828; }}
    .calc-status {{ margin: 12px 0 0; padding: 9px 11px; border-left: 3px solid #d99a18; background: #fff8e8; color: #6d4a06; }}
    .calc-status.complete {{ border-left-color: #147d54; background: #edf8f3; color: #0e6342; }}
    .cost-table {{ margin-top: 14px; }}
    .cost-table table {{ min-width: 680px; }}
    .cost-table tfoot td {{ background: #f2f5f9; font-weight: 750; }}
    @media (max-width: 900px) {{
      .top {{ display: block; }}
      .kpis, .gallery {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .analysis-grid, .summary-grid {{ grid-template-columns: minmax(0, 1fr); }}
      .analysis-grid article {{ border-right: 0; }}
      .verdict-panel {{ display: block; }}
      .decision {{ display: block; }}
      .decision span {{ display: block; margin-top: 6px; }}
      .score-box {{ text-align: left; margin-top: 12px; }}
      .gallery-toolbar {{ grid-template-columns: minmax(0, 1fr); }}
      .profit-inputs, .profit-results {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      .season-indices {{ grid-template-columns: repeat(6, minmax(0, 1fr)); }}
      .kpis, .gallery, .profit-inputs, .profit-results {{ grid-template-columns: minmax(0, 1fr); }}
      .business-heading, .calculator-head {{ display: block; }}
      .calc-button {{ margin-top: 10px; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="top">
      <div>
        <h1>{escape(title)}</h1>
        <div class="subtle">{escape(asin)} · 最近研究：{escape(generated or "-")}</div>
      </div>
      <div class="actions">
        <a class="pill" href="../index.html#research-archive">返回工作台</a>
        {listing_action}
        <a class="pill" href="#supplier-screening">1688 供应商初筛</a>
        <a class="pill" href="{escape(report_link)}" target="_blank" rel="noreferrer">打开 Markdown 报告</a>
      </div>
    </div>

    {decision_html}
    {profitability_notice}
    {render_required_checks(required)}

    {f'''<section>
      <h2>研究结论与行动</h2>
      <div class="summary-grid">
        <article><h3>成立信号</h3><ul>{render_summary_list(summary.get("signals", []))}</ul></article>
        <article><h3>核心风险</h3><ul>{render_summary_list(summary.get("risks", []))}</ul></article>
        <article><h3>切入方案</h3><ul>{render_summary_list(summary.get("entry_strategy", []))}</ul></article>
      </div>
    </section>''' if summary else ''}

    {render_deep_analysis(deep)}

    <section>
      <div class="kpis">
        <div class="kpi"><span>产品池</span><strong>{len(products)}</strong></div>
        <div class="kpi"><span>产品形态</span><strong>{len(forms)}</strong></div>
        <div class="kpi"><span>已读评论</span><strong>{len(reviews)}</strong></div>
        <div class="kpi"><span>低星 / 高星</span><strong>{low_reviews} / {high_reviews}</strong></div>
      </div>
    </section>

    {render_landscape(landscape, gallery_rows)}

    {render_dimensions(dimensions, gallery_rows, render_form_example)}

    {render_bundles(bundles)}

    {render_new_entrant_analysis(entrants, gallery_rows)}

    <section>
      <h2>已研究样本统计（原研究快照）</h2>
      <div class="kpis">
        <div class="kpi"><span>Top10 销量占比</span><strong>{top10_share:.1f}%</strong></div>
        <div class="kpi"><span>价格中间 50%</span><strong>{fmt_money(market.get('price_p25'))}-{fmt_money(market.get('price_p75'))}</strong></div>
        <div class="kpi"><span>近一年新品占比</span><strong>{new_share:.1f}%</strong></div>
        <div class="kpi"><span>评论/销量相关</span><strong>{to_float(market.get('review_sales_spearman')):.2f}</strong></div>
      </div>
      <div class="table-wrap" style="margin-top:12px">
        <table>
          <thead><tr><th>价格带</th><th class="num">Listing</th><th class="num">月销</th><th class="num">销量占比</th><th class="num">评论中位</th></tr></thead>
          <tbody>{render_price_bands(price_bands)}</tbody>
        </table>
      </div>
    </section>

    <section id="snapshot-forms">
      <h2>已研究样本形态（原研究快照）</h2>
      <div class="table-wrap">
        <table class="snapshot-forms">
          <thead><tr><th>形态</th><th class="num">数量</th><th class="num">直接/关键词</th><th class="num">均价</th><th class="num">形态月销</th><th class="num">销量份额</th><th class="num">评论中位</th><th>材质 / 套装</th></tr></thead>
          <tbody>{render_forms(forms, snapshot_gallery_rows)}</tbody>
        </table>
      </div>
    </section>

    {render_review_evidence(demand, read_json(research_dir / "review_translations.json"), review_insights)}

    {supplier_screening_html}
    <section>
      <div class="business-heading">
        <div>
          <h2>商业可行性</h2>
          <p class="status">{escape(business_status_text)} · {escape(business_source_text)}</p>
        </div>
      </div>
      <div class="kpis">
        <div class="kpi"><span>1688 同款采购中位数</span><strong>{fmt_cny(business.get('procurement_cost_cny'))}</strong></div>
        <div class="kpi"><span>有效 SKU 报价</span><strong>{int(to_float(business.get('quote_sample_count')))} / 至少 {fmt_int(business.get('minimum_quote_samples'))}</strong></div>
        <div class="kpi"><span>待核规格候选</span><strong>{int(to_float(business.get('candidate_quote_count')))}</strong></div>
        <div class="kpi"><span>同款报价区间</span><strong>{quote_range_text}</strong></div>
      </div>
      <p class="subtle">采购口径：{escape(str(business.get('procurement_basis') or '1688 同款去重报价中位数'))}；汇率假设 1 USD = {escape(str(business.get('cny_per_usd') or '-'))} CNY。缺少证据：{escape(', '.join(business.get('missing_evidence', [])) or '无')}。</p>
      {quote_links_html}
      {supplier_evidence_html}

      <div class="calculator" id="profitCalculator">
        <div class="calculator-head">
          <div>
            <h3>单件利润计算器</h3>
            <p>有可靠数据的项目已带入，无法取得的项目保持空白。输入会按 {escape(asin)} 保存在当前浏览器。</p>
          </div>
          <button class="calc-button" id="profitReset" type="button">恢复系统值</button>
        </div>

        <div class="profit-inputs">
          <label class="profit-field" for="profitSellingPrice"><span>Amazon 售价</span><div class="profit-control"><span>$</span><input id="profitSellingPrice" data-profit-field="selling_price_usd" type="number" min="0" step="0.01" value="{input_number(profit_defaults.get('selling_price_usd'))}"><span>USD</span></div><small>Amazon 当前售价，可手动改为目标售价。</small></label>
          <label class="profit-field" for="profitProcurement"><span>1688 采购成本 / 可售单元</span><div class="profit-control"><span>¥</span><input id="profitProcurement" data-profit-field="procurement_cost_cny" type="number" min="0" step="0.01" value="{input_number(profit_defaults.get('procurement_cost_cny'))}"><span>CNY</span></div><small>{'1688 同款同规格报价中位数。' if profit_defaults.get('procurement_cost_cny') is not None else '暂无有效同规格中位价，请手动填写。'}</small></label>
          <label class="profit-field" for="profitFx"><span>汇率</span><div class="profit-control"><span>1 USD =</span><input id="profitFx" data-profit-field="cny_per_usd" type="number" min="0.01" step="0.01" value="{input_number(profit_defaults.get('cny_per_usd'))}"><span>CNY</span></div><small>系统规划汇率，可改成实际结汇汇率。</small></label>
          <label class="profit-field" for="profitPackaging"><span>包装费 / 件</span><div class="profit-control"><span>$</span><input id="profitPackaging" data-profit-field="packaging_cost_usd" type="number" min="0" step="0.01" value="{input_number(profit_defaults.get('packaging_cost_usd'))}"><span>USD</span></div><small>{'已带入供应商确认值。' if profit_defaults.get('packaging_cost_usd') is not None else '暂无实价，请手动填写；无则填 0。'}</small></label>
          <label class="profit-field" for="profitFreight"><span>头程物流 / 件</span><div class="profit-control"><span>$</span><input id="profitFreight" data-profit-field="freight_cost_usd" type="number" min="0" step="0.01" value="{input_number(profit_defaults.get('freight_cost_usd'))}"><span>USD</span></div><small>{'已带入供应商确认值。' if profit_defaults.get('freight_cost_usd') is not None else '暂无实际头程价，请手动填写。'}</small></label>
          <label class="profit-field" for="profitDuty"><span>关税 / 件</span><div class="profit-control"><span>$</span><input id="profitDuty" data-profit-field="duty_cost_usd" type="number" min="0" step="0.01" value="{input_number(profit_defaults.get('duty_cost_usd'))}"><span>USD</span></div><small>暂无可靠税费时留空；确认无关税可填 0。</small></label>
          <label class="profit-field" for="profitFba"><span>FBA 履约费 / 件</span><div class="profit-control"><span>$</span><input id="profitFba" data-profit-field="fba_fee_usd" type="number" min="0" step="0.01" value="{input_number(profit_defaults.get('fba_fee_usd'))}"><span>USD</span></div><small>{'已带入产品详情实际费用。' if profit_defaults.get('fba_fee_usd') is not None else '暂无实际 FBA 费用，请手动填写。'}</small></label>
          <label class="profit-field" for="profitOther"><span>贴标 / 质检 / 其他</span><div class="profit-control"><span>$</span><input id="profitOther" data-profit-field="other_cost_usd" type="number" min="0" step="0.01" value="{input_number(profit_defaults.get('other_cost_usd'))}"><span>USD</span></div><small>选填，留空按 0 计算。</small></label>
          <label class="profit-field" for="profitReferral"><span>Amazon 佣金率</span><div class="profit-control"><span></span><input id="profitReferral" data-profit-field="referral_rate_pct" type="number" min="0" max="100" step="0.1" value="{input_number(profit_defaults.get('referral_rate_pct'))}"><span>%</span></div><small>类目佣金规则默认值，需按实际类目确认。</small></label>
          <label class="profit-field" for="profitAds"><span>广告费率</span><div class="profit-control"><span></span><input id="profitAds" data-profit-field="ad_rate_pct" type="number" min="0" max="100" step="0.1" value="{input_number(profit_defaults.get('ad_rate_pct'))}"><span>%</span></div><small>规划值，可改为目标 TACoS / 销售额广告占比。</small></label>
          <label class="profit-field" for="profitReturns"><span>退货损耗率</span><div class="profit-control"><span></span><input id="profitReturns" data-profit-field="return_loss_rate_pct" type="number" min="0" max="100" step="0.1" value="{input_number(profit_defaults.get('return_loss_rate_pct'))}"><span>%</span></div><small>退货率 × 平均损失比例，默认值可修改。</small></label>
          <label class="profit-field" for="profitCoupon"><span>优惠 / 折扣率</span><div class="profit-control"><span></span><input id="profitCoupon" data-profit-field="coupon_rate_pct" type="number" min="0" max="100" step="0.1" value="{input_number(profit_defaults.get('coupon_rate_pct'))}"><span>%</span></div><small>无优惠计划可填 0。</small></label>
          <label class="profit-field" for="profitStorage"><span>仓储及其他比率</span><div class="profit-control"><span></span><input id="profitStorage" data-profit-field="storage_rate_pct" type="number" min="0" max="100" step="0.1" value="{input_number(profit_defaults.get('storage_rate_pct'))}"><span>%</span></div><small>用于仓储、销毁、移除等销售额占比。</small></label>
        </div>

        <p class="calc-status" id="profitStatus" aria-live="polite"></p>
        <div class="profit-results">
          <div class="profit-result primary" id="profitResultProfit"><span>单件贡献利润</span><strong id="profitContribution">-</strong></div>
          <div class="profit-result primary" id="profitResultMargin"><span>当前已填项利润率</span><strong id="profitMargin">-</strong></div>
          <div class="profit-result"><span>盈亏平衡 ACoS</span><strong id="profitBreakEvenAcos">-</strong></div>
          <div class="profit-result"><span>按当前月销估算</span><strong id="profitMonthly">-</strong></div>
        </div>

        <div class="table-wrap cost-table">
          <table>
            <thead><tr><th>项目</th><th>计算口径</th><th class="num">单件金额</th><th class="num">占售价</th></tr></thead>
            <tbody>
              <tr><td>销售收入</td><td>Amazon 售价</td><td class="num" id="profitLineRevenue">-</td><td class="num">100%</td></tr>
              <tr><td>1688 采购</td><td>人民币成本 ÷ 汇率</td><td class="num" id="profitLineProcurement">-</td><td class="num" id="profitShareProcurement">-</td></tr>
              <tr><td>包装</td><td>手动值 / 供应商实价</td><td class="num" id="profitLinePackaging">-</td><td class="num" id="profitSharePackaging">-</td></tr>
              <tr><td>头程物流</td><td>手动值 / 物流报价</td><td class="num" id="profitLineFreight">-</td><td class="num" id="profitShareFreight">-</td></tr>
              <tr><td>关税</td><td>目标税率折算到单件</td><td class="num" id="profitLineDuty">-</td><td class="num" id="profitShareDuty">-</td></tr>
              <tr><td>FBA 履约费</td><td>Amazon 费用</td><td class="num" id="profitLineFba">-</td><td class="num" id="profitShareFba">-</td></tr>
              <tr><td>Amazon 佣金</td><td id="profitBasisReferral">-</td><td class="num" id="profitLineReferral">-</td><td class="num" id="profitShareReferral">-</td></tr>
              <tr><td>广告</td><td id="profitBasisAds">-</td><td class="num" id="profitLineAds">-</td><td class="num" id="profitShareAds">-</td></tr>
              <tr><td>退货损耗</td><td id="profitBasisReturns">-</td><td class="num" id="profitLineReturns">-</td><td class="num" id="profitShareReturns">-</td></tr>
              <tr><td>优惠 / 折扣</td><td id="profitBasisCoupon">-</td><td class="num" id="profitLineCoupon">-</td><td class="num" id="profitShareCoupon">-</td></tr>
              <tr><td>仓储及其他</td><td id="profitBasisStorage">-</td><td class="num" id="profitLineStorage">-</td><td class="num" id="profitShareStorage">-</td></tr>
              <tr><td>贴标 / 质检 / 其他</td><td>选填固定费用</td><td class="num" id="profitLineOther">-</td><td class="num" id="profitShareOther">-</td></tr>
            </tbody>
            <tfoot><tr><td colspan="2">总费用</td><td class="num" id="profitTotalCost">-</td><td class="num" id="profitTotalShare">-</td></tr></tfoot>
          </table>
        </div>
      </div>
      <p class="warning">计算器始终按当前已填项动态估算；空白费用暂不计入，但不代表该费用已确认为 0。正式下单前仍需补齐并核对报价、包装、头程、税费和 Amazon 费用。</p>
    </section>

    <section>
      <h2>评论覆盖</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ASIN</th><th>形态</th><th class="num">月销</th><th class="num">Listing 评论</th><th class="num">已读评论</th></tr></thead>
          <tbody>{render_review_targets(targets)}</tbody>
        </table>
      </div>
    </section>

    <section id="product-gallery">
      <h2>产品图片</h2>
      <div class="gallery-toolbar">
        <select id="galleryScope" aria-label="图片样本范围">
          <option value="snapshot">原研究快照 · {len(snapshot_gallery_rows)} 个样本</option>
          <option value="current">当前同用途 · {len(gallery_rows)} 个样本</option>
        </select>
        <select id="galleryForm" aria-label="按产品形态筛选">
          <option value="">全部产品形态</option>
          {render_gallery_form_options(snapshot_gallery_rows)}
        </select>
        <input id="gallerySearch" type="search" placeholder="搜索 ASIN / 标题 / 材质" aria-label="搜索代表产品">
        <span class="gallery-count" id="galleryCount"></span>
      </div>
      <div id="productGallery">{render_gallery_scopes(snapshot_gallery_rows, gallery_rows)}</div>
      <div class="gallery-empty" id="galleryEmpty" hidden>没有匹配的产品。</div>
    </section>

    {contact_html}
  </main>
  <script>
    (() => {{
      const formSelect = document.getElementById("galleryForm");
      const scopeSelect = document.getElementById("galleryScope");
      const searchInput = document.getElementById("gallerySearch");
      const count = document.getElementById("galleryCount");
      const empty = document.getElementById("galleryEmpty");
      const tiles = Array.from(document.querySelectorAll("[data-gallery-item]"));
      const profitDefaults = {profit_defaults_json};
      const profitStorageKey = "amz-profit-calculator:{escape(asin)}";
      const profitInputs = Array.from(document.querySelectorAll("[data-profit-field]"));
      const requiredProfitFields = [
        ["selling_price_usd", "Amazon 售价"],
        ["procurement_cost_cny", "1688 采购成本"],
        ["cny_per_usd", "汇率"],
        ["packaging_cost_usd", "包装费"],
        ["freight_cost_usd", "头程物流"],
        ["duty_cost_usd", "关税"],
        ["fba_fee_usd", "FBA 履约费"],
        ["referral_rate_pct", "Amazon 佣金率"],
        ["ad_rate_pct", "广告费率"],
        ["return_loss_rate_pct", "退货损耗率"],
        ["coupon_rate_pct", "优惠折扣率"],
        ["storage_rate_pct", "仓储及其他比率"]
      ];
      const lineNodes = {{
        procurement: ["profitLineProcurement", "profitShareProcurement"],
        packaging: ["profitLinePackaging", "profitSharePackaging"],
        freight: ["profitLineFreight", "profitShareFreight"],
        duty: ["profitLineDuty", "profitShareDuty"],
        fba: ["profitLineFba", "profitShareFba"],
        referral: ["profitLineReferral", "profitShareReferral"],
        ads: ["profitLineAds", "profitShareAds"],
        returns: ["profitLineReturns", "profitShareReturns"],
        coupon: ["profitLineCoupon", "profitShareCoupon"],
        storage: ["profitLineStorage", "profitShareStorage"],
        other: ["profitLineOther", "profitShareOther"]
      }};

      const money = value => Number.isFinite(value) ? `$${{value.toFixed(2)}}` : "-";
      const percent = value => Number.isFinite(value) ? `${{value.toFixed(1)}}%` : "-";

      function profitValuesFromInputs() {{
        return Object.fromEntries(profitInputs.map(input => {{
          const text = input.value.trim();
          return [input.dataset.profitField, text === "" ? null : Number(text)];
        }}));
      }}

      function setProfitInputs(values) {{
        profitInputs.forEach(input => {{
          const value = values[input.dataset.profitField];
          input.value = value === null || value === undefined ? "" : String(value);
        }});
      }}

      function loadProfitValues() {{
        try {{
          const saved = JSON.parse(localStorage.getItem(profitStorageKey) || "null");
          return saved && typeof saved === "object" ? {{ ...profitDefaults, ...saved }} : {{ ...profitDefaults }};
        }} catch (error) {{
          return {{ ...profitDefaults }};
        }}
      }}

      function saveProfitValues(values) {{
        try {{
          localStorage.setItem(profitStorageKey, JSON.stringify(values));
          return true;
        }} catch (error) {{
          return false;
        }}
      }}

      function setLine(name, value, sellingPrice) {{
        const ids = lineNodes[name];
        document.getElementById(ids[0]).textContent = money(value);
        document.getElementById(ids[1]).textContent = Number.isFinite(value) && sellingPrice > 0
          ? percent(value / sellingPrice * 100)
          : "-";
      }}

      function setProfitResult(id, value) {{
        document.getElementById(id).textContent = value;
      }}

      function renderProfitCalculator() {{
        const values = profitValuesFromInputs();
        const saved = saveProfitValues(values);
        const sellingPrice = values.selling_price_usd;
        const fx = values.cny_per_usd;
        const procurement = Number.isFinite(values.procurement_cost_cny) && Number.isFinite(fx) && fx > 0
          ? values.procurement_cost_cny / fx
          : null;
        const rateCost = rate => Number.isFinite(sellingPrice) && sellingPrice > 0 && Number.isFinite(rate)
          ? sellingPrice * rate / 100
          : null;
        const costs = {{
          procurement,
          packaging: values.packaging_cost_usd,
          freight: values.freight_cost_usd,
          duty: values.duty_cost_usd,
          fba: values.fba_fee_usd,
          referral: rateCost(values.referral_rate_pct),
          ads: rateCost(values.ad_rate_pct),
          returns: rateCost(values.return_loss_rate_pct),
          coupon: rateCost(values.coupon_rate_pct),
          storage: rateCost(values.storage_rate_pct),
          other: Number.isFinite(values.other_cost_usd) ? values.other_cost_usd : 0
        }};

        document.getElementById("profitLineRevenue").textContent = money(sellingPrice);
        Object.entries(costs).forEach(([name, value]) => setLine(name, value, sellingPrice));
        document.getElementById("profitBasisReferral").textContent = Number.isFinite(values.referral_rate_pct) ? `售价 × ${{values.referral_rate_pct}}%` : "-";
        document.getElementById("profitBasisAds").textContent = Number.isFinite(values.ad_rate_pct) ? `售价 × ${{values.ad_rate_pct}}%` : "-";
        document.getElementById("profitBasisReturns").textContent = Number.isFinite(values.return_loss_rate_pct) ? `售价 × ${{values.return_loss_rate_pct}}%` : "-";
        document.getElementById("profitBasisCoupon").textContent = Number.isFinite(values.coupon_rate_pct) ? `售价 × ${{values.coupon_rate_pct}}%` : "-";
        document.getElementById("profitBasisStorage").textContent = Number.isFinite(values.storage_rate_pct) ? `售价 × ${{values.storage_rate_pct}}%` : "-";

        const invalid = profitInputs
          .filter(input => input.value.trim() !== "" && (!Number.isFinite(Number(input.value)) || Number(input.value) < 0))
          .map(input => input.closest("label").querySelector(":scope > span").textContent);
        const percentFields = ["referral_rate_pct", "ad_rate_pct", "return_loss_rate_pct", "coupon_rate_pct", "storage_rate_pct"];
        if (percentFields.some(field => Number.isFinite(values[field]) && values[field] > 100)) invalid.push("比率不能超过 100%");
        if (Number.isFinite(sellingPrice) && sellingPrice <= 0) invalid.push("Amazon 售价必须大于 0");
        if (Number.isFinite(values.procurement_cost_cny) && values.procurement_cost_cny <= 0) invalid.push("1688 采购成本必须大于 0");
        if (Number.isFinite(fx) && fx <= 0) invalid.push("汇率必须大于 0");

        const missing = requiredProfitFields
          .filter(([field]) => values[field] === null || values[field] === undefined || !Number.isFinite(values[field]))
          .map(([, label]) => label);
        const status = document.getElementById("profitStatus");
        const resultProfit = document.getElementById("profitResultProfit");
        const resultMargin = document.getElementById("profitResultMargin");
        resultProfit.classList.remove("partial", "positive", "negative");
        resultMargin.classList.remove("partial", "positive", "negative");

        if (invalid.length || !Number.isFinite(sellingPrice) || sellingPrice <= 0) {{
          setProfitResult("profitContribution", "-");
          setProfitResult("profitMargin", "-");
          setProfitResult("profitBreakEvenAcos", "-");
          setProfitResult("profitMonthly", "-");
          setProfitResult("profitTotalCost", "-");
          setProfitResult("profitTotalShare", "-");
          status.classList.remove("complete");
          status.textContent = invalid.length
            ? `请修正：${{[...new Set(invalid)].join("、")}}。`
            : "请先填写有效的 Amazon 售价。";
          if (!saved) status.textContent += " 当前浏览器禁止本地保存。";
          return;
        }}

        const totalCost = Object.values(costs)
          .filter(value => Number.isFinite(value))
          .reduce((sum, value) => sum + value, 0);
        const contribution = sellingPrice - totalCost;
        const margin = contribution / sellingPrice * 100;
        const adCost = Number.isFinite(costs.ads) ? costs.ads : 0;
        const breakEvenAcos = Math.max(0, (sellingPrice - (totalCost - adCost)) / sellingPrice * 100);
        const monthlyProfit = Number.isFinite(profitDefaults.monthly_sales)
          ? contribution * profitDefaults.monthly_sales
          : null;
        setProfitResult("profitContribution", money(contribution));
        setProfitResult("profitMargin", percent(margin));
        setProfitResult("profitBreakEvenAcos", percent(breakEvenAcos));
        setProfitResult("profitMonthly", Number.isFinite(monthlyProfit) ? money(monthlyProfit) : "-");
        setProfitResult("profitTotalCost", money(totalCost));
        setProfitResult("profitTotalShare", percent(totalCost / sellingPrice * 100));
        const complete = missing.length === 0;
        status.classList.toggle("complete", complete);
        if (complete) {{
          resultProfit.classList.add(contribution >= 0 ? "positive" : "negative");
          resultMargin.classList.add(margin >= 20 ? "positive" : "negative");
          status.textContent = `必填成本已齐全：单件总费用 ${{money(totalCost)}}，贡献利润率 ${{percent(margin)}}。`;
        }} else {{
          resultProfit.classList.add(contribution < 0 ? "negative" : "partial");
          resultMargin.classList.add(margin < 0 ? "negative" : "partial");
          status.textContent = `当前已填项暂估：已计费用 ${{money(totalCost)}}，利润率 ${{percent(margin)}}。尚未计入 ${{missing.length}} 项：${{missing.join("、")}}。`;
        }}
        status.textContent += saved ? " 手动值已保存在当前浏览器。" : " 当前浏览器禁止本地保存。";
      }}

      function filterGallery() {{
        const selectedForm = formSelect.value;
        const query = searchInput.value.trim().toLowerCase();
        let visible = 0;
        let withImage = 0;
        let total = 0;
        tiles.forEach(tile => {{
          const matchesScope = tile.closest("[data-gallery-scope]").dataset.galleryScope === scopeSelect.value;
          if (matchesScope) total += 1;
          const matchesForm = !selectedForm || tile.dataset.form === selectedForm;
          const matchesQuery = !query || tile.dataset.search.includes(query);
          const show = matchesScope && matchesForm && matchesQuery;
          tile.hidden = !show;
          if (show) visible += 1;
          if (show && tile.dataset.hasImage === "true") withImage += 1;
        }});
        count.textContent = `显示 ${{visible}} / ${{total}} 个 · 有图 ${{withImage}} · 缺图 ${{visible - withImage}}`;
        empty.hidden = visible !== 0;
      }}

      function changeGalleryScope() {{
        formSelect.replaceChildren(document.getElementById(`galleryOptions-${{scopeSelect.value}}`).content.cloneNode(true));
        document.querySelectorAll("[data-gallery-scope]").forEach(group => {{
          group.hidden = group.dataset.galleryScope !== scopeSelect.value;
        }});
        filterGallery();
      }}
      scopeSelect.addEventListener("change", changeGalleryScope);
      document.querySelectorAll("[data-snapshot-form]").forEach(link => {{
        link.addEventListener("click", () => {{
          scopeSelect.value = "snapshot";
          searchInput.value = "";
          changeGalleryScope();
          formSelect.value = link.dataset.snapshotForm;
          filterGallery();
        }});
      }});
      formSelect.addEventListener("change", filterGallery);
      searchInput.addEventListener("input", filterGallery);
      document.querySelectorAll(".landscape-thumb img, .snapshot-thumb img").forEach(image => {{
        const fail = () => {{
          const placeholder = document.createElement("span");
          placeholder.className = image.closest(".snapshot-thumb") ? "snapshot-image-empty" : "landscape-image-empty";
          placeholder.textContent = "图片加载失败";
          image.replaceWith(placeholder);
        }};
        image.addEventListener("error", fail, {{ once: true }});
        if (image.complete && !image.naturalWidth) fail();
      }});
      tiles.forEach(tile => {{
        const image = tile.querySelector("img");
        if (!image) return;
        const fail = () => {{
          tile.dataset.hasImage = "false";
          const placeholder = document.createElement("div");
          placeholder.className = "image-empty";
          placeholder.textContent = "图片加载失败";
          image.replaceWith(placeholder);
          filterGallery();
        }};
        image.addEventListener("error", fail, {{ once: true }});
        if (image.complete && !image.naturalWidth) fail();
      }});
      profitInputs.forEach(input => input.addEventListener("input", renderProfitCalculator));
      document.getElementById("profitReset").addEventListener("click", () => {{
        try {{ localStorage.removeItem(profitStorageKey); }} catch (error) {{}}
        setProfitInputs(profitDefaults);
        renderProfitCalculator();
      }});
      setProfitInputs(loadProfitValues());
      renderProfitCalculator();
      filterGallery();
    }})();
  </script>
</body>
</html>
"""
    return "\n".join(line.rstrip() for line in html.splitlines()) + "\n"


def main() -> None:
    rows = read_csv(INDEX_PATH)
    WEB_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    for row in rows:
        asin = row.get("asin", "").strip()
        if not asin:
            continue
        atomic_write_text(WEB_RESEARCH_DIR / f"{asin}.html", build_page(row))
    print(f"Built {len(rows)} product research pages into {WEB_RESEARCH_DIR}")


if __name__ == "__main__":
    main()
