"""Evidence-backed color, size and sellable-unit comparisons within each form."""

import json
import re
from collections import defaultdict
from datetime import date
from statistics import median

from research_landscape import optional_number, read_json

COLORS = {
    "white": ("白色", "#ffffff"), "pink": ("粉色", "#e99ab0"),
    "black": ("黑色", "#262626"), "red": ("红色", "#ce4147"),
    "blue": ("蓝色", "#4081ce"), "green": ("绿色", "#42966a"),
    "yellow": ("黄色", "#e9c848"), "orange": ("橙色", "#e58d37"),
    "purple": ("紫色", "#9670b7"), "gray": ("灰色", "#9da3aa"),
    "silver": ("银色", "#bdc3c7"), "clear": ("透明", "#f0f5f7"),
    "gold": ("金色", "#c9ad57"), "brown": ("棕色", "#94775d"),
}
UNKNOWN = "未注明 / 待核"
NUMBER = r"\d+(?:\.\d+)?"
VOLUME = re.compile(rf"\b({NUMBER})\s*[- ]?(ml|milliliters?|liters?|litres?|l|cups?|fl\.?\s*oz|fluid ounces?)\b", re.I)
PACK = re.compile(r"\b(?:pack\s+of\s+(\d+)|(\d+)\s*[- ]?pack|(?P<word>one|two|three|four|single)\s*[- ]?pack)\b", re.I)
COUNTS = {"one": 1, "single": 1, "two": 2, "three": 3, "four": 4}


def attributes(value):
    if isinstance(value, dict):
        return {str(k).strip().lower(): str(v).strip() for k, v in value.items() if v is not None}
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return attributes(parsed)
    # Endpoint property strings use key:value; reviews use key=value.
    pairs = re.split(r"[,;]\s*(?=[A-Za-z][A-Za-z _]*[:=])", value)
    return {m[1].strip().lower(): m[2].strip() for p in pairs
            if (m := re.fullmatch(r"\s*([^:=]+)[:=](.+)", p))}


def color_fact(raw, source):
    cleaned = re.sub(r"\bgrey\b", "gray", str(raw).lower())
    cleaned = re.sub(r"\btransparent\b", "clear", cleaned)
    found = [key for key in COLORS if re.search(rf"\b{key}\b", cleaned)]
    return {"label": " / ".join(COLORS[key][0] for key in found) or UNKNOWN,
            "swatches": [COLORS[key][1] for key in found], "raw": str(raw), "source": source}


def pack_fact(raw, source):
    found = {int(m[1] or m[2]) if not m['word'] else COUNTS[m['word'].lower()]
             for m in PACK.finditer(str(raw))}
    if len(found) == 1 and next(iter(found)) > 0:
        quantity = next(iter(found))
        return {"label": f"{quantity}-pack", "quantity": quantity, "raw": str(raw), "source": source}
    if len(found) > 1:
        label = "装数冲突 / 待核"
    elif re.search(r"\b\d+\s*[- ]?(?:pcs|pieces?|count)\b|\bset of \d+\b", str(raw), re.I):
        label = "组件件数 / 装数待核"
    else:
        label = UNKNOWN
    return {"label": label, "quantity": None, "raw": str(raw), "source": source}


def capacity_values(raw):
    result = []
    for m in VOLUME.finditer(str(raw)):
        unit = m[2].lower()
        factor = 1 if unit.startswith(('ml', 'milliliter')) else 236.588 if unit.startswith('cup') else 29.5735 if 'oz' in unit or 'ounce' in unit else 1000
        result.append(round(float(m[1]) * factor, 1))
    return result


def size_facts(attrs, title, description=""):
    raw = attrs.get("capacity", "")
    values = capacity_values(raw or title)
    extra = capacity_values(description) if raw else []
    conflict = bool(values and values[0] > 0 and any(abs(v - values[0]) / values[0] > .05 for v in values + extra))
    if conflict:
        capacity = {"label": "容量标注冲突", "raw": raw + "；描述容量：" + ", ".join(f"{v:g} ml" for v in extra), "source": "商品属性 / 描述"}
    elif values and values[0] > 0:
        capacity = {"label": f"容量约 {values[0]:g} ml", "raw": raw or title, "source": "商品属性" if raw else "标题"}
    else:
        capacity = {"label": UNKNOWN, "raw": raw or "", "source": "容量"}
    # Packaging is intentionally not a fallback for product dimensions.
    physical = attrs.get("product dimensions") or attrs.get("item dimensions l x w x h") or attrs.get("item dimensions")
    size = attrs.get("size", "")
    if not physical and size and not capacity_values(size) and not PACK.search(size):
        physical = size
    if not physical:
        match = re.search(rf"\b{NUMBER}\s*[x×]\s*{NUMBER}(?:\s*[x×]\s*{NUMBER})?\s*(?:cm|mm|inches|inch|in)\b", title, re.I)
        physical = match[0] if match else ""
    dimension = {"label": physical_size(physical) or UNKNOWN, "raw": physical or "", "source": "产品尺寸 / Size" if physical else "产品尺寸"}
    return capacity, dimension


