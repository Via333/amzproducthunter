import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from category_health import category_health_from_records
from category_shape_validation import build_rows_from_category_report, evaluate_shape_row
from opportunity_feedback import load_feedback
from seasonality import holiday_theme

ROOT = Path(__file__).resolve().parents[1]


class HolidayThemeTest(unittest.TestCase):
    def test_holiday_in_title_subject_or_before_decor_noun(self):
        self.assertEqual(holiday_theme("Halloween Decorations Outdoor, 6pcs Tombstones"), "halloween")
        self.assertEqual(holiday_theme("24 Extra Large Tombstones for Graveyard Halloween Decorations Outdoor"), "halloween")
        self.assertEqual(holiday_theme("Outdoor String Lights for Bedroom, 200 LED Christmas Lights"), "christmas")
        self.assertEqual(holiday_theme("Chunful 4 Pcs Fall Decorative Garden Stakes Pumpkin Yard Stakes"), "fall_harvest")

    def test_gift_positioning_and_passing_use_are_not_holiday_products(self):
        self.assertEqual(holiday_theme("Solar Wind Chimes, Christmas Gardening Hummingbird Gifts for Women"), "")
        self.assertEqual(holiday_theme("Christmas Gift Mug, Funny Coffee Cup for Christmas"), "")
        self.assertEqual(holiday_theme("Solar Torch Lights Outdoor with Flickering Flame for Halloween"), "")
        self.assertEqual(holiday_theme("Santa Maria Grill, Portable Argentinian Parrilla"), "")
        self.assertEqual(holiday_theme("Fall Protection Safety Harness, Full Body"), "")

    def test_all_season_bundles_are_not_one_holiday(self):
        self.assertEqual(holiday_theme("8 Set Porch Goose Outfits for Valentine St. Patrick Easter 4th of July "
                                       "Halloween Christmas"), "")
        # Three autumn and winter holidays are still one selling season.
        self.assertEqual(holiday_theme("3Pcs Thanksgiving Halloween Christmas Mailbox Covers Magnetic"), "halloween")


class SeasonalShapeTest(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads((ROOT / "config/category_shape_validation_rules.json").read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rules["_feedback_root"] = Path(self.temp.name)
        load_feedback.cache_clear()
        patcher = patch.dict(os.environ, {"AMZ_AS_OF_DATE": "2026-10-05"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def products(self, seasonal, count=8):
        return [{"asin": f"B0TEST{i:04d}", "brand": f"Company {i}", "price": 25,
                 "title": "Halloween Silicone Sink Caddy Decoration" if i < seasonal else "Silicone Sink Caddy",
                 "monthly_sales_volume": 800, "review_count": 30, "online_date": "2026-09-01", "fba_fee": 4}
                for i in range(count)]

    def row(self, seasonal):
        return build_rows_from_category_report({"source_category_path": "Home > Sink Caddies"},
                                               {"data": {"top100_products": self.products(seasonal)}}, self.rules)[0]

    def test_mostly_holiday_form_is_rejected_whatever_its_score(self):
        row = self.row(seasonal=6)
        self.assertEqual(row["form_seasonal_sales_share"], 0.75)
        self.assertEqual(row["form_seasonal_holidays"], "halloween:6")
        self.assertEqual(row["shape_recommendation"], "Reject category/form")
        self.assertEqual(row["decisive_reject_reason"], "seasonal holiday products")
        self.assertIn("万圣节主题商品占销量 75%", row["opportunity_thesis"])

    def test_partly_holiday_form_is_capped_at_watch(self):
        clean = self.row(seasonal=0)
        self.assertEqual(clean["shape_recommendation"], "Shape opportunity")
        row = self.row(seasonal=3)
        self.assertIn("partly seasonal holiday products", row["validation_flags"])
        self.assertEqual(row["shape_recommendation"], "Watch shape")
        self.assertEqual(row["shape_score"], clean["shape_score"])

    def test_seasonal_reject_overrides_unconfirmed_classification(self):
        row = self.row(seasonal=0)
        row.update(classification_status="category_proxy", form_seasonal_sales_share=0.9, validation_flags="")
        result = evaluate_shape_row(row, self.rules)
        self.assertEqual(result["shape_recommendation"], "Reject category/form")
        self.assertEqual(result["decisive_reject_reason"], "seasonal holiday products")
        self.assertNotIn("类目代理统计", result["opportunity_thesis"])


class SeasonalCategoryHealthTest(unittest.TestCase):
    def records(self, seasonal):
        return [{"asin": f"B0TEST{i:04d}", "brand": f"Company {i}", "price": 25,
                 "title": "Halloween Yard Sign Decoration" if i < seasonal else "Metal Yard Sign",
                 "monthly_sales_volume": 500, "review_count": 50, "online_date": "2026-06-01"}
                for i in range(20)]

    def test_holiday_sales_discount_health_and_flag_category(self):
        with patch.dict(os.environ, {"AMZ_AS_OF_DATE": "2026-10-05"}):
            clean = category_health_from_records(self.records(0), "Patio > Yard Signs")
            seasonal = category_health_from_records(self.records(15), "Patio > Yard Signs")
        self.assertEqual(clean["category_seasonal_sales_share"], 0)
        self.assertNotIn("seasonal holiday products", clean["category_health_flags"])
        self.assertEqual(seasonal["category_seasonal_sales_share"], 0.75)
        self.assertIn("seasonal holiday products", seasonal["category_health_flags"])
        self.assertAlmostEqual(seasonal["category_health_score"], round(clean["category_health_score"] * 0.25, 1), delta=0.1)


if __name__ == "__main__":
    unittest.main()
