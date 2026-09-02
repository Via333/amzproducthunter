#!/usr/bin/env python3
"""Build standalone HTML pages for archived product research outputs."""

from __future__ import annotations

import csv
import json
import shutil
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parent
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


def render_forms(forms: list[dict[str, str]]) -> str:
    rows = []
    for row in forms:
        rows.append(
            """
            <tr>
              <td><b>{form}</b><span>{note}</span></td>
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


def render_demand(rows: list[dict[str, str]]) -> str:
    return "\n".join(
        """
        <tr>
          <td><b>{form}</b><span>{dtype} · {sentiment}</span></td>
          <td>{theme}</td><td class="num">{count}</td><td>{profiles}</td><td>{scenes}</td>
          <td>{excerpt}<span>同主题涉及 ASIN：{asins}</span></td>
        </tr>
        """.format(
            form=escape(row.get("product_form", "")),
            dtype=escape(row.get("demand_type", "")),
            sentiment=escape(row.get("sentiment", "")),
            theme=escape(row.get("theme", "")),
            count=fmt_int(row.get("mention_count")),
            profiles=escape(row.get("user_profiles", "") or "-"),
            scenes=escape(row.get("use_scenes", "") or "-"),
            excerpt=escape(short(row.get("evidence_excerpt", ""), 120)),
            asins=escape(row.get("example_asins", "")),
        )
        for row in rows[:30]
    )


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
    html = []
    for row in products[:36]:
        image = row.get("web_image_url", "") or asset_path(row.get("image_file", ""))
        form = row.get("visual_product_form") or row.get("product_form", "")
        search_text = " ".join(
            str(row.get(field) or "")
            for field in ("asin", "title", "visual_product_form", "product_form", "visual_material_signal", "material")
        ).lower()
        html.append(
            """
            <article class="tile" data-gallery-item data-form="{form_attr}" data-search="{search_attr}">
              <a href="{url}" target="_blank" rel="noreferrer">
                {image_html}
                <b>{asin}</b>
                <span>{title}</span>
              </a>
              <p>{form} · {material} · {pack} pack</p>
            </article>
            """.format(
                url=escape(row.get("listing_url", "")),
                image_html=f'<img src="{escape(image)}" alt="{escape(row.get("asin", ""))}">' if image else '<div class="image-empty">无图</div>',
                asin=escape(row.get("asin", "")),
                title=escape(short(row.get("title", ""), 72)),
                form=escape(form),
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
    for row in products[:36]:
        form = str(row.get("visual_product_form") or row.get("product_form") or "").strip()
        if form and form not in seen:
            forms.append(form)
            seen.add(form)
    return "".join(f'<option value="{escape(form)}">{escape(form)}</option>' for form in forms)


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


def build_page(index_row: dict[str, str]) -> str:
    asin = index_row.get("asin", "")
    research_dir = ROOT / (index_row.get("research_dir") or f"research/{asin}")
    forms = read_csv(research_dir / "product_forms.csv")
    products = read_csv(research_dir / "top_products.csv")
    reviews = read_csv(research_dir / "reviews.csv")
    targets = read_csv(research_dir / "review_targets.csv")
    demand = read_csv(research_dir / "demand_analysis.csv")
    price_bands = read_csv(research_dir / "price_bands.csv")
    market = read_json(research_dir / "market_structure.json")
    business = read_json(research_dir / "business_feasibility.json")
    summary = read_json(research_dir / "research_summary.json")
    contact_sheet = prepare_web_assets(asin, products, research_dir)
    # research_summary.json is the current generic MCP format. deep_analysis.json
    # may be a legacy, product-specific artifact from an older research engine.
    deep = {} if summary else read_json(research_dir / "deep_analysis.json")
    seed = next((row for row in products if row.get("competitor_type") == "seed"), products[0] if products else {})
    title = index_row.get("title") or seed.get("title") or asin
    listing_url = index_row.get("listing_url") or seed.get("listing_url") or f"https://www.amazon.com/dp/{asin}"
    report_link = "../../" + (index_row.get("report_path") or f"reports/product_opportunity_research_{asin}.md")
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
    contribution_margin = to_float(business.get("contribution_margin")) * 100
    break_even_acos = to_float(business.get("break_even_acos")) * 100
    archive_status = index_row.get("status", "")
    archive_notes = index_row.get("notes", "")
    if archive_status == "rejected":
        decision_html = (
            '<section class="decision rejected"><strong>当前结论：已淘汰</strong>'
            f'<span>{escape(archive_notes or "未通过当前风险规则")}</span></section>'
        )
    elif summary:
        decision_html = (
            '<section class="decision"><strong>当前结论：{decision}（{score}/100）</strong>'
            '<span>{reason}</span></section>'
        ).format(
            decision=escape(str(summary.get("decision", "已研究，待复核"))),
            score=escape(str(summary.get("decision_score", "-"))),
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
    .gallery {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; }}
    .gallery-toolbar {{ display: grid; grid-template-columns: minmax(220px, 0.8fr) minmax(280px, 1.2fr) auto; gap: 10px; align-items: center; margin-bottom: 14px; }}
    .gallery-toolbar select, .gallery-toolbar input {{ width: 100%; min-width: 0; box-sizing: border-box; border: 1px solid #cbd3df; border-radius: 7px; padding: 10px 11px; background: #fff; color: #172033; font: inherit; }}
    .gallery-count {{ color: #657184; white-space: nowrap; font-variant-numeric: tabular-nums; }}
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
    }}
    @media (max-width: 560px) {{ .kpis, .gallery {{ grid-template-columns: minmax(0, 1fr); }} }}
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
        <a class="pill" href="{escape(report_link)}" target="_blank" rel="noreferrer">打开 Markdown 报告</a>
      </div>
    </div>

    {decision_html}

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

    <section>
      <h2>市场结构</h2>
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

    <section>
      <h2>产品形态拆分</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>形态</th><th class="num">数量</th><th class="num">直接/关键词</th><th class="num">均价</th><th class="num">形态月销</th><th class="num">销量份额</th><th class="num">评论中位</th><th>材质 / 套装</th></tr></thead>
          <tbody>{render_forms(forms)}</tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>用户需求与评论证据</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>形态 / 需求类型</th><th>主题</th><th class="num">提及</th><th>用户</th><th>场景</th><th>证据</th></tr></thead>
          <tbody>{render_demand(demand)}</tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>商业可行性</h2>
      <p class="status">{escape(business.get('status', 'needs_supplier_quote'))} · {escape(business.get('data_source', 'system_estimate'))}</p>
      <div class="kpis">
        <div class="kpi"><span>贡献利润/件</span><strong>{fmt_money(business.get('contribution_profit'))}</strong></div>
        <div class="kpi"><span>贡献毛利率</span><strong>{contribution_margin:.1f}%</strong></div>
        <div class="kpi"><span>盈亏平衡 ACoS</span><strong>{break_even_acos:.1f}%</strong></div>
        <div class="kpi"><span>首批资金 / 现金周期</span><strong>{fmt_money(business.get('initial_inventory_cash'))} / {fmt_int(business.get('cash_cycle_days'))}天</strong></div>
      </div>
      <p class="subtle">缺少证据：{escape(', '.join(business.get('missing_evidence', [])) or '无')}。系统估算只能用于初筛，供应商报价确认前不得视为真实利润。</p>
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

    <section>
      <h2>代表产品图片</h2>
      <div class="gallery-toolbar">
        <select id="galleryForm" aria-label="按产品形态筛选">
          <option value="">全部产品形态</option>
          {render_gallery_form_options(products)}
        </select>
        <input id="gallerySearch" type="search" placeholder="搜索 ASIN / 标题 / 材质" aria-label="搜索代表产品">
        <span class="gallery-count" id="galleryCount"></span>
      </div>
      <div class="gallery" id="productGallery">{render_gallery(products)}</div>
      <div class="gallery-empty" id="galleryEmpty" hidden>没有匹配的产品。</div>
    </section>

    {contact_html}
  </main>
  <script>
    (() => {{
      const formSelect = document.getElementById("galleryForm");
      const searchInput = document.getElementById("gallerySearch");
      const count = document.getElementById("galleryCount");
      const empty = document.getElementById("galleryEmpty");
      const tiles = Array.from(document.querySelectorAll("[data-gallery-item]"));

      function filterGallery() {{
        const selectedForm = formSelect.value;
        const query = searchInput.value.trim().toLowerCase();
        let visible = 0;
        tiles.forEach(tile => {{
          const matchesForm = !selectedForm || tile.dataset.form === selectedForm;
          const matchesQuery = !query || tile.dataset.search.includes(query);
          const show = matchesForm && matchesQuery;
          tile.hidden = !show;
          if (show) visible += 1;
        }});
        count.textContent = `显示 ${{visible}} / ${{tiles.length}} 个`;
        empty.hidden = visible !== 0;
      }}

      formSelect.addEventListener("change", filterGallery);
      searchInput.addEventListener("input", filterGallery);
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
        (WEB_RESEARCH_DIR / f"{asin}.html").write_text(build_page(row), encoding="utf-8")
    print(f"Built {len(rows)} product research pages into {WEB_RESEARCH_DIR}")


if __name__ == "__main__":
    main()
