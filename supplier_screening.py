"""Evidence-preserving 1688 lead screening, separate from verified procurement costs."""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from product_family import product_identity

SOURCE_FILE = "supplier_screening_sources.json"
CACHE_DAYS = 7
MATCH_LABELS = {"matched": "同用途线索，规格待核", "pending": "产品匹配待核", "excluded": "不纳入初筛"}
CHINESE_TERMS = {
    "cake_guide": ("蛋糕拱形导向工具", r"蛋糕.*(?:拱形|弧形|裙边|褶皱).*(?:导向|辅助|标记|定点)"),
    "rolling_pin": ("擀面杖", r"擀面[杖棍]|面粉棍"),
    "bar_measuring_tool": ("调酒量杯", r"量酒[器杯]|调酒量杯|鸡尾酒量杯|盎司杯"),
    "drain_stopper": ("浴缸止水塞", r"(?:浴缸|水槽|排水).*(?:止水|塞|堵头)"),
}


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def unwrap(value):
    if isinstance(value, dict) and "content" in value:
        texts = [c.get("text", "") for c in value["content"] if c.get("type") == "text"]
        try:
            value = json.loads("\n".join(texts))
        except ValueError:
            return None
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def records(value):
    value = unwrap(value)
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    return [value] if isinstance(value, dict) and value.get("product_id") else []


def number(value, maximum=None, positive=False):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(n) or n < 0 or (positive and n == 0) or (maximum is not None and n > maximum):
        return None
    return n


def valid_date(value):
    try:
        parsed = date.fromisoformat(str(value)[:10])
        return parsed.isoformat() if parsed <= date.today() else ""
    except ValueError:
        return ""


def fresh(value):
    stamp = valid_date(value)
    return bool(stamp and (date.today() - date.fromisoformat(stamp)).days <= CACHE_DAYS)


def shell_record(row):
    # Some endpoints return populated titles with zero-filled unavailable metrics.
    fields = ("price", "service_score", "sales_of_30d", "min_order_quantity", "review_count", "sku_count")
    return not any(number(row.get(k), positive=True) for k in fields) and not row.get("online_date") and not row.get("store_name")


def seed_identity(seed):
    title = str(seed.get("title") or "")
    identity = product_identity(title)
    family = identity["product_family"]
    if re.search(r"jigger|liquor measur|measuring shot", title, re.I):
        family = "bar_measuring_tool"
    return family


def match_title(seed, title):
    family = seed_identity(seed)
    supplied = product_identity(title)["product_family"]
    if not family.startswith(("legacy:", "unresolved:")) and supplied == family:
        return "matched", "标题用途一致；材质、尺寸、件数和目标 SKU 尚未核对。"
    rule = CHINESE_TERMS.get(family)
    if rule and re.search(rule[1], title, re.I):
        return "matched", "标题命中同用途产品名称；不等于同款或同规格报价。"
    if family == "cake_guide" and re.search(r"梳|洗头|刷|支架|打桩|裱花嘴|转换器|发夹|发抓|鲨鱼夹|美甲|烫发|汽车|花洒", title):
        return "excluded", "标题为其他用途工具或配件，不是蛋糕裱花定位工具。"
    if not supplied.startswith(("legacy:", "unresolved:")) and supplied != family:
        return "excluded", "标题指向其他产品用途。"
    return "pending", "图片检索相关性不足以证明同类，需人工核对标题与实物。"


def title_quantity(title):
    match = re.search(r"(?<![\d.])(\d+)\s*(?:pcs?|pieces?|packs?)\b|(\d+)\s*件(?:套|装)", title, re.I)
    return int(next(g for g in match.groups() if g)) if match else None


def source_entries(research_dir):
    base = read_json(research_dir / "research_sources.json")
    entries = [{"tool": "ali1688_product_search_from_image", "retrieved_on": base.get("retrieved_on", ""),
                "result": base.get("supplier_candidates", []), "origin": "research_sources.json"}]
    supplement = read_json(research_dir / SOURCE_FILE)
    entries.extend(supplement.get("calls", []))
    return base, sorted(entries, key=lambda e: str(e.get("retrieved_on", "")))


