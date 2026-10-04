#!/usr/bin/env python3
"""Regression tests for product-specific dashboard conclusions."""

from __future__ import annotations

import json
import unittest

from build_dashboard import build_single_product_insights


class DashboardInsightTest(unittest.TestCase):
    def test_non_beeswax_research_does_not_reuse_bread_bag_copy(self) -> None:
        products = [
            {
                "asin": "MAGNET1",
                "competitor_type": "seed",
                "product_form": "flexible magnetic tool mat set",
                "monthly_sales": 978,
                "reviews": 154,
                "rating": 4.6,
                "material": "PVC leather + embedded magnets",
                "pack_count": "3",
                "feature_tags": "strong magnet; flexible; surface protection",
                "use_case": "mechanic parts/tool retention",
                "style": "workshop utility",
                "image": "seed.jpg",
            },
            {
                "asin": "MAGNET2",
                "competitor_type": "direct",
                "product_form": "flexible magnetic tool mat set",
                "monthly_sales": 354,
                "reviews": 40,
                "rating": 4.5,
                "material": "PVC leather + embedded magnets",
                "pack_count": "1",
                "feature_tags": "strong magnet; flexible",
                "use_case": "mechanic parts/tool retention",
                "style": "workshop utility",
                "image": "direct.jpg",
            },
            {
                "asin": "WRIST1",
                "competitor_type": "keyword",
                "product_form": "magnetic wristband",
                "monthly_sales": 2170,
                "reviews": 21570,
                "rating": 4.7,
            },
        ]
        forms = [
            {
                "form": "flexible magnetic tool mat set",
                "low_review_high_sales_count": 1,
            }
        ]
        reviews = [
            {
                "review_target_asin": "MAGNET1",
                "rating": "2",
                "pain_point_tags": "magnet_strength;durability",
            }
        ]
        review_targets = [
            {
                "asin": "MAGNET1",
                "product_form": "flexible magnetic tool mat set",
            }
        ]
        business = {
            "profitability_verified": False,
            "quote_sample_count": 0,
            "candidate_quote_count": 14,
        }

        result = build_single_product_insights(products, forms, reviews, review_targets, business)
        rendered = json.dumps(result, ensure_ascii=False)

        self.assertNotIn("beeswax bread bag", rendered)
        self.assertNotIn("保鲜", rendered)
        self.assertIn("flexible magnetic tool mat set", rendered)
        self.assertIn("磁力/吸附稳定性", rendered)
        self.assertIn("利润未验证", rendered)
        self.assertIn("相邻形态", rendered)


if __name__ == "__main__":
    unittest.main()
