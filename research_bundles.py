"""Separate platform associations, review context and existing components."""

import re
import json
import urllib.request
from datetime import date
from html.parser import HTMLParser
from research_landscape import read_json


class FbtParser(HTMLParser):
    """Only collect products inside Amazon's explicitly named FBT widget."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.items = []
        self.heading = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        parent = self.stack[-1] if self.stack else {}
        widget = parent.get('widget', False) or 'p13n-desktop-sims-fbt' in attrs.get('cel_widget_id', '')
        current = parent.get('item')
        match = re.fullmatch(r'amzn1\.asin\.([A-Z0-9]{10})', attrs.get('data-csa-c-item-id', ''))
        if widget and match:
            current = {'asin': match[1], 'title_parts': [], 'image_url': ''}
            self.items.append(current)
        if widget and current is not None and tag == 'img':
            current['image_url'] = attrs.get('src', '')
        context = {'tag': tag, 'widget': widget, 'item': current,
                   'title': parent.get('title', False) or (widget and attrs.get('id', '').startswith('ProductTitle-')),
                   'heading': parent.get('heading', False) or (widget and tag == 'h2'),
                   'skip': parent.get('skip', False) or tag in ('script', 'style')}
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append(context)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        context = self.stack[-1] if self.stack else {}
        if context.get('skip'):
            return
        if context.get('heading'):
            self.heading.append(text)
        if context.get('title') and context.get('item') is not None:
            context['item']['title_parts'].append(text)

    def observations(self, seed_asin):
        if 'frequently bought together' not in ' '.join(self.heading).lower():
            return []
        if seed_asin not in {item['asin'] for item in self.items}:
            return []
        return [{'kind': 'amazon_fbt', 'asin': item['asin'],
                 'label': ' '.join(' '.join(item['title_parts']).split()), 'image_url': item['image_url'],
                 'interpretation': '平台同购模块中出现，尚无购买人数、比例或订单级证据。',
                 'bundle_assessment': '需先核对互补用途、是否重复、尺寸、新增成本和适用风险。'}
                for item in self.items if item['asin'] != seed_asin and item['title_parts']]


def collect_associations(study, fetch=None):
    """One public-page read; failures never erase an existing observation."""
    url = f'https://www.amazon.com/dp/{study.name}'
    if not re.fullmatch('[A-Z0-9]{10}', study.name):
        raise ValueError('Invalid ASIN')
    def download(url):
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read(4 * 1024 * 1024).decode('utf-8', errors='replace')
    result = {'checked_on': date.today().isoformat(), 'source_url': url, 'status': 'unavailable', 'count': 0}
    try:
        parser = FbtParser()
        parser.feed((fetch or download)(url))
        observations = parser.observations(study.name)
        if observations:
            saved = {'seed_asin': study.name, 'checked_on': result['checked_on'], 'source_url': url,
                     'source_status': '公开商品页单次采集，未取得同购订单比例',
                     'module': 'Frequently bought together', 'observations': observations}
            path = study / 'association_evidence.json'
            previous = read_json(path)
            if previous:
                history = study / 'association_history'
                history.mkdir(exist_ok=True)
                # Preserve prior evidence even when the module changes.
                index = len(list(history.glob('*.json'))) + 1
                (history / f'{index:04}.json').write_text(json.dumps(previous, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            path.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            result.update(status='observed', count=len(observations))
    except Exception as error:
        result['error_type'] = type(error).__name__
    (study / 'association_collection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def analyze_bundles(root, study, landscape, products, reviews):
    seed = next((p for p in products if p.get('asin') == study.name), {})
    source = read_json(study / 'research_sources.json')
    detail = source.get('detail', {})
    seed_text = ' '.join(str(detail.get(k) or seed.get(k) or '') for k in ('title', 'sub_title', 'description'))
    rules = read_json(root / 'config/bundle_research_rules.json')
    profile = next((p for p in rules.get('profiles', []) if re.search(p['title_pattern'], seed_text)), {})
    allowed = {p['asin'] for f in landscape.get('forms', []) for p in f['products']}
    unique = {}
    for row in reviews:
        if row.get('review_target_asin') not in allowed and row.get('asin') not in allowed:
            continue
        text = ' '.join(row.get('review_text', '').split())
        if text:
            unique.setdefault((text, row.get('review_date'), row.get('rating')), row)
    candidates = []
    for rule in profile.get('candidates', []):
        matched = [r for r in unique.values() if re.search(rule['pattern'], r.get('review_text', ''))]
        included = bool(re.search(rule['pattern'], seed_text)) and rule['role'].startswith('已有配件')
        if not matched and not included:
            continue
        candidates.append({**rule, 'review_mentions': len(matched), 'included': included,
                           'example_asins': sorted({r['asin'] for r in matched if r.get('asin')}),
                           'evidence': [{'asin': r.get('asin'), 'date': r.get('review_date'), 'text': r.get('review_text', ''),
                                         'rating': r.get('rating')} for r in matched]})
    candidates.sort(key=lambda c: (-c['review_mentions'], c['id']))
    saved = read_json(study / 'association_evidence.json')
    if saved.get('seed_asin') != study.name:
        saved = {}
    observed, seen = [], set()
    for row in saved.get('observations', []):
        asin = row.get('asin', '')
        key = (asin, row.get('kind'))
        if row.get('kind') != 'amazon_fbt' or not re.fullmatch('[A-Z0-9]{10}', asin) or asin == study.name or key in seen:
            continue
        seen.add(key)
        observed.append(row)
    return {'seed_asin': study.name, 'observations': observed, 'source_url': saved.get('source_url', ''),
            'source_status': saved.get('source_status', ''), 'checked_on': saved.get('checked_on', ''),
            'module': saved.get('module', ''), 'profile': profile.get('id', ''),
            'review_count': len(unique), 'candidates': candidates,
            'purchase_frequency': None, 'bundle_conversion': None}
