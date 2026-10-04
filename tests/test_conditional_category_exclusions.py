#!/usr/bin/env python3
"""Broad category access must not bypass product-level safety gates."""

import json
import unittest
from pathlib import Path

from build_dashboard import normalize_exclusion_rules
from category_shape_validation import is_brand_dependent, product_exclusion_reason
from discover_sorftime_opportunities import category_exclusion_reason, product_passes_filters, select_categories
from import_sorftime_candidates import build_candidate
from product_exclusions import hard_exclusion_reason
from product_risk import infer_oversize_risk
from product_selection import hard_exclusion_reason as scoring_exclusion_reason
from product_selection import hard_stop_reason, normalize_row


ROOT = Path(__file__).resolve().parents[1]


def config(name):
    return json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))


CLEANING = "Home & Kitchen > Vacuums & Floor Care"
INDUSTRIAL = "Industrial & Scientific > Hardware > Cable Ties"
AUTO = "Automotive > Automotive Replacement Parts > Body & Trim > Fasteners"
HUNTING = "Sports & Outdoors > Hunting & Shooting > Hunting > Storage Bags"

ALLOWED = [
    ("Universal Vacuum Cleaner HEPA Filters", CLEANING + " > Vacuum Parts & Accessories > Filters"),
    ("Universal filters for vacuum cleaners", CLEANING + " > Filters"),
    ("Reusable Steam Mop Pads", CLEANING + " > Steam Cleaner Accessories"),
    ("Cleaning Pads for Steam Cleaners", CLEANING + " > Steam Cleaner Accessories"),
    ("Vacuum Cleaner Holder Wall Rack", CLEANING + " > Vacuum Accessories"),
    ("Cordless Vacuum Attachment", CLEANING + " > Vacuum Attachments"),
    ("Portable Vacuum Accessory", CLEANING + " > Vacuum Accessories"),
    ("Vacuum Brush Roll", CLEANING + " > Vacuum Accessories"),
    ("Universal Vacuum Storage Bags", "Home & Kitchen > Storage Bags"),
    ("Compact Manual Floor Squeegee", CLEANING + " > Squeegees"),
    ("Compact Manual Floor Scrubber", CLEANING + " > Brushes"),
    ("Industrial Nylon Cable Ties", INDUSTRIAL),
    ("Electric Drill Holster", "Tools & Home Improvement > Tool Holsters"),
    ("Universal Car Trim Fastener Clips", AUTO),
    ("Universal Auto Cable Organizer", AUTO),
    ("Hunting Utility Pouch", HUNTING),
    ("Burgundy Hunting Organizer Pouch", HUNTING),
    ("Shooting Outdoor Storage Straps", HUNTING),
]

BLOCKED = [
    ("Portable Steam Cleaner", CLEANING + " > Steam Cleaners"),
    ("Steam Mop with Replacement Pads", CLEANING + " > Steam Mops"),
    ("Handheld Vacuum Cleaner with HEPA Filters", CLEANING + " > Handheld Vacuums"),
    ("Vacuum Filter Kit and Handheld Vacuum Cleaner", CLEANING + " > Handheld Vacuums"),
    ("Vacuum Cleaner Replacement Motor", CLEANING + " > Motors"),
    ("Model X200", CLEANING + " > Upright Vacuums"),
    ("Portable Steamer", CLEANING),
    ("Industrial Electric Motor", INDUSTRIAL),
    ("Industrial Air Compressor", INDUSTRIAL),
    ("Hunting Rifle Sling", HUNTING),
    ("Pistol Storage Pouch", HUNTING),
    ("Hunting Crossbow Kit", HUNTING),
    ("Hunting Broadheads", HUNTING),
    ("Heavy Duty Case", "Sports > Hunting & Shooting > Gun Cases"),
    ("Car Brake Pads", AUTO),
    ("Replacement Steering Rack", AUTO),
    ("Automotive Airbag Module", AUTO),
    ("Universal Spark Plugs", AUTO),
    ("OEM Trim Clip Set", AUTO),
    ("Trim Clips Fits 2018 Sedan", AUTO),
    ("Industrial Liquid", "Industrial & Scientific > Hazmat"),
    ("Universal Power Cell", "Industrial & Scientific > Batteries"),
    ("Cleaning Solution", "Industrial & Scientific > Chemicals"),
    ("Reusable Food Storage Bag", "Home & Kitchen > Kitchen Storage"),
]


