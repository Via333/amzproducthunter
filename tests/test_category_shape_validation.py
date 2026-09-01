import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from category_shape_validation import (
    build_validation_rows,
    load_json,
    resolve_discovery_run_dir,
    select_seed_rows,
)


class CategoryShapeValidationTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.runs_root = self.root / "discovery_runs"
        self.run_dir = self.runs_root / "20260901_test"
        raw_dir = self.run_dir / "raw_category_reports"
        raw_dir.mkdir(parents=True)
        (self.run_dir / "run_manifest.json").write_text(
            json.dumps({"status": "success", "finished_at": "2026-09-01T21:42:46"}),
            encoding="utf-8",
        )
        products = [
            self.product("B0SEED0001", "Magnetic Tool Mat for Workbench", "HOME ORGANIZERS AND STORAGE", 600, 50, "Brand A"),
            self.product("B0MAT000002", "Large Magnetic Tool Mat", "HOME ORGANIZERS AND STORAGE", 500, 70, "Brand B"),
            self.product("B0HOLSTER01", "Universal Drill Tool Holster", "UTILITY HOLSTER POUCH", 350, 1000, "Brand C"),
            self.product("B0HOLSTER02", "Quick Draw Tool Holster", "UTILITY HOLSTER POUCH", 250, 1200, "Brand D"),
        ]
        (raw_dir / "553582.json").write_text(
            json.dumps({"data": {"top100_products": products}}),
            encoding="utf-8",
        )
        self.rules = {
            "discovery_runs_root": str(self.runs_root),
            "seed_recommendation_contains": "Watch",
            "seed_limit": 12,
            "allow_deep_dive_fallback": False,
            "research_root": str(self.root / "research"),
            "thresholds": load_json("config/category_shape_validation_rules.json")["thresholds"],
        }

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def product(asin, title, product_category, sales, reviews, brand):
        return {
            "asin": asin,
            "title": title,
            "product_category": product_category,
            "monthly_sales_volume": str(sales),
            "review_count": reviews,
            "star_rating": 4.5,
            "price": 24.99,
            "brand": brand,
            "delivery_type": "FBA",
            "seller_origin": "中国",
            "online_date": "2026-03-01",
        }

    def test_current_scan_top100_is_used_and_seed_rank_is_preserved(self):
        ranked = [
            {
                "source_asin": "B0SEED0001",
                "source_category_id": "553582",
                "source_category_name": "Tool Holsters",
                "source_category_path": "Tools > Tool Holsters",
                "product_name": "Magnetic Tool Mat for Workbench",
                "opportunity_score": "59.4",
                "recommendation": "Watch or collect more data",
            }
        ]
        seeds = select_seed_rows(ranked, self.rules)
        rows = build_validation_rows(seeds, [], self.rules, self.run_dir)

        primary = next(row for row in rows if row["shape_scope"] == "seed_form")
        self.assertEqual(primary["seed_rank"], 1)
        self.assertEqual(primary["seed_title"], "Magnetic Tool Mat for Workbench")
        self.assertEqual(primary["source_category_id"], "553582")
        self.assertEqual(primary["data_quality"], "category_top100")
        self.assertEqual(primary["validation_run_id"], "20260901_test")
        self.assertEqual(primary["product_form"], "magnetic tool mat")
        self.assertEqual(primary["form_count"], 2)
        self.assertNotEqual(primary["shape_recommendation"], "Needs category Top100")
        self.assertTrue(any(row["product_form"] == "tool holster" for row in rows))

    def test_latest_successful_raw_run_is_resolved(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AMZ_WEEKLY_RUN_ID", None)
            resolved = resolve_discovery_run_dir(self.rules)
        self.assertEqual(resolved, self.run_dir)


if __name__ == "__main__":
    unittest.main()