def build_screening(research_dir):
    research_dir = Path(research_dir)
    base, entries = source_entries(research_dir)
    seed = unwrap(base.get("detail", {})) or {}
    if not isinstance(seed, dict):
        seed = {}
    merged = {}
    raw_count = 0
    for entry in entries:
        if "variations" in entry.get("tool", ""):
            continue
        for row in records(entry.get("result")):
            offer = str(row.get("product_id") or "")
            if not re.fullmatch(r"\d+", offer):
                continue
            raw_count += 1
            target = merged.setdefault(offer, {"product_id": offer, "field_dates": {}, "sources": [], "empty_details": False})
            stamp = valid_date(entry.get("retrieved_on", ""))
            target["sources"].append({"tool": entry.get("tool", ""), "retrieved_on": stamp})
            empty = shell_record(row)
            if empty and "request" in entry.get("tool", ""):
                target["empty_details"] = True
            for key, value in row.items():
                if key in ("field_dates", "sources", "empty_details"):
                    continue
                if empty and key not in ("product_id", "title", "photo", "url"):
                    continue
                if value is not None and value != "" and value != [] and value != {}:
                    target[key] = value
                    target["field_dates"][key] = stamp
    rejected_quotes = {}
    quote_path = research_dir / "supplier_quotes.csv"
    if quote_path.exists():
        with quote_path.open(encoding="utf-8-sig", newline="") as handle:
            for q in csv.DictReader(handle):
                status = q.get("match_status", "")
                if "mismatch" in status or status in {"rejected", "invalid"}:
                    rejected_quotes[q.get("source_product_id", "")] = q.get("match_reason") or status
    candidates = []
    for offer, row in merged.items():
        title = str(row.get("title") or offer)
        match, reason = match_title(seed, title)
        if offer in rejected_quotes:
            match, reason = "excluded", rejected_quotes[offer]
        breakdown = row.get("service_score_detail") or []
        if isinstance(breakdown, str):
            try:
                breakdown = json.loads(breakdown)
            except ValueError:
                breakdown = []
        if not isinstance(breakdown, list):
            breakdown = []
        quality = next((number(s.get("score"), 5, True) for s in breakdown
                        if isinstance(s, dict) and s.get("title") in {"品质体验", "quality experience"}), None)
        tags = row.get("seller_identities") or []
        if not isinstance(tags, list):
            tags = []
        factory = any(str(t).lower().replace("_", " ") in {"super factory", "superfactory", "超级工厂"} for t in tags)
        rating = number(row.get("score"), 5, True) if number(row.get("review_count"), positive=True) else None
        c = {"id": offer, "title": title, "url": f"https://detail.1688.com/offer/{offer}.html",
             "image": str(row.get("photo") or ""), "store": str(row.get("store_name") or ""),
             "origin": str(row.get("shipping_origin") or ""), "pack": title_quantity(title),
             "price": number(row.get("price"), positive=True), "service": number(row.get("service_score"), 5, True),
             "quality": quality, "repurchase": number(row.get("repurchase_rate"), 100), "rating": rating,
             "reviews": number(row.get("review_count"), positive=True), "moq": number(row.get("min_order_quantity"), positive=True),
             "sales": number(row.get("sales_of_30d")), "factory": factory, "tags": [str(t) for t in tags],
             "match": match, "reason": reason, "sources": row["sources"], "field_dates": row["field_dates"],
             "empty_details": row["empty_details"]}
        metric_keys = ("price", "service_score", "repurchase_rate")
        c["stale"] = not all(fresh(row["field_dates"].get(k)) for k in metric_keys)
        low_quality = (quality is not None and quality < 3.5) or (rating is not None and rating < 4)
        if match == "excluded":
            priority, note = 4, reason
        elif match == "pending":
            priority, note = 3, reason
        elif low_quality:
            priority, note = 2, "品质体验低于 3.5/5 或商品评分低于 4/5，先核对差评与质量。"
        elif not c["stale"] and c["price"] and (c["service"] or 0) >= 4 and (c["repurchase"] or 0) >= 30:
            priority, note = 0, "同用途标题、服务评分和回头率有支持，优先询样；仍非已验证供应商。"
        else:
            priority, note = 1, "同用途线索，补齐评分、规格报价和店铺主体后再比较。"
        c.update(priority=priority, priority_note=note)
        candidates.append(c)
    candidates.sort(key=lambda c: (c["priority"], -int(c["factory"]),
                                   -(c["rating"] or 0), -(c["repurchase"] if c["repurchase"] is not None else -1),
                                   -(c["service"] or 0), c["id"]))
    return {"asin": research_dir.name, "raw_count": raw_count, "candidates": candidates,
            "source_count": len(entries), "calls": read_json(research_dir / SOURCE_FILE).get("calls", [])}


