import unittest
import copy
from html.parser import HTMLParser

from build_product_research_pages import (
    build_profit_calculator_defaults,
    input_number,
    render_gallery,
    render_gallery_form_options,
    render_gallery_scopes,
    render_forms,
    render_supplier_evidence,
    render_landscape,
    render_landscape_products,
)


class ProductResearchGalleryTest(unittest.TestCase):
    def test_snapshot_forms_show_one_matching_image_and_asin_per_row(self):
        products = [
            {"asin": "B000000001", "product_form": "bottle", "competitor_type": "seed",
             "monthly_sales": 9000, "title": "Bottle without photo"},
            {"asin": "B000000002", "product_form": "bottle", "title": 'Bottle <small> & "clear"',
             "web_image_url": "assets/STUDY/bottle.jpg"},
            {"asin": "B000000003", "product_form": "whisk utensil", "title": "Whisk",
             "main_image_url": '["https://m.media-amazon.com/images/I/whisk.jpg"]'},
            {"asin": "B000000004", "product_form": "other", "title": "Other",
             "web_image_url": "assets/STUDY/other.jpg"},
        ]
        before = copy.deepcopy(products)
        html = render_forms([{"product_form": "bottle", "count": "12"},
                             {"product_form": "whisk utensil", "count": "1"}], products)
        self.assertEqual(html.count("data-example-asin="), 2)
        self.assertEqual(html.count("<img"), 2)
        self.assertIn('href="https://www.amazon.com/dp/B000000002"', html)
        self.assertIn('src="assets/STUDY/bottle.jpg"', html)
        self.assertIn('alt="Bottle &lt;small&gt; &amp; &quot;clear&quot;"', html)
        self.assertIn('width="88" height="88" loading="lazy"', html)
        self.assertIn('src="https://m.media-amazon.com/images/I/whisk.jpg"', html)
        self.assertNotIn("B000000004", html)
        self.assertNotIn("<small>", html)
        self.assertEqual(products, before)
        HTMLParser().feed(html)

    def test_snapshot_form_without_image_keeps_asin_and_does_not_borrow_other_form(self):
        html = render_forms([{"product_form": "bottle"}, {"product_form": "missing"}], [
            {"asin": "B000000001", "product_form": "bottle", "main_image_url": "javascript:alert(1)"},
            {"asin": "B000000002", "product_form": "new bottle", "web_image_url": "assets/new.jpg"},
        ])
        self.assertIn("待补图", html)
        self.assertIn("暂无对应样本", html)
        self.assertIn("amazon.com/dp/B000000001", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("B000000002", html)

    def test_scopes_keep_snapshot_options_separate_from_current(self):
        html = render_gallery_scopes(self.products, [self.products[0]])
        self.assertIn('data-gallery-scope="snapshot">', html)
        self.assertIn('data-gallery-scope="current" hidden>', html)
        self.assertEqual(html.count("data-gallery-item"), 3)
        current = html.split('id="galleryOptions-current"')[1]
        self.assertNotIn('rigid magnetic parts tray', current)
        html = render_forms([{"product_form": 'A "quoted" <form>'}])
        self.assertIn('data-snapshot-form="A &quot;quoted&quot; &lt;form&gt;"', html)

    def test_gallery_uses_scope_form_over_visual_tag_and_safe_cached_urls(self):
        row = {**self.products[0], "visual_product_form": "old visual tag",
               "main_image_url": '["https://m.media-amazon.com/images/I/cached.jpg"]'}
        html = render_gallery([row])
        self.assertIn('data-form="flexible magnetic tool mat set"', html)
        self.assertIn('src="https://m.media-amazon.com/images/I/cached.jpg"', html)
        row["main_image_url"] = "http://127.0.0.1/private"
        self.assertNotIn("<img", render_gallery([row]))

    def test_landscape_card_renders_sales_listing_date_and_price_from_product(self) -> None:
        refs = [{"asin": "B000000001", "title": "Drain cover", "monthly_sales": 10595,
                 "price": 11.95, "online_date": "2011-04-10",
                 "metric_sources": {field: {"path": "search.json", "retrieved_on": "2026-09-05"}
                                    for field in ("monthly_sales", "price", "online_date")}}]
        html = render_landscape_products(refs, {"B000000001": {"monthly_sales": 999, "price": 100,
                                                               "online_date": "2026-09-06"}})
        for value in ("月销 10,595", "价格 $11.95", "上架 2011-04-10", "采集 2026-09-05"):
            self.assertIn(value, html)
        self.assertNotIn("2026-09-06", html)
        self.assertIn("来源：search.json", html)

    def test_landscape_card_distinguishes_missing_metrics_from_zero(self) -> None:
        html = render_landscape_products([{"asin": "B000000001", "monthly_sales": 0, "price": 0}], {})
        self.assertIn("月销 0", html)
        self.assertIn("价格 $0.00", html)
        self.assertIn("上架 未提供", html)
        html = render_landscape_products([{"asin": "B000000002", "monthly_sales": None,
                                           "price": "NaN", "online_date": "2026-99-01"}], {})
        for label in ("月销", "价格", "上架"):
            self.assertIn(f"{label} 未提供", html)

    def test_landscape_images_match_asin_not_product_order(self) -> None:
        refs = [
            {"asin": "B000000001", "title": "Soft drain cover", "monthly_sales": 123,
             "excluded_reason": "missing category evidence"},
            {"asin": "B000000002", "title": "Steel drain stopper", "monthly_sales": 400,
             "excluded_reason": ""},
        ]
        lookup = {"B000000002": {"web_image_url": "assets/STUDY/steel.jpg"},
                  "B000000001": {"web_image_url": "assets/STUDY/soft.jpg"}}
        before = copy.deepcopy(refs)
        html = render_landscape_products(refs, lookup)
        self.assertLess(html.index("soft.jpg"), html.index("steel.jpg"))
        self.assertIn('href="https://www.amazon.com/dp/B000000001"', html)
        self.assertIn('alt="Soft drain cover"', html)
        self.assertIn('width="120" height="120" loading="lazy"', html)
        self.assertIn("月销 123", html)
        self.assertIn("已排除：missing category evidence", html)
        self.assertEqual(refs, before)

    def test_landscape_missing_image_keeps_link_and_escapes_text(self) -> None:
        html = render_landscape_products([
            {"asin": "B000000001", "title": 'A <script> & "title"', "monthly_sales": 0,
             "excluded_reason": "<risk>"},
        ], {})
        self.assertNotIn("<img", html)
        self.assertNotIn("<script>", html)
        self.assertIn("待补图", html)
        self.assertIn("&lt;risk&gt;", html)
        self.assertIn("amazon.com/dp/B000000001", html)

    def test_landscape_can_reuse_cached_sorftime_image_url(self) -> None:
        product = {"asin": "B000000001", "title": "Cover", "monthly_sales": 100}
        url = "https://m.media-amazon.com/images/I/example.jpg"
        html = render_landscape_products([product], {product["asin"]: {"main_image_url": url}})
        self.assertIn(f'src="{url}"', html)
        html = render_landscape_products([product], {product["asin"]: {"main_image_url": "javascript:alert(1)"}})
        self.assertNotIn("<img", html)

    def test_landscape_expansion_spans_full_table_and_preserves_all_products(self) -> None:
        form = {"label": "Soft cover", "is_target": True, "next_check": "Check specs",
                "count": 9, "eligible_count": 0, "monthly_sales": 1000, "sales_share": 1,
                "median_monthly_sales": 100, "median_price": 7, "median_reviews": 50,
                "new_entrants": {"new_entrant_count": 0, "new_entrant_success_count": 0},
                "packs": [{"quantity": "2", "count": 9, "monthly_sales": 1000, "median_price": 7}],
                "products": [{"asin": f"B{i:09}", "title": "Cover", "monthly_sales": 100,
                              "excluded_reason": ""} for i in range(9)]}
        landscape = {"forms": [form], "family_label": "Stoppers", "source_product_count": 9,
                     "related_product_count": 9, "other_or_unresolved_count": 0,
                     "scope_note": "Sample only", "decision_note": "Not approval"}
        html = render_landscape(landscape, [{"asin": "B000000000", "web_image_url": "assets/STUDY/first.jpg"}])
        self.assertIn('<td colspan="8"><details class="landscape-details">', html)
        self.assertEqual(html.count("data-reference-asin="), 9)
        self.assertIn("2 件/组件", html)
        self.assertIn("first.jpg", html)
        HTMLParser().feed(html)

    def test_supplier_results_distinguish_search_from_sku_verification(self) -> None:
        html = render_supplier_evidence([
            {"source_platform": "1688", "source_product_id": "123", "supplier": "Example <script>",
             "source_sku_name": "201 / 25cm", "source_unit_price": "1.8", "match_status": "material_mismatch",
             "match_reason": "不是304"},
        ], {"supplier_candidates": [{}, {}, {}]})
        self.assertIn("已返回 3 条原始结果", html)
        self.assertIn("¥1.80", html)
        self.assertIn("材质不匹配", html)
        self.assertIn("detail.1688.com/offer/123.html", html)
        self.assertNotIn("<script>", html)
        self.assertIn("未记录", render_supplier_evidence([], {}))
        self.assertIn("已返回 0 条", render_supplier_evidence([], {"supplier_candidates": []}))

    def setUp(self) -> None:
        self.products = [
            {
                "asin": "DIRECT01",
                "title": "Flexible Magnetic Tool Mat",
                "listing_url": "https://example.com/direct",
                "product_form": "flexible magnetic tool mat set",
                "material": "PVC leather",
                "pack_count": "3",
            },
            {
                "asin": "TRAY01",
                "title": "Rigid Magnetic Parts Tray",
                "listing_url": "https://example.com/tray",
                "product_form": "rigid magnetic parts tray",
                "material": "steel",
                "pack_count": "1",
            },
        ]

    def test_gallery_cards_include_filter_metadata(self) -> None:
        html = render_gallery(self.products)

        self.assertIn('data-form="flexible magnetic tool mat set"', html)
        self.assertIn('data-search="direct01 flexible magnetic tool mat', html)
        self.assertIn('data-form="rigid magnetic parts tray"', html)

    def test_form_options_are_unique_and_keep_product_order(self) -> None:
        html = render_gallery_form_options([self.products[0], self.products[0], self.products[1]])

        self.assertEqual(html.count("flexible magnetic tool mat set"), 2)
        self.assertLess(html.index("flexible magnetic tool mat set"), html.index("rigid magnetic parts tray"))

    def test_profit_calculator_keeps_unverified_costs_blank(self) -> None:
        defaults = build_profit_calculator_defaults(
            {"price": "35.99", "monthly_sales": "978"},
            {
                "target_price": 35.99,
                "cny_per_usd": 7,
                "packaging_cost": 0,
                "freight_cost": 2.88,
                "fba_fee": 6.48,
                "assumptions": {
                    "packaging": "planning_rate",
                    "freight": "planning_rate",
                    "fba_fee": "planning_rate",
                    "referral_fee_rate": 0.15,
                    "ad_cost_rate": 0.15,
                },
            },
            {
                "planning_cny_per_usd": 7,
                "assumed_return_rate": 0.05,
                "assumed_return_loss_rate": 0.5,
                "assumed_coupon_rate": 0.03,
                "assumed_storage_rate": 0.02,
            },
        )

        self.assertEqual(defaults["selling_price_usd"], 35.99)
        self.assertEqual(defaults["cny_per_usd"], 7)
        self.assertIsNone(defaults["procurement_cost_cny"])
        self.assertIsNone(defaults["packaging_cost_usd"])
        self.assertIsNone(defaults["freight_cost_usd"])
        self.assertIsNone(defaults["fba_fee_usd"])
        self.assertEqual(defaults["return_loss_rate_pct"], 2.5)
        self.assertEqual(input_number(defaults["procurement_cost_cny"]), "")
        self.assertEqual(input_number(0), "0")

    def test_profit_calculator_prefills_confirmed_costs(self) -> None:
        defaults = build_profit_calculator_defaults(
            {"price": "29.99"},
            {
                "procurement_cost_cny": 42,
                "cny_per_usd": 7.2,
                "packaging_cost": 0.6,
                "freight_cost": 1.8,
                "fba_fee": 5.1,
                "assumptions": {
                    "packaging": "quote_median",
                    "freight": "confirmed",
                    "fba_fee": "product_detail",
                },
            },
            {"referral_fee_rate": 0.15, "target_ad_cost_rate": 0.12},
        )

        self.assertEqual(defaults["procurement_cost_cny"], 42)
        self.assertEqual(defaults["packaging_cost_usd"], 0.6)
        self.assertEqual(defaults["freight_cost_usd"], 1.8)
        self.assertEqual(defaults["fba_fee_usd"], 5.1)
        self.assertEqual(defaults["referral_rate_pct"], 15)
        self.assertEqual(defaults["ad_rate_pct"], 12)

    def test_profit_calculator_prefills_observed_summary_fba_fee(self) -> None:
        defaults = build_profit_calculator_defaults(
            {"price": "2.65"},
            {
                "target_price": 2.65,
                "fba_fee": None,
                "assumptions": {"fba_fee": "planning_rate"},
            },
            {"referral_fee_rate": 0.15, "target_ad_cost_rate": 0.15},
            {"fba_fee": 3.01},
        )

        self.assertEqual(defaults["fba_fee_usd"], 3.01)


if __name__ == "__main__":
    unittest.main()
