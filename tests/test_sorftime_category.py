import json
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discover_sorftime_opportunities as discover  # noqa: E402
import mcp_product_opportunity_research as research  # noqa: E402
from category_shape_validation import extract_category_report_products, normalized_category_product  # noqa: E402
from sorftime_category import (  # noqa: E402
    build_category_report,
    normalize_category_stats,
    report_has_products,
    report_has_stats,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sorftime_2026_10"
PRODUCTS = json.loads((FIXTURES / "category_products_10140455011.json").read_text())
STATS = json.loads((FIXTURES / "category_report_10253330011.json").read_text())


class FakeClient:
    def __init__(self):
        self.calls = []
        self.tool_call_count = 0

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        self.tool_call_count += 1
        return {"category_products": PRODUCTS, "category_report": STATS}[name]


class CategoryProductsAdapterTest(unittest.TestCase):
    def test_new_fields_map_to_pipeline_names(self):
        report = build_category_report("10140455011", PRODUCTS, today=date(2026, 10, 18))
        first = report["data"]["top100_products"][0]
        self.assertEqual(first["asin"], "B003P2UOCO")
        self.assertEqual(first["review_count"], 55320)
        self.assertEqual(first["star_rating"], 4.5)
        self.assertEqual(first["delivery_type"], "FBA")
        self.assertEqual(first["category_rank"], "Category: Craft Cabinets, Rank: 1")
        self.assertTrue(first["main_image"].startswith("https://"))
        self.assertEqual(first["seller"], "Amazon")
        self.assertGreater(first["days_listed"], 1000)
        self.assertEqual(first["product_category"], "")

    def test_no_data_marker_becomes_empty(self):
        row = build_category_report("10140455011", PRODUCTS)["data"]["top100_products"][5]
        self.assertIsNone(row["monthly_sales_volume"])
        self.assertIsNone(row["price"])

    def test_existing_readers_find_the_top100(self):
        report = build_category_report("10140455011", PRODUCTS)
        self.assertEqual(len(extract_category_report_products(report)), 6)
        self.assertEqual(len(discover.extract_category_report_products(report)), 6)
        normalized = normalized_category_product(report["data"]["top100_products"][0], "Tools > Craft Cabinets")
        self.assertEqual(normalized["reviews"], 55320)
        self.assertEqual(normalized["rating"], 4.5)
        self.assertTrue(normalized["is_fba"])

    def test_statistics_only_report_is_not_a_top100(self):
        self.assertFalse(report_has_products(STATS))
        self.assertEqual(discover.extract_category_report_products(STATS), [])

    def test_statistics_keep_old_keys(self):
        stats = normalize_category_stats(STATS)
        self.assertEqual(stats["top3_brands_sales_volume_share"], STATS["data"]["top3_brand_sales_volume_share"])
        self.assertEqual(stats["first_brand"], STATS["data"]["brands_list"][0][0])
        self.assertEqual(stats["average_price"], STATS["data"]["category_products_price"][0])
        self.assertEqual(stats["top100_monthly_sales_volume"], STATS["data"]["top100_monthly_sales_volume"])


class WeeklyDiscoveryCallTest(unittest.TestCase):
    def test_weekly_scan_uses_one_category_products_call(self):
        client = FakeClient()
        repo = Path(__file__).resolve().parents[1]
        rules = {**json.loads((repo / "config/autodiscovery_rules.json").read_text()), "marketplace": "US"}
        defaults = json.loads((repo / "config/import_defaults.json").read_text())
        category = {"category_id": "10140455011", "name": "Craft Cabinets", "path": "Tools > Craft Cabinets"}
        with TemporaryDirectory() as temp_dir:
            _, examined = discover.collect_products_for_category_mcp(category, rules, defaults, client, temp_dir)
            saved = json.loads((Path(temp_dir) / "10140455011.json").read_text())
        self.assertEqual([name for name, _ in client.calls], ["category_products"])
        self.assertEqual(client.calls[0][1], {"node_id": "10140455011", "amz_site": "US"})
        self.assertEqual(examined, 6)
        self.assertTrue(report_has_products(saved))
        self.assertIn("category_health_score", category)


class ResearchCategoryReportTest(unittest.TestCase):
    def test_cached_weekly_top100_only_fetches_statistics(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "archive/discovery_runs/20261019_083000/raw_category_reports"
            raw.mkdir(parents=True)
            (raw / "10140455011.json").write_text(json.dumps(build_category_report("10140455011", PRODUCTS)))
            client = FakeClient()
            with patch.object(research, "ROOT", root):
                report, path = research.category_report(client, "10140455011", "US")
        self.assertEqual([name for name, _ in client.calls], ["category_report"])
        self.assertTrue(report_has_products(report))
        self.assertTrue(report_has_stats(report))
        self.assertEqual(path.name, "10140455011.json")

    def test_missing_cache_fetches_top100_and_statistics(self):
        with TemporaryDirectory() as temp_dir:
            client = FakeClient()
            with patch.object(research, "ROOT", Path(temp_dir)):
                report, _ = research.category_report(client, "10140455011", "US")
        self.assertEqual([name for name, _ in client.calls], ["category_products", "category_report"])
        self.assertEqual(len(report["data"]["top100_products"]), 6)


if __name__ == "__main__":
    unittest.main()
