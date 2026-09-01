import unittest

from mcp_product_opportunity_research import (
    broad_refill_snapshot,
    deep_product_form,
    embedded_number,
)


class McpProductOpportunityResearchTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