def collect_supplier_screening(research_dir, client, max_calls=3):
    """Reuse search leads; at most one keyword and two detail calls per run."""
    research_dir = Path(research_dir)
    path = research_dir / SOURCE_FILE
    saved = read_json(path)
    saved.setdefault("calls", [])
    if fresh(saved.get("last_checked_on")):
        return build_screening(research_dir)
    sources = read_json(research_dir / "research_sources.json")
    seed = unwrap(sources.get("detail", {})) or {}
    rule = CHINESE_TERMS.get(seed_identity(seed))
    used = 0

    def cached(tool, arguments):
        return any(c.get("tool") == tool and c.get("arguments") == arguments and fresh(c.get("retrieved_on")) for c in saved["calls"])

    def call(tool, arguments):
        nonlocal used
        used += 1
        try:
            result = client.call_tool(tool, arguments)
            entry = {"tool": tool, "arguments": arguments, "result": result, "retrieved_on": date.today().isoformat()}
        except Exception:
            entry = {"tool": tool, "arguments": arguments, "result": None, "status": "unavailable", "retrieved_on": date.today().isoformat()}
        saved["calls"].append(entry)
        path.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return entry

    if rule:
        args = {"search_name": rule[0], "page": 1}
        if used < max_calls and not cached("ali1688_similar_product", args):
            call("ali1688_similar_product", args)
    candidates = build_screening(research_dir)["candidates"]
    attempts = 0
    for c in candidates:
        if used >= max_calls or attempts >= 2:
            break
        args = {"product_id": c["id"]}
        if c["match"] != "matched" or cached("ali1688_product_request", args):
            continue
        call("ali1688_product_request", args)
        attempts += 1
    result = build_screening(research_dir)
    saved["last_checked_on"] = date.today().isoformat()
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (research_dir / "supplier_screening.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def fmt(value, suffix="", prefix=""):
    return "未提供" if value is None else f"{prefix}{value:,.2f}".rstrip("0").rstrip(".") + suffix


def render_supplier_screening(screening, asin):
    candidates = screening["candidates"]
    cards = []
    for rank, c in enumerate(candidates):
        esc = lambda v: escape(str(v), quote=True)
        photo = urlparse(c["image"])
        image = f'<img src="{esc(c["image"])}" alt="{esc(c["title"])}" loading="lazy" referrerpolicy="no-referrer">' if photo.scheme == "https" and (photo.hostname or "").endswith(".alicdn.com") else '<span class="supplier-no-image">暂无图片</span>'
        attrs = " ".join(f'data-{k}="{esc(v if v is not None else "")}"' for k, v in {
            "id": c["id"], "rank": rank, "price": c["price"], "repurchase": c["repurchase"],
            "rating": c["rating"], "service": c["service"], "factory": int(c["factory"]),
            "pack": c["pack"], "match": c["match"], "search": c["title"] + " " + c["store"] + " " + c["id"]}.items())
        priority = ["优先询样", "补证比较", "质量指标需核对", "匹配待核", "不纳入初筛"][c["priority"]]
        dates = " · ".join(f'{name} {c["field_dates"].get(field) or "日期未记录"}' for name, field in
                           [("价格", "price"), ("评分", "service_score"), ("回头率", "repurchase_rate")])
        card = f'''<article class="supplier-card" {attrs}>
          <header><span class="supplier-priority p{c['priority']}">{priority}</span>
          <label class="supplier-pick"><input type="checkbox" data-supplier-pick="{c['id']}" aria-label="选择供货链接 {c['id']}">候选</label></header>
          <div class="supplier-product"><a href="{c['url']}" target="_blank" rel="noopener noreferrer" class="supplier-photo">{image}</a>
          <div><a href="{c['url']}" target="_blank" rel="noopener noreferrer">{esc(c['title'])}</a>
          <p>{esc(c['store'] or '店铺名称未提供')} · {esc(c['origin'] or '产地未提供')}</p>
          <p class="supplier-muted">货号 {c['id']} · 标题 {str(c['pack']) + ' 件/组件' if c['pack'] else '数量未注明'}</p></div></div>
          <dl class="supplier-metrics">
          <div><dt>商品展示价</dt><dd>{fmt(c['price'], prefix='¥')}</dd></div>
          <div><dt>回头率</dt><dd>{fmt(c['repurchase'], '%')}</dd></div>
          <div><dt>商品评分</dt><dd>{fmt(c['rating'], '/5')}</dd></div>
          <div><dt>服务评分</dt><dd>{fmt(c['service'], '/5')}</dd></div>
          <div><dt>品质体验</dt><dd>{fmt(c['quality'], '/5')}</dd></div>
          <div><dt>起订量</dt><dd>{fmt(c['moq'])}</dd></div></dl>
          <p>好评率：未提供 · 近 30 天销量：{fmt(c['sales'])}</p>
          <p><strong>{'平台超级工厂标签，自有工厂待核' if c['factory'] else '自有工厂：无已核验证据'}</strong></p>
          <p class="supplier-muted">{esc(MATCH_LABELS[c['match']])} · {esc(c['priority_note'])}</p>
          <details><summary>证据与缺口</summary><p>{esc(c['reason'])}</p>
          <p>店铺身份标签：{esc(', '.join(c['tags']) or '未提供')}；会员或实力商家标签不证明自有工厂。</p>
          <p>采集：{esc(dates)}</p>
          {'<p>部分指标超过 7 天或缺采集日期，需刷新确认。</p>' if c['stale'] else ''}
          {'<p>详情接口返回空指标，已保留搜索来源的可用值；零值占位不作为真实报价或差评。</p>' if c['empty_details'] else ''}
          <p>待核目标 SKU 尺寸、材质、件数、阶梯含税价，以及营业主体、生产地址和产线证据。好评率与回头率的统计周期、分母尚未核实。</p></details>
          </article>'''
        cards.append(card)
    packs = sorted({c["pack"] for c in candidates if c["pack"] is not None})
    options = ''.join(f'<option value="{p}">{p} 件/组件（标题）</option>' for p in packs)
    matched = sum(c["match"] == "matched" for c in candidates)
    factory = sum(c["factory"] and c["match"] == "matched" for c in candidates)
    return f'''<section id="supplier-screening" data-asin="{escape(asin, quote=True)}">
      <h2>1688 供应商初筛</h2>
      <p>去重后 {len(candidates)} 条供货链接 · 同用途线索 {matched} 条 · 同用途工厂标签 {factory} 条。店铺主体未齐，链接数不等于独立供应商数。</p>
      <p class="supplier-muted">展示价未核对目标 SKU，不进入利润计算；服务评分、品质体验不等于商品好评率。初筛仅供询样比较。</p>
      <div class="supplier-filters">
      <label>搜索<input type="search" data-filter="search" placeholder="产品 / 店铺 / 货号"></label>
      <label>匹配<select data-filter="match"><option value="eligible">同用途及待核</option><option value="matched">同用途线索</option><option value="pending">匹配待核</option><option value="excluded">已排除</option><option value="">全部</option></select></label>
      <label>标题件数<select data-filter="pack"><option value="">全部件数</option>{options}</select></label>
      <label>工厂证据<select data-filter="factory"><option value="">全部</option><option value="1">有平台工厂标签</option><option value="0">工厂待核</option></select></label>
      <label>展示价上限 ¥<input data-filter="price" type="number" min="0" step="0.01" placeholder="不限"></label>
      <label>回头率下限 %<input data-filter="repurchase" type="number" min="0" max="100" step="1" placeholder="不限"></label>
      <label>商品评分下限 /5<input data-filter="rating" type="number" min="0" max="5" step="0.1" placeholder="不限"></label>
      <label>排序<select data-filter="sort"><option value="rank">初筛优先级</option><option value="price">展示价低到高（未校规格）</option><option value="repurchase">回头率高到低</option><option value="rating">商品评分高到低</option><option value="service">服务评分高到低</option><option value="factory">工厂标签优先</option></select></label>
      </div>
      <div class="supplier-toolbar"><label><input type="checkbox" data-filter="selected">仅看已选</label>
      <span data-supplier-count aria-live="polite"></span><button type="button" data-supplier-reset title="重置筛选" aria-label="重置筛选"><i data-lucide="rotate-ccw"></i></button></div>
      <p class="supplier-save-state" aria-live="polite"></p>
      <details class="supplier-order"><summary>初筛依据</summary><p>先核用途与质量风险，再参考工厂标签、商品评分、回头率和服务评分；缺失不视为零分或合格。价格保留独立排序，跨尺寸、材质、件数不能直接比价。优先询样不代表已验证自有工厂或可下单。</p></details>
      <div class="supplier-grid">{''.join(cards)}</div>
      <p data-supplier-empty hidden>没有符合当前条件的供货链接。</p>
      {'' if candidates else '<p>尚无 1688 候选数据。</p>'}
      </section>'''