class ConditionalCategoryTest(unittest.TestCase):
    def setUp(self):
        self.discovery = config("autodiscovery_rules.json")
        self.scoring = config("scoring_rules.json")
        self.defaults = config("import_defaults.json")
        self.exclusions = config("category_exclusions.json")

    def test_wide_categories_are_no_longer_skipped(self):
        for path in {path for _, path in ALLOWED}:
            with self.subTest(path=path):
                category = {"category_id": "test", "path": path}
                self.assertEqual(category_exclusion_reason(category, self.discovery["category_filters"], self.exclusions), "")

    def test_essential_category_exclusions_remain(self):
        for term in ["Grocery", "Supplement", "Hazmat", "Battery", "Chemical", "Medical", "Furniture"]:
            with self.subTest(term=term):
                category = {"category_id": term, "path": "Store > " + term}
                self.assertTrue(category_exclusion_reason(category, self.discovery["category_filters"], self.exclusions))

    def test_specific_high_risk_subtrees_are_skipped_without_blocking_parent_markets(self):
        blocked = [
            "Sports > Hunting > Gun Accessories, Maintenance & Storage > Gun Safes",
            "Sports > Shooting > Air Guns & Accessories > Rails",
            "Sports > Hunting > Hunting Knives > Fixed Blade Hunting Knives",
            "Home & Kitchen > Food Storage > Vacuum Sealer Bags",
            "Home & Kitchen > Bakeware > Parchment",
            "Tools & Home Improvement > Kitchen Sinks > Single Bowl",
        ]
        for path in blocked:
            with self.subTest(path=path):
                self.assertTrue(
                    category_exclusion_reason(
                        {"category_id": path, "path": path},
                        self.discovery["category_filters"],
                        self.exclusions,
                    )
                )
        for path in [HUNTING, "Automotive > Interior Accessories", INDUSTRIAL]:
            with self.subTest(path=path):
                self.assertEqual(
                    category_exclusion_reason(
                        {"category_id": path, "path": path},
                        self.discovery["category_filters"],
                        self.exclusions,
                    ),
                    "",
                )

    def test_reopened_categories_rejoin_never_scanned_queue(self):
        tree = {"Data": [{"NodeId": "industrial", "name": "Industrial & Scientific", "children": [
            {"NodeId": "ties", "name": "Cable Ties", "children": []},
        ]}]}
        selected = select_categories(tree, self.discovery, {"ties": {"status": "excluded", "scan_count": "0"}}, self.exclusions)
        self.assertEqual([row["category_id"] for row in selected], ["ties"])
        self.assertEqual(selected[0]["rotation_bucket"], "never_scanned")

    def test_accessories_pass_all_product_gates(self):
        for title, category in ALLOWED:
            with self.subTest(title=title):
                row = {"product_name": title, "category": category, "brand": "OpenBrand"}
                self.assertEqual(hard_exclusion_reason(row), "")
                self.assertEqual(scoring_exclusion_reason(row, self.scoring), "")
                self.assertLess(infer_oversize_risk(title, category), 65)
                product = {"title": title, "bsr_category": category, "brand": "OpenBrand"}
                self.assertEqual(product_exclusion_reason(product, self.scoring), "")
                candidate = build_candidate(
                    {"asin": "ACCESSORY1", "title": title, "price": 25, "sales": 500, "reviews": 50, "rating": 4.2},
                    self.defaults, category,
                )
                self.assertTrue(product_passes_filters(candidate, self.discovery["product_filters"]))
                self.assertEqual(hard_stop_reason(normalize_row(candidate, self.scoring), self.scoring), "")

    def test_high_risk_products_still_rejected(self):
        for title, category in BLOCKED:
            with self.subTest(title=title):
                row = {"product_name": title, "category": category, "brand": "OpenBrand"}
                self.assertTrue(hard_exclusion_reason(row))
                self.assertTrue(scoring_exclusion_reason(row, self.scoring))
                self.assertTrue(product_exclusion_reason({"title": title, "bsr_category": category}, self.scoring))
                candidate = build_candidate(
                    {"asin": "BLOCKED1", "title": title, "price": 25, "sales": 500, "reviews": 50, "rating": 4.2},
                    self.defaults, category,
                )
                self.assertFalse(product_passes_filters(candidate, self.discovery["product_filters"]))

    def test_brand_specific_fragile_and_oversize_accessories_still_rejected(self):
        self.assertTrue(is_brand_dependent({"title": "Filters Compatible with Dyson", "brand": "OpenBrand"}, {"dyson"}))
        self.assertTrue(product_exclusion_reason({"title": "Glass Vacuum Cleaner Holder", "bsr_category": CLEANING}, self.scoring))
        self.assertTrue(product_exclusion_reason({"title": "Outdoor Storage Box 80 Quart", "bsr_category": HUNTING}, self.scoring))

    def test_automotive_airbag_rule_does_not_reject_shipping_air_bags(self):
        self.assertEqual(hard_exclusion_reason({"product_name": "Packing Air Bags", "category": "Office > Shipping Supplies"}), "")

    def test_dashboard_has_real_reasons_and_no_removed_terms(self):
        rows = normalize_exclusion_rules(self.discovery, self.exclusions)
        terms = {row["term"] for row in rows}
        for removed in ["industrial", "floor care", "automotive replacement", "hunting", "shooting", "steam cleaner", "vacuum"]:
            self.assertNotIn(removed, terms)
        self.assertTrue(all(row["reason"] != "不进入个人卖家类目扫描队列" for row in rows))
        self.assertIn("危险品", next(row["reason"] for row in rows if row["term"] == "hazmat"))


if __name__ == "__main__":
    unittest.main()
