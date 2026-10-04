"""Research-only presentation; no selections trigger data collection or purchases."""

import re
from html import escape
from urllib.parse import urlparse

from research_dimensions import UNKNOWN
from research_images import allowed_image_url


def money(value):
    return f'${value:,.2f}' if value is not None else '未取得'


def asin_link(asin):
    if not re.fullmatch('[A-Z0-9]{10}', str(asin)):
        return escape(str(asin))
    return f'<a href="https://www.amazon.com/dp/{asin}" target="_blank" rel="noreferrer">{asin}</a>'


def swatches(colors):
    return ''.join(f'<i class="dimension-swatch" style="background:{color}" aria-hidden="true"></i>'
                   for color in colors if re.fullmatch(r'#[a-fA-F0-9]{6}', color))


def render_dimensions(analysis, gallery, render_example):
    images = {p['asin']: p for p in gallery}
    forms = analysis.get('forms', [])
    selected = next((f['form'] for f in forms if f['is_target']), forms[0]['form'] if forms else '')
    options = ''.join(f'<option value="{escape(f["form"])}"{" selected" if f["form"] == selected else ""}>{escape(f["label"])} · {f["count"]} 个样本</option>' for f in forms)
    blocks = []
    for form in forms:
        panels = []
        for dimension, label, keys in [('color', '颜色', ('color',)), ('size', '尺寸 / 容量', ('capacity', 'size')), ('pack', '数量 / Pack', ('pack',))]:
            tables = []
            for key in keys:
                rows = []
                for group in form['groups'][key]:
                    members = group['members']
                    representative = next((p for p in members if p['asin'] in images), members[0])
                    sample = {**representative, **images.get(representative['asin'], {})}
                    sample['main_image_url'] = sample.get('main_image_url') or representative.get('main_image_url', '')
                    evidence = ''.join(f'<li>{asin_link(p["asin"])} · {escape(p["facts"][key]["source"])}：{escape(p["facts"][key]["raw"] or "未取得")}<small>{escape(p["attribute_date"] or "采集日期未记录")} · {escape(p["attribute_source"])}</small></li>' for p in members)
                    extra = f'<span>按主品折算 {money(group["median_unit_price"])}</span>' if key == 'pack' else ''
                    rows.append(f'''<tr><td data-label="分类"><strong>{swatches(group['swatches'])}{escape(group['label'])}</strong></td>
                      <td data-label="样本 / 占比">{group['count']} / {group['share']:.0%}</td>
                      <td data-label="售价中位">{money(group['median_price'])}{extra}</td>
                      <td data-label="示例商品">{render_example(sample)}</td>
                      <td data-label="分类证据"><details><summary>{len(members)} 个 ASIN · 原始属性</summary><ul>{evidence}</ul></details></td></tr>''')
                heading = {'capacity': '容量', 'size': '产品尺寸 / 规格'}.get(key, label)
                tables.append(f'''<h3>{heading}</h3><div class="table-wrap dimension-table-wrap"><table class="dimension-table">
                  <thead><tr><th>分类</th><th>样本 / 占比</th><th>售价中位</th><th>示例商品</th><th>分类证据</th></tr></thead>
                  <tbody>{''.join(rows)}</tbody></table></div>''')
            panels.append(f'<div data-dimension-panel="{dimension}"{" hidden" if dimension != "color" else ""}>{"".join(tables)}</div>')
        blocks.append(f'<div data-dimension-form="{escape(form["form"])}"{" hidden" if form["form"] != selected else ""}>{"".join(panels)}</div>')
    variants = []
    for v in analysis.get('variants', []):
        signal = f'{v["page_sales_signal"]:,.0f}' if v['page_sales_signal'] is not None else '缺失 / 超期'
        variants.append(f'''<tr><td data-label="子体 ASIN">{asin_link(v['asin'])}</td>
          <td data-label="颜色">{swatches(v['color']['swatches'])}{escape(v['color']['label'])}</td>
          <td data-label="Pack">{escape(v['pack']['label'])}</td><td data-label="尺寸">{escape(v['size'])}</td>
          <td data-label="页面销量信号">{signal}</td><td data-label="原始变体属性">{escape(v['property'])}</td></tr>''')
    variant_html = f'''<div class="table-wrap dimension-table-wrap"><table class="dimension-table variant-table"><thead><tr><th>子体 ASIN</th><th>颜色</th><th>Pack</th><th>尺寸</th><th>页面销量信号</th><th>原始变体属性</th></tr></thead><tbody>{''.join(variants)}</tbody></table></div>''' if variants else '<p class="dimension-note">尚未取得目标子体属性，不能推断颜色或规格销量。</p>'
    coverage = f'已取得 {len(variants)} / {analysis.get("variant_total") or "未知"} 个子体；采集于 {analysis.get("variant_retrieved_on") or "未记录"}。'
    comments = '；'.join(f'{escape(color)} {count} 条' for color, count in analysis['review_colors'].items()) or '暂无带属性的评论'
    empty = '<p class="dimension-note">目标用途尚未确认，暂不混合相邻产品做规格统计。</p>' if not forms else ''
    return f'''<section id="dimension-research" class="research-dimensions" data-dimension-research>
      <h2>颜色、尺寸与 Pack 研究</h2>
      <div class="dimension-toolbar"><label for="dimensionForm">产品形态<select id="dimensionForm">{options}</select></label>
        <div class="dimension-tabs" role="tablist" aria-label="规格研究维度">
          <button type="button" role="tab" id="dimension-tab-color" aria-selected="true" aria-controls="dimension-panel" tabindex="0" data-dimension="color">颜色</button>
          <button type="button" role="tab" id="dimension-tab-size" aria-selected="false" aria-controls="dimension-panel" tabindex="-1" data-dimension="size">尺寸 / 容量</button>
          <button type="button" role="tab" id="dimension-tab-pack" aria-selected="false" aria-controls="dimension-panel" tabindex="-1" data-dimension="pack">数量 / Pack</button>
        </div>
      </div>
      <p class="dimension-note">同形态 ASIN 去重统计，含待核及排除样本，仅作规格背景。售价沿用研究快照；占比是样本占比，不是颜色或规格销量份额；父体、Listing 销量不分摊给子体。Pack 指同类主品装数，组件数另列；未核 SKU 的单件折价仅作线索。</p>
      <div id="dimension-panel" role="tabpanel" aria-labelledby="dimension-tab-color">{empty}{''.join(blocks)}</div>
      <h3>目标 Listing 子体矩阵</h3><p class="dimension-note">{escape(coverage)}销量为近 15 天内最近抓取的页面信号，并非 15 天销量；缺失不等于零，不能与类目月销相加或据此计算购买份额。</p>
      {variant_html}
      <p class="dimension-note">评论颜色样本（去重 {analysis['review_count']} 条）：{comments}。仅使用评论自身的颜色属性；采样不等于消费者偏好。</p>
    </section>'''


