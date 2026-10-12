import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from category_shape_validation import build_rows_from_category_report
from opportunity_feedback import load_feedback
from product_taxonomy import is_broad_product_type

ROOT = Path(__file__).resolve().parents[1]


class ProductTypeConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads((ROOT / "config/category_shape_validation_rules.json").read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rules["_feedback_root"] = Path(self.temp.name)
        load_feedback.cache_clear()
        patcher = patch.dict(os.environ, {"AMZ_AS_OF_DATE": "2026-10-05"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def row(self, types, title="Model A100"):
        products = [{"asin": f"B0TEST{i:04d}", "title": title, "brand": f"Company {i}", "price": 25,
                     "monthly_sales_volume": 800, "review_count": 30, "online_date": "2026-09-01", "fba_fee": 4,
                     "product_category": product_type} for i, product_type in enumerate(types)]
        return build_rows_from_category_report({"source_category_path": "Home > Sink Caddies"},
                                               {"data": {"top100_products": products}}, self.rules)[0]

    def test_one_specific_amazon_type_confirms_leaf_proxy(self):
        row = self.row(["SINK CADDY"] * 8)
        self.assertEqual(row["classification_status"], "product_type_confirmed")
        self.assertEqual(row["form_dominant_product_type"], "sink caddy")
        self.assertEqual(row["form_dominant_product_type_share"], 1.0)
        self.assertIn("规格待核", row["product_form_label"])
        self.assertNotIn("category proxy requires classification", row["validation_flags"])
        self.assertEqual(row["shape_recommendation"], "Shape opportunity")
        self.assertIn("亚马逊商品类型 sink caddy 确认", row["opportunity_thesis"])
        self.assertTrue(row["next_action"].startswith("先抽查参考 ASIN 的主图和规格"))

    def test_department_or_placeholder_type_keeps_proxy(self):
        for product_type in ("OUTDOOR_LIVING", "ABIS_MUSIC", "HOME"):
            row = self.row([product_type] * 8)
            self.assertEqual(row["classification_status"], "category_proxy")
            self.assertEqual(row["shape_recommendation"], "Watch shape")
        self.assertTrue(is_broad_product_type(""))
        self.assertFalse(is_broad_product_type("WIND_CHIME"))

    def test_mixed_or_thin_types_keep_proxy(self):
        # One best seller of a type is not evidence that the group shares it.
        self.assertEqual(self.row(["SINK CADDY"] * 6 + ["STORAGE RACK"] * 2)["classification_status"], "category_proxy")
        self.assertEqual(self.row(["SINK CADDY"] * 6 + [""] * 2)["classification_status"], "category_proxy")
        self.assertEqual(self.row(["SINK CADDY"] * 4)["classification_status"], "category_proxy")

    def test_title_identity_is_not_replaced(self):
        row = self.row(["STORAGE RACK"] * 8, title="Silicone Sink Caddy")
        self.assertEqual(row["classification_status"], "title_rule")
        self.assertEqual(row["product_form"], "sink caddy")


if __name__ == "__main__":
    unittest.main()
