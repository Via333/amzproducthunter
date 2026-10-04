import copy
import json
import unittest
from pathlib import Path

from build_dashboard import normalize_shape_validation
from category_shape_validation import normalized_category_product
from product_market_validation import market_identity, merge_product_observations


class ProductMarketTest(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads(Path("config/category_shape_validation_rules.json").read_text())

    def observation(self, source, asin, title, sales=400, reviews=50, price=20):
        category = {"id": source, "name": {"1": "Jiggers", "2": "Bar Sets"}.get(source, source),
                    "path": "Home > " + source, "snapshot_id": "run1"}
        product = normalized_category_product({
            "asin": asin, "title": title, "monthly_sales_volume": sales,
            "review_count": reviews, "price": price, "brand": asin,
            "online_date": "2026-06-01", "product_category": "KITCHEN",
        }, category["path"])
        return category, product

    def test_cross_category_jiggers_dedupe_and_share_one_form(self):
        rows = [self.observation("1", "SAME", "Japanese Stainless Steel Jigger", 500),
                self.observation("2", "SAME", "2 Pack Japanese Stainless Steel Jiggers", 500),
                self.observation("2", "OTHER", "Japanese Stainless Steel Jigger", 300)]
        before = copy.deepcopy(rows)
        result = merge_product_observations(rows, self.rules, "run1")
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row["market_id"], "barware")
        self.assertEqual(row["family_sample_count"], 2)
        self.assertEqual(row["form_count"], 2)
        self.assertEqual(row["form_total_sales"], 800)
        self.assertEqual(row["form_avg_sales"], 400)
        self.assertEqual(row["family_duplicate_count"], 1)
        self.assertEqual({s["name"] for s in row["source_categories"]}, {"Jiggers", "Bar Sets"})
        self.assertEqual(rows, before)

    def test_full_kit_is_sibling_not_jigger_competitor(self):
        rows = [self.observation("1", "JIGGER", "Japanese Stainless Steel Jigger")]
        rows += [self.observation("2", f"KIT{i}", "Cocktail Shaker Set with Jigger", 30000, 9000)
                 for i in range(12)]
        result = merge_product_observations(rows, self.rules, "run1")
        self.assertEqual({r["market_id"] for r in result}, {"barware"})
        self.assertEqual({r["product_family"] for r in result}, {"cocktail_jigger", "cocktail_tool_kit"})
        jigger = next(r for r in result if r["product_family"] == "cocktail_jigger")
        self.assertEqual(jigger["family_sample_count"], 1)
        self.assertNotIn("top10 review wall", jigger["validation_flags"])

    def test_source_conflicts_use_stable_record_not_largest_sales(self):
        rows = [self.observation("2", "SAME", "Japanese Stainless Steel Jigger", 9999),
                self.observation("1", "SAME", "Japanese Stainless Steel Jigger", 300)]
        result = merge_product_observations(rows, self.rules, "run1")
        self.assertEqual(result, merge_product_observations(list(reversed(rows)), self.rules, "run1"))
        self.assertEqual(result[0]["form_total_sales"], 300)
        self.assertEqual(result[0]["family_conflict_count"], 1)

    def test_unresolved_source_label_is_not_barware_identity(self):
        rows = [self.observation("2", "UNKNOWN", "Model 100")]
        row = merge_product_observations(rows, self.rules, "run1")[0]
        self.assertEqual(row["market_id"], "unresolved")
        self.assertEqual(row["shape_recommendation"], "Needs classification")
        self.assertIsNone(normalize_shape_validation([row])[0]["shape_score"])

    def test_unknown_specs_remain_pending_not_zero_score(self):
        row = merge_product_observations([self.observation("2", "JIGGER", "Japanese Jigger")], self.rules, "run1")[0]
        self.assertEqual(row["classification_status"], "needs_specification")
        self.assertEqual(row["market_id"], "barware")
        self.assertEqual(row["shape_recommendation"], "Needs classification")
        self.assertIsNone(normalize_shape_validation([row])[0]["shape_score"])

    def test_unrelated_product_not_barware_because_of_source(self):
        _, product = self.observation("2", "MAT", "Magnetic Tool Mat")
        self.assertEqual(market_identity(product)[0], "tool_mat")

    def test_no_historical_snapshot_mix(self):
        row = self.observation("1", "JIGGER", "Japanese Stainless Steel Jigger")
        with self.assertRaises(ValueError):
            merge_product_observations([row], self.rules, "run2")

    def test_hard_risk_from_any_source_survives_dedupe(self):
        rows = [self.observation("1", "JIGGER", "Japanese Stainless Steel Jigger"),
                self.observation("2", "JIGGER", "Glass Japanese Jigger")]
        row = merge_product_observations(rows, self.rules, "run1")[0]
        self.assertEqual(row["form_count"], 0)
        self.assertEqual(row["shape_recommendation"], "Reject category/form")


if __name__ == "__main__":
    unittest.main()
