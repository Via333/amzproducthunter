import csv
import tempfile
import unittest
from pathlib import Path
from business_feasibility import quote_rows_from_1688, traceable_1688_candidates, valid_1688_quotes

from mcp_product_opportunity_research import (
    broad_refill_snapshot,
    deep_product_form,
    embedded_number,
    local_category_node_id,
    make_summary,
    raw_row,
    review_pain_tags,
    supplier_matches,
)


class McpProductOpportunityResearchTest(unittest.TestCase):
    def test_rolling_pin_supplier_leads_survive_without_becoming_valid_quotes(self):
        rows = [
            {"title": "不锈钢擀面杖", "product_id": "101", "price": 1.5},
            {"title": "不锈钢面粉棍烘焙工具", "product_id": "102", "price": 2.2},
            {"title": "304不锈钢光轴圆棒", "product_id": "103", "price": 1.1},
        ]
        matched, reason = supplier_matches(rows, {"title": "Stainless Steel Hollow Rolling Pin"})
        self.assertEqual([row["product_id"] for row in matched], ["101", "102"])
        quotes = quote_rows_from_1688(matched, cny_per_usd=7, match_reason=reason)
        self.assertEqual(len(traceable_1688_candidates(quotes)), 2)
        self.assertEqual(valid_1688_quotes(quotes), [])
        quotes[0].update(price_scope="exact_sku", source_sku_id="25cm")
        self.assertEqual(valid_1688_quotes(quotes), [])

    def test_rejected_sku_is_not_a_pending_supplier_candidate(self):
        row = {"source_platform": "1688", "source_product_id": "123", "normalized_unit_cost_cny": "1.8"}
        for status in ("material_mismatch", "dimension_mismatch", "rejected", "unavailable"):
            self.assertEqual(traceable_1688_candidates([{**row, "match_status": status}]), [])

    def test_local_category_node_recovers_reference_asin_from_shape_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.csv"
            validation = root / "validation.csv"
            candidates.write_text("source_asin,source_category_id\n", encoding="utf-8")
            with validation.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["seed_asin", "source_category_id", "form_reference_asins", "shape_score"],
                )
                writer.writeheader()
                writer.writerow({
                    "source_category_id": "289724",
                    "form_reference_asins": "B0GLHYJ9L4:3429/55; B0OTHER:500/20",
                    "shape_score": "77.6",
                })

            self.assertEqual(
                local_category_node_id("b0glhyj9l4", candidates, validation),
                "289724",
            )

    def test_embedded_number_does_not_treat_top80_label_as_value_percent(self):
        self.assertAlmostEqual(embedded_number("Top-80% products average price: 16.700875"), 16.700875)
        self.assertAlmostEqual(
            embedded_number("Top-3 brands monthly sales share: 45.45%", percent=True),
            0.4545,
        )

    def test_deep_product_form_separates_refill_subtypes(self):
        self.assertEqual(
            deep_product_form({"title": "80 Count Duster Refills Compatible with Swiffer", "brand": "Example"}),
            "compatible disposable refill",
        )
        self.assertEqual(
            deep_product_form({"title": "20 Duster Refills with Extendable Handle", "brand": "Example"}),
            "duster refill kit",
        )
        self.assertEqual(
            deep_product_form({"title": "8 Pack Reusable Duster Refills", "brand": "Example"}),
            "reusable duster refill",
        )
        self.assertEqual(
            deep_product_form({"title": "Duster Refills, 11 Count", "brand": "Swiffer"}),
            "brand duster refill",
        )

    def test_broad_refill_snapshot_keeps_all_refill_subtypes(self):
        rows = [
            {"title": "80 Count Duster Refills Compatible with Swiffer", "product_category": "DUST REMOVAL TOOL", "monthly_sales_volume": 1200, "review_count": 90},
            {"title": "20 Duster Refills with Extendable Handle", "product_category": "DUST REMOVAL TOOL", "monthly_sales_volume": 900, "review_count": 250},
            {"title": "Cobweb Duster with Extension Pole", "product_category": "DUST REMOVAL TOOL", "monthly_sales_volume": 1000, "review_count": 500},
        ]
        snapshot = broad_refill_snapshot(rows)
        self.assertEqual(snapshot["count"], 2)
        self.assertEqual(snapshot["total_monthly_sales"], 2100)
        self.assertEqual(snapshot["low_review_high_sales_count"], 2)
        self.assertAlmostEqual(snapshot["sales_share"], 2100 / 3100, places=4)

    def test_target_form_products_are_direct_competitors(self):
        row = raw_row(
            "SEED",
            {
                "asin": "OTHER",
                "title": "Stainless Steel Japanese Double Cocktail Jigger",
                "product_category": "MEASURING CUP",
            },
            "Jiggers",
            "cocktail jigger - stainless steel - japanese",
        )

        self.assertEqual(row["product_form"], "cocktail jigger - stainless steel - japanese")
        self.assertEqual(row["competitor_type"], "direct")
        self.assertEqual(row["relevance_score"], 90)

    def test_bar_tool_review_tags_do_not_use_duster_taxonomy(self):
        tags = review_pain_tags(
            "The measurements are accurate, but the markings inside should be darker and easier to read.",
            "bar_measuring_tool",
        )

        self.assertIn("measurement_accuracy", tags)
        self.assertIn("marking_readability", tags)
        self.assertNotIn("fiber_shedding", tags)

    def test_jigger_summary_is_product_specific(self):
        raw_products = [
            {
                "asin": "SEED",
                "title": "2-Pack Stainless Steel Cocktail Jiggers",
                "product_category": "MEASURING CUP",
                "monthly_sales_volume": 3700,
                "review_count": 220,
                "price": 6.99,
            },
            {
                "asin": "OTHER",
                "title": "2-Pack Double Cocktail Jigger Set",
                "product_category": "MEASURING CUP",
                "monthly_sales_volume": 1600,
                "review_count": 120,
                "price": 8.99,
            },
        ]
        seed = {
            "asin": "SEED",
            "title": raw_products[0]["title"],
            "brand": "Example",
            "product_form": "measuring cup",
            "price": 6.99,
            "monthly_sales": 3700,
            "reviews": 220,
            "rating": 4.7,
        }
        summary = make_summary(
            seed,
            {
                "fba_fee": 3.16,
                "package_size_cm": "12.9*8.7*4.8",
                "weight_g": 91,
                "online_date": "2025-12-05",
                "variation_count": 9,
                "node_id": "2566780011",
            },
            {"top3_brands_sales_volume_share": "35%"},
            {
                "count": 2,
                "total_monthly_sales": 5300,
                "sales_share": 1,
                "median_reviews": 170,
                "median_price": 7.99,
                "low_review_high_sales_count": 2,
            },
            {"sales": ["2026-01=100", "2026-08=3700"], "price": []},
            [],
            {},
            [],
            raw_products,
            [{"pain_point_tags": "measurement_accuracy; marking_readability"}],
            "cached.json",
            8,
        )

        rendered = str(summary)
        self.assertEqual(summary["research_profile"], "bar_measuring_tool")
        self.assertEqual(summary["shape"]["name"], "measuring cup")
        self.assertIn("食品接触", rendered)
        self.assertNotIn("Swiffer", rendered)
        self.assertNotIn("duster refill", rendered)
        self.assertNotIn("80P", rendered)


if __name__ == "__main__":
    unittest.main()
