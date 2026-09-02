#!/usr/bin/env python3
"""Offline tests for market evidence, taxonomy, demand, and business feasibility."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from business_feasibility import build_business_feasibility
from auto_research_shortlist import select_shortlist
from demand_analysis import analyze_demand
from discover_sorftime_opportunities import load_category_tree, product_passes_filters
from import_sorftime_candidates import build_candidate
from market_structure import analyze_market
from product_taxonomy import classify_product_form
from product_exclusions import hard_exclusion_reason
from product_risk import has_valid_category, infer_compliance_risk, infer_fragile_risk, infer_oversize_risk
from product_selection import calculate_scores, normalize_row, write_csv as write_selection_csv
from weekly_scan_observability import collect_metrics


ROOT = Path(__file__).resolve().parents[1]


class EvidenceTest(unittest.TestCase):
    def test_estimated_profit_does_not_reject_market_candidate(self) -> None:
        defaults = json.loads((ROOT / "config" / "import_defaults.json").read_text(encoding="utf-8"))
        rules = json.loads((ROOT / "config" / "scoring_rules.json").read_text(encoding="utf-8"))
        candidate = build_candidate(
            {
                "asin": "MARKET1",
                "title": "Compact Wall Mounted Cable Organizer",
                "price": 29.99,
                "sales": 450,
                "reviews": 35,
                "rating": 4.2,
                "FbaFee": 5.5,
            },
            defaults,
            "Home & Kitchen > Cable Organizers",
        )
        scored = calculate_scores(normalize_row(candidate, rules), rules)
        self.assertEqual(scored["recommendation"], "Watch or collect more data")
        self.assertIn("supplier quote required", scored["key_flags"])

    def test_verified_low_profit_still_rejects_candidate(self) -> None:
        defaults = json.loads((ROOT / "config" / "import_defaults.json").read_text(encoding="utf-8"))
        rules = json.loads((ROOT / "config" / "scoring_rules.json").read_text(encoding="utf-8"))
        candidate = build_candidate(
            {
                "asin": "MARKET2",
                "title": "Compact Wall Mounted Cable Organizer",
                "price": 29.99,
                "sales": 450,
                "reviews": 35,
                "rating": 4.2,
                "FbaFee": 5.5,
            },
            defaults,
            "Home & Kitchen > Cable Organizers",
        )
        candidate["cost"] = 18
        candidate["shipping"] = 4
        candidate["profit_estimate_status"] = "supplier_quote_verified"
        scored = calculate_scores(normalize_row(candidate, rules), rules)
        self.assertEqual(scored["recommendation"], "Reject")
        self.assertNotIn("estimated profit is not a rejection gate", scored["key_flags"])

    def test_candidate_marks_estimated_and_observed_fields(self) -> None:
        defaults = json.loads((ROOT / "config" / "import_defaults.json").read_text(encoding="utf-8"))
        candidate = build_candidate(
            {"asin": "TST1", "title": "Cable Organizer", "price": 24.99, "sales": 300, "reviews": 42, "rating": 4.3},
            defaults,
        )
        self.assertEqual(candidate["profit_estimate_status"], "estimated_default_costs")
        self.assertIn("observed:price,sales,reviews,rating", candidate["data_source_summary"])
        self.assertGreater(candidate["evidence_confidence"], 30)

    def test_mcp_category_report_fields_map_to_candidate(self) -> None:
        defaults = json.loads((ROOT / "config" / "import_defaults.json").read_text(encoding="utf-8"))
        candidate = build_candidate(
            {
                "asin": "MCP1",
                "title": "Compact Drawer Organizer",
                "price": 26.99,
                "monthly_sales_volume": 640,
                "review_count": 83,
                "star_rating": 4.2,
                "product_category": "Drawer Organizers",
                "fba_fee": 5.25,
            },
            defaults,
        )
        self.assertEqual(candidate["est_monthly_sales"], 640)
        self.assertEqual(candidate["avg_rating"], 4.2)
        self.assertEqual(candidate["category"], "Drawer Organizers")
        self.assertIn("observed:price,sales,reviews,rating,fba_fee", candidate["data_source_summary"])

    def test_fragile_material_is_a_hard_risk_signal(self) -> None:
        defaults = json.loads((ROOT / "config" / "import_defaults.json").read_text(encoding="utf-8"))
        candidate = build_candidate(
            {
                "asin": "GLASS1",
                "title": "Hand Painted Glass Sweets Jar with Lid",
                "category": "Cookie Jars",
                "price": 69.95,
                "sales": 278,
                "reviews": 151,
                "rating": 4.5,
            },
            defaults,
        )
        self.assertGreaterEqual(candidate["fragile_risk"], 80)
        self.assertGreaterEqual(infer_fragile_risk("Ceramic kitchen canister"), 80)
        self.assertEqual(infer_fragile_risk("Fiberglass repair tape"), 20)

    def test_personal_seller_hard_risk_signals(self) -> None:
        self.assertGreaterEqual(infer_oversize_risk("2 Count 160 Quart Wheeled Extra Large Storage Bin"), 80)
        self.assertGreaterEqual(infer_oversize_risk("Clear Stackable Storage Container 21 Qt"), 65)
        self.assertGreaterEqual(infer_oversize_risk("65L Large Woven Cotton Rope Blanket Basket"), 80)
        self.assertGreaterEqual(infer_oversize_risk("6 Pack Large Closet Storage Baskets for Shelves"), 80)
        self.assertGreaterEqual(infer_oversize_risk("StorageWorks Storage Bins with Lids, Fabric Closet Storage Bins, Large"), 80)
        self.assertGreaterEqual(infer_oversize_risk("Storage Box Organizer, Pack of 4"), 80)
        self.assertGreaterEqual(infer_oversize_risk("Storage Box 28 Inches Long"), 65)
        self.assertGreaterEqual(infer_oversize_risk("Large Capacity Garage Ball Storage Rack with Wheels"), 65)
        self.assertGreaterEqual(infer_compliance_risk("Reusable Beeswax Bread Bags for Sourdough"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Glass Sweets Jar", "Cookie Jars"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Camping Stove with Gas Regulator"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Quick Draw Belly Band Holster for Concealed Carry"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Stainless Steel Camping Cookware Set"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Raised Toilet Seat with Handles"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Edible Wafer Paper for Cake Decoration"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Liquor Bottle Pourer Dispenser"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Collapsible Bowls", "Camping Dishes & Utensils"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Mini Hand Garlic Grinder"), 80)
        self.assertGreaterEqual(infer_oversize_risk("Broom and Dustpan Set with 53“ Long Handle"), 65)
        self.assertGreaterEqual(infer_oversize_risk("4 Gallon Ice Bucket with Handles"), 80)
        self.assertGreaterEqual(infer_oversize_risk("Spin Mop System with Bucket and Wringer"), 80)
        self.assertGreaterEqual(infer_oversize_risk("Mop and Bucket Set for Floor Cleaning"), 80)
        self.assertGreaterEqual(infer_oversize_risk("Heavy-Duty Outdoor Broom with Stiff Bristles"), 80)
        self.assertGreaterEqual(infer_oversize_risk("Large 10 Quart Wash Basin Bucket"), 65)
        self.assertGreaterEqual(infer_oversize_risk("Floor Scrub Brush with 52 inch Long Handle"), 65)
        self.assertFalse(has_valid_category("['', '']"))
        self.assertTrue(has_valid_category("Home & Kitchen > Storage Boxes"))

    def test_discovery_rejects_fragile_candidate_before_shortlisting(self) -> None:
        candidate = {
            "product_name": "Hand Painted Glass Sweets Jar with Lid",
            "category": "Cookie Jars",
            "notes": "brand Example",
            "target_price": 69.95,
            "est_monthly_sales": 278,
            "avg_review_count": 151,
            "avg_rating": 4.5,
            "fragile_risk": 90,
        }
        filters = {
            "min_price": 12,
            "max_price": 80,
            "min_monthly_sales": 200,
            "max_monthly_sales": 5000,
            "min_rating": 3.6,
            "max_rating": 4.6,
            "max_review_count": 2500,
            "max_fragile_risk": 79,
        }
        self.assertFalse(product_passes_filters(candidate, filters))

    def test_market_structure_calculates_concentration_and_new_share(self) -> None:
        rows = [
            {"asin": "A", "monthly_sales": 700, "reviews": 100, "price": 20, "listing_date": "2026-01-01"},
            {"asin": "B", "monthly_sales": 200, "reviews": 20, "price": 30, "listing_date": "2024-01-01"},
            {"asin": "C", "monthly_sales": 100, "reviews": 10, "price": 50, "listing_date": "2023-01-01"},
        ]
        result = analyze_market(rows, now=datetime(2026, 8, 12))
        self.assertEqual(result["total_monthly_sales"], 1000)
        self.assertEqual(result["top10_sales_share"], 1.0)
        self.assertAlmostEqual(result["new_listing_share_12m"], 1 / 3, places=4)

    def test_generic_taxonomy_uses_modifier_and_product_term(self) -> None:
        form = classify_product_form("Foldable Wall Mounted Bathroom Organizer Rack")
        self.assertIn("wall-mounted", form)
        self.assertIn("organizer", form)

    def test_taxonomy_does_not_treat_negated_electricity_as_electric(self) -> None:
        form = classify_product_form(
            "Wall-Mounted Storage Box for Phone, No Electricity Needed",
            "",
            "ELECTRIC ITEM CONTAINER",
        )
        self.assertNotIn("electric", form)
        self.assertIn("wall-mounted", form)
        self.assertIn("storage box", form)

    def test_taxonomy_strips_brand_and_keeps_combo_as_an_attribute(self) -> None:
        branded = classify_product_form(
            "Holaloha Toilet Bowl Brush",
            "Home & Kitchen > Toilet Brushes & Holders",
            brand="Holaloha",
            category_brands=["Holaloha", "Sellemer"],
        )
        base = classify_product_form("Storage Box", "Office > Index Card Storage", "STORAGE BOX")
        combo = classify_product_form("Storage Box Set with Dividers", "Office > Index Card Storage", "STORAGE BOX")
        self.assertNotIn("holaloha", branded)
        self.assertEqual(base, combo)

    def test_ball_storage_separates_wall_mount_from_floor_rack(self) -> None:
        wall = classify_product_form(
            "Metal Ball Holder Wall Mount for Basketball Display",
            "Sports > Basketball > Ball Storage",
            "STORAGE RACK",
        )
        floor = classify_product_form(
            "Garage Sports Equipment Organizer with Golf Bag Rack",
            "Sports > Basketball > Ball Storage",
            "STORAGE RACK",
        )
        self.assertEqual(wall, "wall-mounted ball holder")
        self.assertEqual(floor, "floor sports equipment organizer")

    def test_shared_product_exclusions_cover_logistics_and_food_contact(self) -> None:
        self.assertTrue(
            hard_exclusion_reason(
                {"product_name": "Portable Steam Cleaner", "category": "Home > Cleaning", "brand": "Generic"}
            )
        )
        self.assertTrue(
            hard_exclusion_reason(
                {"product_name": "Reusable Food Storage Bag", "category": "Home > Kitchen", "brand": "Generic"}
            )
        )

    def test_shared_product_exclusions_do_not_reject_medicine_ball_storage(self) -> None:
        self.assertEqual(
            hard_exclusion_reason(
                {
                    "product_name": "Wall Mounted Medicine Ball Storage Rack",
                    "category": "Sports & Outdoors > Exercise & Fitness > Medicine Balls",
                    "brand": "OpenBrand",
                }
            ),
            "",
        )

    def test_demand_analysis_separates_basic_and_fulfillment(self) -> None:
        reviews = [
            {"asin": "A", "rating": "1", "review_text": "It broke and does not work", "review_date": "2026-01-01"},
            {"asin": "A", "rating": "1", "review_text": "Package arrived damaged", "review_date": "2026-01-02"},
        ]
        output = analyze_demand(
            reviews,
            [{"asin": "A", "product_form": "manual brush"}],
            ROOT / "config" / "demand_taxonomy.json",
        )
        demand_types = {row["demand_type"] for row in output}
        self.assertIn("basic", demand_types)
        self.assertIn("fulfillment_noise", demand_types)

    def test_business_feasibility_requires_supplier_quote(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            research_dir = Path(temporary)
            with (research_dir / "top_products.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["asin", "competitor_type", "price"])
                writer.writeheader()
                writer.writerow({"asin": "A", "competitor_type": "seed", "price": "30"})
            result = build_business_feasibility(research_dir, ROOT / "config" / "business_feasibility_rules.json")
            self.assertEqual(result["status"], "needs_supplier_quote")
            self.assertIsNone(result["initial_inventory_cash"])

    def test_fresh_category_tree_cache_avoids_network(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            cache = Path(temporary) / "tree.json"
            expected = {"Data": [{"NodeId": "1", "name": "Home"}]}
            cache.write_text(json.dumps(expected), encoding="utf-8")
            args = SimpleNamespace(
                category_tree_json="",
                category_tree_cache=str(cache),
                category_tree_cache_hours=720,
                force_category_tree_refresh=False,
            )
            result, source = load_category_tree(args, {"category_tree": {}}, "1")
            self.assertEqual(result, expected)
            self.assertEqual(source, "fresh_cache")

    def test_auto_research_shortlist_is_ranked_and_deduplicated(self) -> None:
        rows = [
            {"source_asin": "A", "opportunity_score": "70", "recommendation": "Watch or collect more data"},
            {"source_asin": "A", "opportunity_score": "65", "recommendation": "Watch or collect more data"},
            {"source_asin": "B", "opportunity_score": "80", "recommendation": "Reject"},
            {"source_asin": "C", "opportunity_score": "60", "recommendation": "Go to supplier validation"},
        ]
        rules = {
            "recommendations": ["Go to supplier validation", "Watch or collect more data"],
            "minimum_score": 52,
            "max_candidates": 4,
        }
        shortlist = select_shortlist(rows, rules)
        self.assertEqual([row["source_asin"] for row in shortlist], ["A", "C"])

    def test_auto_research_shortlist_skips_hard_risk_and_invalid_category(self) -> None:
        rows = [
            {
                "source_asin": "LARGE",
                "opportunity_score": "80",
                "recommendation": "Watch or collect more data",
                "category": "Storage Boxes",
                "oversize_risk": "90",
            },
            {
                "source_asin": "FOOD",
                "opportunity_score": "75",
                "recommendation": "Watch or collect more data",
                "category": "Food Storage Bags",
                "compliance_risk": "90",
            },
            {
                "source_asin": "EMPTY",
                "opportunity_score": "70",
                "recommendation": "Watch or collect more data",
                "category": "['', '']",
            },
            {
                "source_asin": "VALID",
                "opportunity_score": "65",
                "recommendation": "Watch or collect more data",
                "category": "Home & Kitchen > Drawer Organizers",
            },
        ]
        rules = {
            "recommendations": ["Watch or collect more data"],
            "minimum_score": 52,
            "max_candidates": 4,
            "max_compliance_risk": 79,
            "max_fragile_risk": 79,
            "max_oversize_risk": 64,
            "require_valid_category": True,
        }
        shortlist = select_shortlist(rows, rules)
        self.assertEqual([row["source_asin"] for row in shortlist], ["VALID"])

    def test_observability_does_not_mix_previous_run_outputs(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            root = Path(temporary)
            old_selection = root / "archive" / "selection_runs" / "old_run"
            old_selection.mkdir(parents=True)
            (old_selection / "selection_ranked.csv").write_text(
                "source_asin,recommendation\nOLD,Go to supplier validation\n",
                encoding="utf-8",
            )
            metrics = collect_metrics(root, "new_run")
            self.assertEqual(metrics["validated_categories"], 0)
            self.assertEqual(metrics["validation_rows"], 0)
            self.assertEqual(metrics["discovery_status"], "not_started")

    def test_empty_selection_still_writes_a_valid_csv(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            output = Path(temporary) / "selection.csv"
            write_selection_csv([], output)
            self.assertTrue(output.exists())
            self.assertIn("opportunity_score", output.read_text(encoding="utf-8").splitlines()[0])


if __name__ == "__main__":
    unittest.main()