def physical_size(value):
    if not value:
        return ''
    value = str(value).strip()
    dimensions = re.fullmatch(rf'({NUMBER})(?:\s*[x×]\s*({NUMBER}))?(?:\s*[x×]\s*({NUMBER}))?\s*(cm|mm|inches|inch|in|")', value, re.I)
    if dimensions:
        unit = dimensions[4].lower()
        factor = 10 if unit == 'cm' else 1 if unit == 'mm' else 25.4
        return ' × '.join(f'{float(v) * factor:g}' for v in dimensions.groups()[:3] if v) + ' mm'
    return value


def payload_data(saved):
    value = saved.get("payload", saved)
    if isinstance(value, dict):
        value = value.get("data", value.get("result", value))
    return value


def evidence(root, study, asin, kind):
    for folder in ('attribute_evidence', 'new_entrant_evidence'):
        path = study / folder / f'{asin}_{kind}.json'
        if path.is_file():
            saved = read_json(path)
            data = payload_data(saved)
            requested = saved.get('arguments', {}).get('asin', asin)
            valid = isinstance(data, dict) and data.get('asin') == asin if kind == 'detail' else isinstance(data, list)
            if valid and requested == asin:
                return data, str(path.relative_to(root)), saved.get('retrieved_on', '')
    if kind == 'detail':
        sources = read_json(study / 'research_sources.json')
        detail = sources.get('detail', {})
        if detail.get('asin') == asin:
            return detail, str((study / 'research_sources.json').relative_to(root)), sources.get('retrieved_on', '')
        path = root / '.local/sorftime_image_details/US' / f'{asin}.json'
        saved = read_json(path)
        data = saved.get('data', {})
        if data.get('asin') == asin:
            return data, str(path.relative_to(root)), saved.get('retrieved_at', '')[:10]
    return {}, '', ''


def product_facts(product, detail):
    attrs = attributes(detail.get('attributes'))
    title = ' '.join(filter(None, (detail.get('title') or product.get('title', ''), detail.get('sub_title', ''))))
    color = color_fact(attrs.get('color') or title, '商品属性' if attrs.get('color') else '标题')
    pack_raw = attrs.get('item package quantity') or attrs.get('number of bottles')
    if pack_raw and re.fullmatch(r'\d+', pack_raw):
        pack_raw += '-pack'
    pack = pack_fact(pack_raw or title, '商品属性' if pack_raw else '标题；不采用旧默认装数')
    capacity, size = size_facts(attrs, title, detail.get('description', ''))
    return {"color": color, "capacity": capacity, "size": size, "pack": pack}


def summarize_groups(products, key):
    grouped = defaultdict(list)
    for p in products:
        grouped[p['facts'][key]['label']].append(p)
    result = []
    for label, members in grouped.items():
        prices = [n for p in members if (n := optional_number(p.get('price'))) is not None and n > 0]
        # Only explicit main-product pack quantities permit per-unit pricing.
        units = [n / p['facts']['pack']['quantity'] for p in members
                 if (n := optional_number(p.get('price'))) is not None and n > 0 and p['facts']['pack']['quantity']]
        result.append({'label': label, 'count': len(members), 'share': len(members) / len(products),
                       'median_price': median(prices) if prices else None,
                       'median_unit_price': median(units) if units else None,
                       'swatches': members[0]['facts'][key].get('swatches', []), 'members': members})
    return sorted(result, key=lambda g: (g['label'] == UNKNOWN, -g['count'], g['label']))


