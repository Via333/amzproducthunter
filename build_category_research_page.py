#!/usr/bin/env python3
"""Render one archived category research analysis as a standalone web page."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "archive" / "category_research_index.csv"
INDEX_FIELDS = [
    "node_id",
    "category_name",
    "category_path",
    "researched_at",
    "verdict",
    "primary_opportunity",
    "product_count",
    "raw_review_count",
    "page_path",
    "report_path",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one category research page from its analysis JSON.")
    parser.add_argument("--node-id", required=True)
    return parser.parse_args()


def number(value: object) -> float:
    try:
        return float(str(value or "").replace(",", "") or 0)
    except ValueError:
        return 0.0


def fmt_int(value: object) -> str:
    return f"{number(value):,.0f}"


def fmt_money(value: object) -> str:
    return f"${number(value):,.2f}"


def fmt_pct(value: object) -> str:
    return f"{number(value) * 100:.1f}%"


def list_html(values: list[object]) -> str:
    return "".join(f"<li>{escape(str(value))}</li>" for value in values)


def decision_label(value: str) -> tuple[str, str]:
    if value in {"supplier_and_fto_validation", "continue_independent_product_research"}:
        return "继续验证", "good"
    if value.startswith("watch") or value == "separate_category_research":
        return "观察/另行研究", "watch"
    return "淘汰", "bad"


def render_validation_tracks(rows: list[dict]) -> str:
    rendered = []
    for row in rows:
        state = str(row.get("state") or "pending")
        status_class = "good" if state == "research_done" else "watch"
        links = []
        for link in row.get("links") or []:
            url = str(link.get("url") or "")
            external = ' target="_blank" rel="noreferrer"' if url.startswith("http") else ""
            links.append(
                f'<a class="track-link" href="{escape(url, quote=True)}"{external}>{escape(str(link.get("label") or "查看"))}</a>'
            )
        rendered.append(
            """
            <article class="validation-track">
              <div class="track-head"><h3>{form}</h3><strong class="{status_class}">{status}</strong></div>
              <p class="track-result">{current_result}</p>
              <div class="track-cols"><div><b>已完成的证据</b><ul>{evidence}</ul></div><div><b>尚未完成</b><ul>{pending}</ul></div></div>
              <div class="track-links">{links}</div>
            </article>
            """.format(
                form=escape(str(row.get("form") or "")),
                status_class=status_class,
                status=escape(str(row.get("status") or "")),
                current_result=escape(str(row.get("current_result") or "")),
                evidence=list_html(row.get("evidence") or []),
                pending=list_html(row.get("pending") or []),
                links="".join(links),
            )
        )
    return "".join(rendered)


def render_forms(rows: list[dict]) -> str:
    rendered = []
    for row in rows:
        label, css = decision_label(str(row.get("decision") or ""))
        rendered.append(
            """
            <tr>
              <td><strong>{name}</strong><span>{reason}</span></td>
              <td class="num">{count}</td><td class="num">{sales}</td><td class="num">{share}</td>
              <td class="num">{price}</td><td class="num">{reviews}</td>
              <td class="num">{success}/{new_count}</td><td><b class="{css}">{label}</b></td>
            </tr>
            """.format(
                name=escape(str(row.get("label") or row.get("name") or "")),
                reason=escape(str(row.get("reason") or "")),
                count=fmt_int(row.get("listing_count")),
                sales=fmt_int(row.get("monthly_sales")),
                share=fmt_pct(row.get("sales_share")),
                price=fmt_money(row.get("median_price")),
                reviews=fmt_int(row.get("median_reviews")),
                success=fmt_int(row.get("new_entrant_success_count")),
                new_count=fmt_int(row.get("new_listing_count")),
                css=css,
                label=label,
            )
        )
    return "".join(rendered)


def render_references(rows: list[dict]) -> str:
    return "".join(
        """
        <tr><td><a href="https://www.amazon.com/dp/{asin}" target="_blank" rel="noreferrer">{asin}</a>
        <span>{form} · {role}</span></td><td class="num">{sales}</td><td class="num">{reviews}</td><td class="num">{price}</td></tr>
        """.format(
            asin=escape(str(row.get("asin") or "")),
            form=escape(str(row.get("form") or "")),
            role=escape(str(row.get("role") or "")),
            sales=fmt_int(row.get("monthly_sales")),
            reviews=fmt_int(row.get("reviews")),
            price=fmt_money(row.get("price")),
        )
        for row in rows
    )


def render_keywords(rows: list[dict]) -> str:
    return "".join(
        "<tr><td>{keyword}</td><td class='num'>{volume}</td><td class='num'>{cpc}</td><td class='num'>{sales}</td><td>{peak}</td></tr>".format(
            keyword=escape(str(row.get("keyword") or "")),
            volume=fmt_int(row.get("monthly_search_volume")),
            cpc=fmt_money(row.get("cpc")),
            sales=fmt_int(row.get("first_page_avg_sales")),
            peak=escape(str(row.get("peak") or "")),
        )
        for row in rows
    )


def render_reviews(rows: list[dict]) -> str:
    return "".join(
        """
        <article>
          <h3>{form}</h3>
          <div class="review-cols"><div><b>用户认可</b><ul>{positive}</ul></div><div><b>主要问题</b><ul>{negative}</ul></div></div>
          <p class="cut-in"><b>切入判断：</b>{cut_in}</p>
        </article>
        """.format(
            form=escape(str(row.get("form") or "")),
            positive=list_html(row.get("positive") or []),
            negative=list_html(row.get("negative") or []),
            cut_in=escape(str(row.get("cut_in") or "")),
        )
        for row in rows
    )


def render_gallery_options(rows: list[dict]) -> str:
    forms = sorted({str(row.get("form") or "").strip() for row in rows if row.get("form")})
    return "".join(f'<option value="{escape(form, quote=True)}">{escape(form)}</option>' for form in forms)


def render_gallery(rows: list[dict]) -> str:
    rendered = []
    for row in rows:
        form = str(row.get("form") or "")
        decision = str(row.get("decision") or "")
        css = "good" if "优先" in decision else "bad" if "淘汰" in decision else "watch"
        asin = str(row.get("asin") or "")
        rendered.append(
            """
            <article class="shape-tile" data-shape-item data-form="{form_attr}">
              <a href="https://www.amazon.com/dp/{asin}" target="_blank" rel="noreferrer">
                <img src="{image}" alt="{form} {asin}" loading="lazy">
              </a>
              <div class="shape-copy"><b>{form}</b><span>{caption}</span>
                <div class="shape-meta"><span>{asin} · {sales} 件/月</span><strong class="{css}">{decision}</strong></div>
              </div>
            </article>
            """.format(
                form_attr=escape(form, quote=True),
                form=escape(form),
                asin=escape(asin, quote=True),
                image=escape(str(row.get("image") or ""), quote=True),
                caption=escape(str(row.get("caption") or "")),
                sales=fmt_int(row.get("monthly_sales")),
                decision=escape(decision),
                css=css,
            )
        )
    return "".join(rendered)


def render_trend(rows: list[dict]) -> str:
    maximum = max((number(row.get("value")) for row in rows), default=1)
    return "".join(
        "<div class='bar-col'><div class='bar' style='height:{height:.1f}%'></div><b>{value}</b><span>{period}</span></div>".format(
            height=number(row.get("value")) / maximum * 100,
            value=fmt_int(row.get("value")),
            period=escape(str(row.get("period") or "")),
        )
        for row in rows
    )


def build_html(data: dict) -> str:
    metrics = data.get("category_metrics") or {}
    coverage = data.get("review_coverage") or {}
    node_id = str(data.get("node_id") or "")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(str(data.get('category_name')))} 类目深度研究</title><link rel="icon" href="data:,">
<style>
:root{{--ink:#182236;--muted:#657187;--line:#d8deea;--bg:#f5f7fa;--blue:#245fc5;--green:#147853;--amber:#a76100;--red:#a02c2c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1320px;margin:auto;padding:24px}}a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}h1{{margin:0;font-size:28px}}h2{{margin:0 0 12px;font-size:20px}}h3{{margin:0 0 8px;font-size:16px}}p{{margin:8px 0}}
.top{{display:flex;justify-content:space-between;gap:20px;margin-bottom:18px}}.actions{{display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap}}.pill{{display:inline-flex;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:#fff;font-weight:700}}.subtle,td span{{display:block;color:var(--muted);font-size:12px}}
section{{margin-bottom:18px;padding:18px;border:1px solid var(--line);border-radius:8px;background:#fff}}.verdict{{border-left:4px solid var(--amber)}}.verdict strong{{font-size:22px}}.kpis{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.kpi{{padding:12px;border-left:3px solid #8da5ca;background:#f7f9fc}}.kpi strong{{display:block;font-size:23px}}.kpi span{{color:var(--muted)}}
.validation-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.validation-track{{display:grid;gap:10px;padding:14px;border:1px solid var(--line);border-radius:6px}}.track-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}}.track-head h3{{margin:0}}.track-head strong{{max-width:52%;text-align:right;font-size:12px}}.track-result{{margin:0;padding:10px;border-left:3px solid #8da5ca;background:#f4f7fb;font-weight:700}}.track-cols{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.track-cols b{{font-size:12px;color:#40516a}}.track-cols ul{{font-size:12px}}.track-links{{display:flex;gap:8px;flex-wrap:wrap}}.track-link{{padding:7px 9px;border:1px solid var(--line);border-radius:6px;font-weight:700}}
.warning{{padding:12px;border-left:3px solid var(--amber);background:#fff8e8}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:6px}}table{{width:100%;min-width:880px;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#f0f3f8;color:#40516a;font-size:12px}}.num{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}.good{{color:var(--green)}}.watch{{color:var(--amber)}}.bad{{color:var(--red)}}
.gallery-head{{display:flex;align-items:end;justify-content:space-between;gap:14px;margin-bottom:12px}}.gallery-filter{{display:grid;gap:4px;min-width:240px}}.gallery-filter label{{color:var(--muted);font-size:12px;font-weight:700}}.gallery-filter select{{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink);font:inherit}}.shape-gallery{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.shape-tile{{overflow:hidden;border:1px solid var(--line);border-radius:6px;background:#fff}}.shape-tile a{{display:block;aspect-ratio:1/1;background:#f7f8fa}}.shape-tile img{{display:block;width:100%;height:100%;object-fit:contain}}.shape-copy{{display:grid;gap:5px;padding:10px}}.shape-copy>b{{font-size:15px}}.shape-copy>span{{min-height:38px;color:var(--muted);font-size:12px}}.shape-meta{{display:flex;align-items:flex-end;justify-content:space-between;gap:8px;border-top:1px solid var(--line);padding-top:7px}}.shape-meta span{{font-size:11px}}.shape-meta strong{{white-space:nowrap;font-size:12px}}.gallery-empty{{padding:24px;border:1px dashed var(--line);text-align:center;color:var(--muted)}}
.review-list{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.review-list article{{padding:14px;border:1px solid var(--line);border-radius:6px}}.review-cols{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}ul,ol{{margin:7px 0;padding-left:20px}}li{{margin:5px 0}}.cut-in{{padding:10px;background:#eef5ff}}
.trend{{display:grid;grid-template-columns:repeat(12,minmax(48px,1fr));gap:8px;align-items:end;height:225px;overflow:auto}}.bar-col{{display:grid;grid-template-rows:150px auto auto;align-items:end;text-align:center;min-width:48px}}.bar{{width:70%;margin:auto;background:#2c68cf;border-radius:4px 4px 0 0}}.bar-col b{{font-size:11px}}.bar-col span{{font-size:10px;color:var(--muted)}}
@media(max-width:1000px){{.shape-gallery{{grid-template-columns:repeat(3,minmax(0,1fr))}}}}@media(max-width:850px){{main{{padding:14px}}.top{{display:block}}.actions{{margin-top:12px}}.kpis,.review-list,.review-cols,.shape-gallery,.validation-grid{{grid-template-columns:1fr 1fr}}.track-cols{{grid-template-columns:1fr}}}}@media(max-width:600px){{.kpis,.review-list,.review-cols,.shape-gallery,.validation-grid{{grid-template-columns:1fr}}.gallery-head{{display:block}}.gallery-filter{{min-width:0;margin-top:10px}}.track-head{{display:block}}.track-head strong{{display:block;max-width:none;margin-top:4px;text-align:left}}h1{{font-size:24px}}}}
</style></head><body><main>
<div class="top"><div><h1>{escape(str(data.get('category_name')))} 类目深度研究</h1><div class="subtle">Amazon US · Node {escape(node_id)} · {escape(str(data.get('category_path') or ''))}</div></div><div class="actions"><a class="pill" href="../index.html#category-research-archive">返回工作台</a><a class="pill" href="https://www.amazon.com/gp/bestsellers/hi/{escape(node_id)}" target="_blank" rel="noreferrer">打开类目</a><a class="pill" href="../assets/reports/category_deep_research_{escape(node_id)}.md">Markdown 报告</a></div></div>
<section class="verdict"><span class="subtle">整体判断 · {escape(str(data.get('grade') or ''))}</span><strong>{escape(str(data.get('verdict') or ''))}</strong><p>{escape(str(data.get('summary') or ''))}</p></section>
<section id="validation-tracks"><h2>继续验证进度</h2><p class="subtle">“继续验证”不等于已通过。下面分开显示已有结果、待补证据和查看入口。</p><div class="validation-grid">{render_validation_tracks(data.get('validation_tracks') or [])}</div></section>
<section><h2>类目结构</h2><div class="kpis"><div class="kpi"><span>Top100 月销量</span><strong>{fmt_int(metrics.get('top100_monthly_sales'))}</strong></div><div class="kpi"><span>类目健康度</span><strong>{number(metrics.get('category_health_score')):.1f}</strong></div><div class="kpi"><span>评论中位 / Top10</span><strong>{fmt_int(metrics.get('median_reviews'))} / {fmt_int(metrics.get('top10_median_reviews'))}</strong></div><div class="kpi"><span>价格中位</span><strong>{fmt_money(metrics.get('median_price'))}</strong></div><div class="kpi"><span>Top3 产品 / 品牌</span><strong>{fmt_pct(metrics.get('top3_product_sales_share'))} / {fmt_pct(metrics.get('top3_brand_sales_share'))}</strong></div><div class="kpi"><span>低评销量占比</span><strong>{fmt_pct(metrics.get('low_review_sales_share'))}</strong></div><div class="kpi"><span>CN/HK 卖家</span><strong>{fmt_pct(metrics.get('cn_hk_seller_share'))}</strong></div><div class="kpi"><span>退货率</span><strong>{fmt_pct(metrics.get('return_rate'))}</strong></div></div></section>
<section><h2>数据质量提醒</h2><div class="warning"><ul>{list_html(data.get('data_quality_flags') or [])}</ul></div></section>
<section><div class="gallery-head"><div><h2>产品形态图片总览</h2><p class="subtle">图片对应本次形态判断；同一形态保留多个样例时，可比较结构和价格带。</p></div><div class="gallery-filter"><label for="shapeFilter">按产品形态筛选</label><select id="shapeFilter"><option value="">全部形态</option>{render_gallery_options(data.get('shape_gallery') or [])}</select></div></div><div class="shape-gallery" id="shapeGallery">{render_gallery(data.get('shape_gallery') or [])}</div><div class="gallery-empty" id="shapeEmpty" hidden>没有匹配的形态图片。</div></section>
<section><h2>形态拆分</h2><div class="table-wrap"><table><thead><tr><th>形态 / 判断</th><th class="num">样本</th><th class="num">月销</th><th class="num">份额</th><th class="num">中位价</th><th class="num">评论中位</th><th class="num">新品成功</th><th>结论</th></tr></thead><tbody>{render_forms(data.get('forms') or [])}</tbody></table></div></section>
<section><h2>代表产品</h2><div class="table-wrap"><table><thead><tr><th>ASIN / 角色</th><th class="num">月销</th><th class="num">评论</th><th class="num">售价</th></tr></thead><tbody>{render_references(data.get('representative_asins') or [])}</tbody></table></div></section>
<section id="review-analysis"><h2>评论分析</h2><p class="subtle">共归纳 {fmt_int(coverage.get('raw_review_count'))} 条评论、{fmt_int(coverage.get('asin_count'))} 个 ASIN；不展示评论全文。</p><div class="review-list">{render_reviews(data.get('review_analysis') or [])}</div></section>
<section><h2>类目趋势</h2><div class="trend">{render_trend((data.get('trend') or {{}}).get('sales') or [])}</div><p>{escape(str((data.get('trend') or {{}}).get('note') or ''))}</p></section>
<section><h2>核心关键词</h2><div class="table-wrap"><table><thead><tr><th>关键词</th><th class="num">月搜索</th><th class="num">CPC</th><th class="num">首页均销</th><th>旺季</th></tr></thead><tbody>{render_keywords(data.get('keywords') or [])}</tbody></table></div></section>
<section><h2>机会与风险</h2><div class="review-cols"><div><h3>保留方向</h3><ul>{list_html(data.get('opportunities') or [])}</ul></div><div><h3>主要风险</h3><ul>{list_html(data.get('risks') or [])}</ul></div></div></section>
<section><h2>下一步</h2><ol>{list_html(data.get('next_actions') or [])}</ol><p class="subtle">生成时间：{escape(str(data.get('researched_at') or ''))} · 数据源：{escape(str(data.get('data_provider') or ''))} · MCP 调用 {fmt_int(data.get('mcp_tool_calls'))} 次</p></section>
</main><script>
const shapeFilter = document.getElementById("shapeFilter");
const shapeItems = Array.from(document.querySelectorAll("[data-shape-item]"));
const shapeEmpty = document.getElementById("shapeEmpty");
function filterShapes() {{
  const selected = shapeFilter.value;
  let visible = 0;
  shapeItems.forEach((item) => {{
    const show = !selected || item.dataset.form === selected;
    item.hidden = !show;
    if (show) visible += 1;
  }});
  shapeEmpty.hidden = visible !== 0;
}}
shapeFilter.addEventListener("change", filterShapes);
</script></body></html>"""


