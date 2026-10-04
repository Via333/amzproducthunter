import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from research_dimensions import (UNKNOWN, attributes, color_fact, pack_fact, size_facts,
    product_facts, summarize_groups, analyze_dimensions, collect_dimensions, evidence)
from research_bundles import analyze_bundles, collect_associations
from research_dimension_view import render_dimensions, render_bundles


class DimensionResearchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.study = self.root / 'research/B000000001'
        self.study.mkdir(parents=True)
        self.product = {'asin': 'B000000001', 'title': 'Pancake Batter Dispenser Bottle White', 'price': 12,
                        'monthly_sales': 3000, 'parent_asin': 'B000000999'}
        self.landscape = {'forms': [{'form': 'bottle', 'label': '瓶式', 'is_target': True, 'products': [self.product]}]}

    def save(self, name, data):
        path = self.study / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def test_structured_and_endpoint_properties(self):
        self.assertEqual(attributes('{"Color":"White","Size":"10 cm"}'), {'color': 'White', 'size': '10 cm'})
        self.assertEqual(attributes('Color:2 Pack-Pink,Size:10 x 20 cm'), {'color': '2 Pack-Pink', 'size': '10 x 20 cm'})
        self.assertEqual(attributes('Color=Pink'), {'color': 'Pink'})
        self.assertEqual(attributes(None), {})

    def test_color_and_pack_are_independent_dimensions(self):
        self.assertEqual(color_fact('2 Pack-White', 'property')['label'], '白色')
        self.assertEqual(pack_fact('2 Pack-White', 'property')['quantity'], 2)
        self.assertEqual(color_fact('blue / red', 'property')['label'], '红色 / 蓝色')
        self.assertEqual(color_fact('unknown shade', 'property')['label'], UNKNOWN)

    def test_pack_does_not_use_default_or_component_count(self):
        for text in ('Bottle', '4 Piece Bottle Kit with Brush', '2PCS bottle kit'):
            self.assertIsNone(pack_fact(text, 'title')['quantity'])
        self.assertEqual(pack_fact('One Pack', 'title')['quantity'], 1)
        self.assertEqual(pack_fact('Pack of 3', 'title')['quantity'], 3)
        self.assertIsNone(pack_fact('2-pack or 4-pack', 'title')['quantity'])
        facts = product_facts({'title': 'Bottle', 'pack_count': 1}, {})
        self.assertEqual(facts['pack']['label'], UNKNOWN)

    def test_capacity_is_normalized_and_conflicts_are_retained(self):
        self.assertEqual(size_facts({'capacity': '1 L'}, '')[0]['label'], '容量约 1000 ml')
        self.assertEqual(size_facts({'capacity': '4.5 cups'}, '', '1000ml bottle')[0]['label'], '容量标注冲突')
        self.assertEqual(size_facts({'capacity': '2.1 pounds'}, '')[0]['label'], UNKNOWN)
        self.assertEqual(size_facts({'capacity': '0 ml'}, '')[0]['label'], UNKNOWN)

    def test_packaging_does_not_become_product_size(self):
        cap, size = size_facts({'package dimensions': '20 x 30 x 40 cm'}, '')
        self.assertEqual(size['label'], UNKNOWN)
        a = size_facts({'product dimensions': '10 inch'}, '')[1]['label']
        b = size_facts({'product dimensions': '25.4 cm'}, '')[1]['label']
        self.assertEqual(a, b)

    def test_no_parent_sales_allocation_and_per_unit_prices_require_pack(self):
        products = [{**self.product, 'facts': product_facts(self.product, {})},
                    {**self.product, 'asin': 'B000000002', 'title': 'White Bottle 2 pack', 'price': 20,
                     'facts': product_facts({'title': 'White Bottle 2 pack'}, {})}]
        groups = summarize_groups(products, 'color')
        self.assertEqual(groups[0]['count'], 2)
        self.assertEqual(groups[0]['median_unit_price'], 10)
        self.assertNotIn('monthly_sales', groups[0])
        self.assertEqual(groups[0]['share'], 1)

    def test_forms_stay_separate_and_empty_sources_are_supported(self):
        other = {'form': 'funnel', 'label': '漏斗', 'is_target': False,
                 'products': [{**self.product, 'asin': 'B000000002', 'title': 'Blue funnel'}]}
        landscape = {'forms': self.landscape['forms'] + [other]}
        before = copy.deepcopy(landscape)
        result = analyze_dimensions(self.root, self.study, landscape)
        self.assertEqual([f['count'] for f in result['forms']], [1, 1])
        self.assertEqual(landscape, before)
        self.assertEqual(analyze_dimensions(self.root, self.study, {'forms': []})['forms'], [])

    def test_child_negative_sales_is_missing_not_zero_and_no_inherited_size(self):
        self.save('attribute_evidence/B000000001_variations.json', {'payload': {'data': [
            {'Asin': 'B000000001', 'Property': 'Color:White', 'SalesAmount': 1000, 'ItemTotal': 3},
            {'Asin': 'B000000002', 'Property': 'Color:2 Pack-White', 'SalesAmount': -1, 'ItemTotal': 3},
        ]}})
        result = analyze_dimensions(self.root, self.study, self.landscape)
        self.assertEqual(result['variant_total'], 3)
        self.assertEqual(len(result['variants']), 2)
        self.assertIsNone(result['variants'][1]['page_sales_signal'])
        self.assertEqual(result['variants'][0]['size'], UNKNOWN)
        self.assertIsNone(result['variants'][0]['pack']['quantity'])

    def test_shared_reviews_deduped_without_inheriting_listing_color(self):
        row = {'asin': 'B000000001', 'review_target_asin': 'B000000001', 'review_text': 'Works.', 'rating': '5', 'review_date': '20260908', 'asin_property': 'Color=Pink'}
        other = {**row, 'review_text': 'No property', 'asin_property': ''}
        result = analyze_dimensions(self.root, self.study, self.landscape, [row, dict(row), other])
        self.assertEqual(result['review_count'], 2)
        self.assertEqual(result['review_colors'], {'粉色': 1, UNKNOWN: 1})

    def test_wrong_asin_detail_is_not_reused(self):
        self.save('attribute_evidence/B000000001_detail.json', {'payload': {'data': {'asin': 'B000000002'}}})
        self.assertFalse(evidence(self.root, self.study, 'B000000001', 'detail')[0])

    def test_collector_budget_and_failure_do_not_change_source(self):
        client = Mock()
        client.call_tool.return_value = {'data': {**self.product, 'attributes': {'Color': 'White'}}}
        result = collect_dimensions(self.root, self.study, self.landscape, max_calls=1, client=client)
        self.assertEqual(result['calls'], 1)
        client.call_tool.assert_called_once()
        client.reset_mock()
        collect_dimensions(self.root, self.study, self.landscape, max_calls=0, client=client)
        client.call_tool.assert_not_called()
        client.call_tool.side_effect = RuntimeError('secret URL must not leak')
        result = collect_dimensions(self.root, self.study, self.landscape, max_calls=3, client=client)
        self.assertEqual(result['calls'], 1)
        self.assertNotIn('secret', json.dumps(result))

    def test_rendering_escapes_attributes_and_keeps_dimension_controls(self):
        self.save('research_sources.json', {'detail': {**self.product, 'attributes': {'Size': '<script>bad</script>'}}})
        result = analyze_dimensions(self.root, self.study, self.landscape)
        markup = render_dimensions(result, [], lambda p: p['asin'])
        self.assertNotIn('<script>', markup)
        self.assertIn('&lt;script&gt;', markup)
        self.assertIn('aria-label="规格研究维度"', markup)
        self.assertIn('不是颜色或规格销量份额', markup)

    def test_bundle_counts_are_review_mentions_not_buyer_counts(self):
        config = self.root / 'config'
        config.mkdir()
        (config / 'bundle_research_rules.json').write_text(json.dumps({'profiles': [{
            'id': 'batter', 'title_pattern': 'Batter', 'candidates': [{
                'id': 'brush', 'label': '刷具', 'pattern': 'brush', 'role': '已有配件', 'next_check': '核对'}]}]}))
        row = {'asin': self.study.name, 'review_text': 'Missing brush', 'rating': '1', 'review_date': '20260101'}
        result = analyze_bundles(self.root, self.study, self.landscape, [self.product], [row, dict(row)])
        self.assertEqual(result['review_count'], 1)
        self.assertEqual(result['candidates'][0]['review_mentions'], 1)
        self.assertIsNone(result['purchase_frequency'])
        self.assertFalse(result['candidates'][0]['included'])

    def test_only_target_fbt_observations_are_shown_without_frequency_claims(self):
        self.save('association_evidence.json', {'seed_asin': self.study.name, 'source_url': 'javascript:alert(1)', 'observations': [
            {'kind': 'amazon_fbt', 'asin': 'B000000002', 'label': '<img src=x>'},
            {'kind': 'search_similarity', 'asin': 'B000000003'},
            {'kind': 'amazon_fbt', 'asin': self.study.name},
            {'kind': 'amazon_fbt', 'asin': 'B000000002', 'label': 'duplicate'},
        ]})
        result = analyze_bundles(self.root, self.study, self.landscape, [self.product], [])
        self.assertEqual(len(result['observations']), 1)
        html = render_bundles(result)
        self.assertNotIn('javascript:', html)
        self.assertNotIn('<img src=x>', html)
        self.assertIn('不是“买得最多”排名', html)
        self.save('association_evidence.json', {'seed_asin': 'B000000999', 'observations': result['observations']})
        self.assertFalse(analyze_bundles(self.root, self.study, self.landscape, [self.product], [])['observations'])

    def test_live_collection_requires_named_fbt_widget_and_seed(self):
        markup = '''<div cel_widget_id="p13n-desktop-sims-fbt_DetailPage_0"><h2>Frequently bought together</h2>
          <div data-csa-c-item-id="amzn1.asin.B000000001"><div id="ProductTitle-1">Seed</div></div>
          <div data-csa-c-item-id="amzn1.asin.B000000002"><img src="https://m.media-amazon.com/images/I/example.jpg"><div id="ProductTitle-2">Companion</div></div></div>
          <div data-csa-c-item-id="amzn1.asin.B000000003">Unrelated recommendation</div>'''
        result = collect_associations(self.study, lambda url: markup)
        self.assertEqual(result['count'], 1)
        saved = (self.study / 'association_evidence.json').read_bytes()
        result = collect_associations(self.study, lambda url: 'captcha')
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual((self.study / 'association_evidence.json').read_bytes(), saved)
        result = collect_associations(self.study, lambda url: markup.replace('Frequently bought together', 'Sponsored related products'))
        self.assertEqual(result['count'], 0)
        result = collect_associations(self.study, lambda url: markup.replace('B000000001', 'B000000999'))
        self.assertEqual(result['count'], 0)


if __name__ == '__main__':
    unittest.main()