def render_bundles(analysis):
    observations = []
    for row in analysis['observations']:
        image_url = row.get('image_url', '')
        image = f'<img src="{escape(image_url)}" alt="{escape(row["label"])}" width="112" height="112" loading="lazy">' if allowed_image_url(image_url) else '<span class="bundle-image-empty">待补图</span>'
        observations.append(f'''<article class="bundle-product"><div class="bundle-product-heading">{image}<div><h3>{escape(row['label'])}</h3><p>{escape(row.get('brand', ''))} · {asin_link(row['asin'])}</p><span class="bundle-evidence-level">平台同购模块线索</span></div></div>
          <p>{escape(row.get('interpretation', ''))}</p><p class="bundle-next">{escape(row.get('bundle_assessment', '待核对具体规格与成本'))}</p></article>''')
    source_url = analysis['source_url']
    parsed = urlparse(source_url)
    safe_source = source_url if parsed.scheme == 'https' and parsed.hostname == 'www.amazon.com' else ''
    source = f'<a href="{escape(safe_source)}" target="_blank" rel="noreferrer">Amazon 商品页同购模块</a>' if safe_source else '暂无已保存的平台同购来源'
    candidates = []
    for c in analysis['candidates']:
        examples = ''.join(f'<li>{asin_link(e["asin"])} · {escape(str(e["date"]))} · {escape(str(e["rating"]))} 星<span>提及：{escape(c["label"])}</span></li>' for e in c['evidence'])
        # These are mentions, including complaints and comparisons, not positive purchase endorsements.
        candidates.append(f'''<tr><td data-label="搭配产品"><strong>{escape(c['label'])}</strong><span>{escape(c['role'])}</span></td>
          <td data-label="评论提及">{c['review_mentions']} / {analysis['review_count']}<span>非同购人数</span></td>
          <td data-label="现有配置">{'目标已附带' if c['included'] else '是否附带需核对 SKU'}</td>
          <td data-label="套装判断">{escape(c['next_check'])}</td>
          <td data-label="来源"><details><summary>评论来源 · {len(c['evidence'])} 条</summary><ul>{examples or '<li>仅商品标题 / 描述提及，暂无评论命中。</li>'}</ul><a href="#review-evidence">查看评论研究</a></details></td></tr>''')
    table = f'''<div class="table-wrap dimension-table-wrap"><table class="dimension-table bundle-table"><thead><tr><th>搭配产品</th><th>评论提及</th><th>现有配置</th><th>套装判断</th><th>来源</th></tr></thead><tbody>{''.join(candidates)}</tbody></table></div>''' if candidates else '<p class="dimension-note">尚无适用于该用途的搭配证据，暂不生成通用赠品建议。</p>'
    return f'''<section id="bundle-research" class="research-dimensions">
      <h2>关联商品与组合套装</h2>
      <p class="bundle-boundary">同购订单数、购买比例、组合转化率：未取得。平台模块出现不代表适合直接打包销售，以下不是“买得最多”排名。</p>
      <h3>平台关联线索</h3><p class="dimension-note">{source} · 核对 {escape(analysis['checked_on'] or '未记录')} · {escape(analysis['source_status'])}</p>
      <div class="bundle-products">{''.join(observations) or '<p class="dimension-note">未取得平台同购模块数据；不等于没有关联购买。</p>'}</div>
      <h3>评论场景与配件线索</h3><p class="dimension-note">按去重评论提及次数排序，包含抱怨、替代方案和已有配件，不等于购买意愿或套装需求。</p>
      {table}
      <p class="bundle-boundary">组合验证：互补用途 → SKU 与配件不重复 → 尺寸 / 接口匹配 → 包装和 FBA 成本 → 食品接触、专利及电器风险 → 小样使用测试。缺少新增成本和转化证据，暂不判定组合利润。</p>
    </section>'''