def update_index(data: dict, page_path: Path, report_copy: Path) -> None:
    rows = []
    if INDEX_PATH.exists():
        with INDEX_PATH.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    node_id = str(data.get("node_id") or "")
    rows = [row for row in rows if row.get("node_id") != node_id]
    rows.append(
        {
            "node_id": node_id,
            "category_name": data.get("category_name", ""),
            "category_path": data.get("category_path", ""),
            "researched_at": data.get("researched_at", ""),
            "verdict": data.get("verdict", ""),
            "primary_opportunity": data.get("primary_opportunity", ""),
            "product_count": (data.get("category_metrics") or {}).get("category_sample_count", 100),
            "raw_review_count": (data.get("review_coverage") or {}).get("raw_review_count", 0),
            "page_path": str(page_path.relative_to(ROOT)),
            "report_path": str(report_copy.relative_to(ROOT)),
        }
    )
    rows.sort(key=lambda row: str(row.get("researched_at") or ""), reverse=True)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    node_id = args.node_id.strip()
    analysis_path = ROOT / "research" / f"category_{node_id}_analysis.json"
    report_path = ROOT / "reports" / f"category_deep_research_{node_id}.md"
    if not analysis_path.exists() or not report_path.exists():
        raise SystemExit(f"Missing category research inputs for node {node_id}")
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    page_path = ROOT / "web" / "category" / f"{node_id}.html"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(build_html(data), encoding="utf-8")
    report_copy = ROOT / "web" / "assets" / "reports" / report_path.name
    report_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(report_path, report_copy)
    update_index(data, page_path, report_copy)
    print(f"Built {page_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