def analyze_dimensions(root, study, landscape, reviews=()):
    forms, sources = [], set()
    seed_asin = study.name
    for form in landscape.get('forms', []):
        members = []
        for row in {p['asin']: p for p in form['products']}.values():
            detail, path, retrieved = evidence(root, study, row['asin'], 'detail')
            if path:
                sources.add(path)
            members.append({**row, 'facts': product_facts(row, detail if isinstance(detail, dict) else {}),
                            'main_image_url': detail.get('main_image', '') if isinstance(detail, dict) else '',
                            'attribute_source': path or '已有标题快照', 'attribute_date': retrieved})
        forms.append({'form': form['form'], 'label': form['label'], 'is_target': form['is_target'],
                      'count': len(members), 'groups': {key: summarize_groups(members, key) for key in ('color', 'capacity', 'size', 'pack')}})
    data, path, retrieved = evidence(root, study, seed_asin, 'variations')
    variants, seen = [], set()
    for item in data if isinstance(data, list) else []:
        row = {k.lower().replace('_', ''): v for k, v in item.items()}
        asin = row.get('asin', '')
        if not re.fullmatch('[A-Z0-9]{10}', asin) or asin in seen:
            continue
        seen.add(asin)
        props = attributes(row.get('property', ''))
        pack = pack_fact(row.get('property', ''), '子体属性')
        variants.append({'asin': asin, 'property': row.get('property', ''),
                         'color': color_fact(props.get('color', ''), '子体属性'), 'pack': pack,
                         'size': props.get('size') or props.get('capacity') or UNKNOWN,
                         'page_sales_signal': optional_number(row.get('salesamount'))})
    unique_reviews = {}
    target_asins = {p['asin'] for f in landscape.get('forms', []) for p in f['products']}
    for row in reviews:
        if row.get('review_target_asin', row.get('asin')) not in target_asins:
            continue
        # Shared reviews repeated under different collection targets count once.
        text = ' '.join(row.get('review_text', '').split())
        if text:
            unique_reviews.setdefault((text, row.get('review_date'), row.get('rating')), row)
    review_colors = defaultdict(int)
    for row in unique_reviews.values():
        review_colors[color_fact(attributes(row.get('asin_property')).get('color', ''), '评论属性')['label']] += 1
    return {'seed_asin': seed_asin, 'forms': forms, 'variants': variants,
            'variant_source': path, 'variant_retrieved_on': retrieved,
            'variant_total': max((int(r.get('ItemTotal') or r.get('item_total') or 0) for r in data), default=0) if isinstance(data, list) else 0,
            'review_colors': dict(review_colors), 'review_count': len(unique_reviews),
            'sources': sorted(sources), 'analysis_date': date.today().isoformat()}


def collect_dimensions(root, study, landscape, max_calls=8, client=None):
    from sorftime_mcp_client import SorftimeMcpClient
    calls, reused, errors = 0, 0, []
    products = {p['asin']: p for f in landscape.get('forms', []) for p in f['products']}
    ordered = sorted(products, key=lambda a: (a != study.name, -float(products[a].get('monthly_sales') or 0)))
    for asin in ordered:
        for kind in ('detail', 'variations') if asin == study.name else ('detail',):
            data, _, _ = evidence(root, study, asin, kind)
            if data:
                reused += 1
                continue
            if calls >= max_calls or errors:
                continue
            args = {'asin': asin, 'amz_site': 'US', **({'page': 1} if kind == 'variations' else {})}
            try:
                client = client or SorftimeMcpClient(max_attempts=1)
                calls += 1
                payload = client.call_tool('product_' + kind, args)
                data = payload_data(payload)
                if kind == 'detail' and (not isinstance(data, dict) or data.get('asin') != asin):
                    raise ValueError('invalid detail')
                if kind == 'variations' and not isinstance(data, list):
                    raise ValueError('invalid variations')
                target = study / 'attribute_evidence' / f'{asin}_{kind}.json'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps({'tool': 'product_' + kind, 'arguments': args,
                    'retrieved_on': date.today().isoformat(), 'payload': payload}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            except Exception as error:
                errors.append({'asin': asin, 'kind': kind, 'error_type': type(error).__name__})
    result = {'calls': calls, 'cache_reuses': reused, 'errors': errors, 'max_calls': max_calls,
              'checked_on': date.today().isoformat(), 'unfetched': [a for a in ordered if not evidence(root, study, a, 'detail')[0]]}
    (study / 'dimension_collection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result
