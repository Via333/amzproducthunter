import csv
import json
import tempfile
import unittest
from pathlib import Path

from research_landscape import (
    analyze_landscape, build_landscape, merge_landscape_product, normalize_landscape_product,
)


class ResearchLandscapeMetricsTest(unittest.TestCase):
    def product(self, **fields):
        source = fields.pop("source", {"path": "search.json", "retrieved_on": "2026-09-05"})
        return normalize_landscape_product({
            "asin": "B000000001", "title": "Stainless Steel Japanese Cocktail Jigger",
            "price": 11.95, "online_date": "2011-04-10", **fields,
        }, "Home > Jiggers", source)

    def test_category_keyword_and_csv_sales_aliases(self):
        for field in ("monthly_sales_volume", "monthly_sale_volume", "monthly_sales"):
            with self.subTest(field=field):
                p = self.product(**{field: "10,595", "monthly_sales_amount": 126610.25})
                result = analyze_landscape([p], p["asin"])
                product = result["forms"][0]["products"][0]
                self.assertEqual(product["monthly_sales"], 10595)
                self.assertEqual(product["price"], 11.95)
                self.assertEqual(product["online_date"], "2011-04-10")
                self.assertEqual(product["metric_sources"]["monthly_sales"]["retrieved_on"], "2026-09-05")

    def test_missing_invalid_sales_never_derived_from_revenue(self):
        for value in (None, "", "-", "N/A", "NaN", "inf", -1):
            with self.subTest(value=value):
                p = self.product(monthly_sale_volume=value, monthly_sales_amount=1000)
                result = analyze_landscape([p], p["asin"])
                self.assertIsNone(result["forms"][0]["products"][0]["monthly_sales"])
                self.assertNotIn("monthly_sales", p["metric_sources"])
        self.assertEqual(self.product(monthly_sales_volume=None, monthly_sale_volume=0)["monthly_sales"], 0)

    def test_missing_fields_do_not_overwrite_valid_snapshot(self):
        old = self.product(monthly_sale_volume=100)
        incoming = self.product(monthly_sales_volume=None, price="", online_date="")
        merged = merge_landscape_product(old, incoming)
        for field in ("monthly_sales", "price", "online_date", "metric_sources"):
            self.assertEqual(merged[field], old[field])

    def test_newer_reported_zero_is_kept_but_older_snapshot_is_not(self):
        old = self.product(monthly_sale_volume=100)
        incoming = self.product(monthly_sale_volume=0, source={"path": "new.json", "retrieved_on": "2026-09-06"})
        merged = merge_landscape_product(old, incoming)
        self.assertEqual(merged["monthly_sales"], 0)
        self.assertEqual(merged["metric_sources"]["monthly_sales"]["path"], "new.json")
        self.assertEqual(merge_landscape_product(merged, old)["monthly_sales"], 0)

    def test_build_preserves_raw_metrics_over_csv_defaults_and_ignores_detail_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            study = root / "research/B000000001"
            study.mkdir(parents=True)
            (study / "research_sources.json").write_text(json.dumps({"site": "US", "retrieved_on": "2026-09-05"}))
            (study / "search_stop.json").write_text(json.dumps({
                "tool": "product_search_from_name", "arguments": {"amz_site": "US", "name": "jigger"},
                "retrieved_on": "2026-09-05", "result": {"products": [{
                    "asin": study.name, "title": "Japanese Stainless Steel Cocktail Jigger",
                    "monthly_sale_volume": 10595, "price": 11.95, "online_date": "2011-04-10",
                }]},
            }))
            with (study / "top_products.csv").open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["asin", "title", "monthly_sales", "price", "listing_date"])
                writer.writeheader()
                writer.writerow({"asin": study.name, "title": "Japanese Stainless Steel Cocktail Jigger", "monthly_sales": 0})
            cache = root / ".local/sorftime_image_details/US"
            cache.mkdir(parents=True)
            (cache / f"{study.name}.json").write_text(json.dumps({"monthly_sales_volume": 99999, "price": 99}))
            result = build_landscape(root, study)
            self.assertEqual(result["related_product_count"], 1)
            form = result["forms"][0]
            self.assertEqual(form["monthly_sales"], 10595)
            self.assertEqual(form["median_price"], 11.95)
            p = form["products"][0]
            self.assertEqual(p["online_date"], "2011-04-10")
            self.assertEqual(p["metric_sources"]["monthly_sales"]["path"], "research/B000000001/search_stop.json")


if __name__ == "__main__":
    unittest.main()
