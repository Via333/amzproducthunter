import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime

from category_shape_validation import (build_rows_from_category_report, category_metrics,
    evaluate_shape_row, new_entrant_metrics, update_shape_archive, read_csv)
from product_risk import infer_compliance_risk
from opportunity_feedback import load_feedback, feedback_state
from discover_sorftime_opportunities import select_categories

ROOT = Path(__file__).resolve().parents[1]


class OptimizationRulesTest(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads((ROOT / "config/category_shape_validation_rules.json").read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rules["_feedback_root"] = self.root
        load_feedback.cache_clear()

    def products(self, title="Silicone Sink Caddy", count=8):
        return [{"asin": f"B0TEST{i:04d}", "title": title, "brand": f"Company {i}",
                 "price": 25, "monthly_sales_volume": 800, "review_count": 30,
                 "online_date": "2026-09-01", "fba_fee": 4} for i in range(count)]

    def rows(self, products, path="Home > Sink Caddies"):
        return build_rows_from_category_report({"source_category_path": path},
            {"data": {"top100_products": products}}, self.rules)

    def test_proxy_never_becomes_opportunity_with_many_samples(self):
        row = self.rows(self.products("Model A100"))[0]
        self.assertEqual(row["classification_status"], "category_proxy")
        self.assertEqual(row["shape_recommendation"], "Watch shape")
        self.assertIn("代理", row["opportunity_thesis"])

    def test_small_brand_sample_is_not_monopoly_or_clearance(self):
        row = self.rows(self.products(count=2))[0]
        self.assertNotIn("brand concentration", row["validation_flags"])
        self.assertIn("insufficient brand evidence", row["validation_flags"])
        self.assertNotEqual(row["shape_recommendation"], "Shape opportunity")

    def test_sales_weighted_brand_share_deduplicates_parents(self):
        metrics = category_metrics([
            {"asin":"a", "parent_asin":"P", "brand":"A", "monthly_sales":900},
            {"asin":"b", "parent_asin":"P", "brand":"A", "monthly_sales":900},
            {"asin":"c", "brand":"B", "monthly_sales":100}])
        self.assertEqual(metrics["category_sample_count"], 2)
        self.assertEqual(metrics["category_top_brand_sales_share"], .9)

    def test_ancestor_not_product_but_leaf_still_evidence(self):
        self.assertLess(infer_compliance_risk("Napkin Holder", "Home > Dinnerware > Napkin Holders"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Model A100", "Home > Paper Plates"), 80)
        self.assertGreaterEqual(infer_compliance_risk("Paper Plates", "Home > Napkin Holders"), 80)

    def test_old_parent_high_reviews_and_future_dates_not_successes(self):
        products = [
            {"asin":"a", "online_date":"2026-08-01", "reviews":900, "monthly_sales":500},
            {"asin":"b", "online_date":"2026-08-01", "parent_online_date":"2020-01-01", "reviews":10, "monthly_sales":500},
            {"asin":"c", "online_date":"2027-08-01", "reviews":10, "monthly_sales":500},
            {"asin":"d", "online_date":"2026-08-01", "reviews":10, "monthly_sales":500}]
        result = new_entrant_metrics(products, self.rules["thresholds"], datetime(2026, 10, 4))
        self.assertEqual(result["new_entrant_success_count_raw"], 3)
        self.assertEqual(result["new_entrant_success_count"], 1)

    def test_budget_is_not_profit_or_hard_rejection(self):
        products = self.products()
        for p in products:
            p["fba_fee"] = 14
        row = self.rows(products)[0]
        self.assertLess(row["form_landed_cost_ceiling_median"], 1.5)
        self.assertEqual(row["shape_recommendation"], "Watch shape")
        for p in products:
            p.pop("fba_fee")
        row = self.rows(products)[0]
        self.assertEqual(row["form_landed_cost_ceiling_median"], "")
        self.assertEqual(row["form_fee_source"], "missing")

    def test_ip_hold_is_scoped_and_blocks_new_score(self):
        path = self.root / "research/B0TEST0000/risk_review.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"asin":"B0TEST0000", "site":"US", "scope":{"asins":["B0REF00000"]},
                                    "patent":{"risk":"high","clearance":"blocked"}}))
        row = self.rows(self.products())[0]
        self.assertEqual(row["review_hold_status"], "ip_review_required")
        self.assertEqual(row["shape_recommendation"], "Reject category/form")
        self.assertEqual(feedback_state({"seed_asin":"B0REF00000"}, self.root)[0], "ip_review_required")
        self.assertEqual(feedback_state({"seed_asin":"B0OTHER000", "product_form":row["product_form"]}, self.root), ("", ""))

    def test_legacy_repair_does_not_revive_invalidated(self):
        from workflow_storage import atomic_write_csv
        rows = [dict(shape_archive_key="A", archive_status="not_in_latest_run", category_path="Home > Organizers", product_form="cable clip"),
                dict(shape_archive_key="B", archive_status="invalidated_by_rule", category_path="Home > Organizers", product_form="cable clip")]
        archive = self.root / "archive"
        atomic_write_csv(archive / "shape_opportunity_library.csv", rows, list(rows[0]))
        with patch.dict(os.environ, {"AMZ_SOURCE_ROOT":str(self.root)}):
            update_shape_archive([], archive, "repair")
        result = {r["shape_archive_key"]:r["archive_status"] for r in read_csv(archive / "shape_opportunity_library.csv")}
        self.assertEqual(result, {"A":"stale_needs_rescan", "B":"invalidated_by_rule"})

    def test_reviewed_required_checks_hold_only_its_asin(self):
        path = self.root / "research/B0TEST0000/required_checks.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"asin":"B0TEST0000", "site":"US", "checks":{
            "seasonality":{"review_status":"reviewed", "risk":"high"}}}))
        self.assertEqual(feedback_state({"seed_asin":"B0TEST0000"}, self.root)[0], "seasonal_confirmed")
        self.assertEqual(feedback_state({"seed_asin":"B0TEST0001"}, self.root), ("", ""))

    def test_mixed_verified_form_uses_remaining_subset(self):
        row = self.rows(self.products())[0]
        row["form_excluded_share"] = .7
        result = evaluate_shape_row(row, self.rules)
        self.assertIn("mixed with excluded products", result["validation_flags"])
        self.assertNotEqual(result["shape_recommendation"], "Reject category/form")
        row["form_count"] = 4
        result = evaluate_shape_row(row, self.rules)
        self.assertEqual(result["decisive_reject_reason"], "excluded products")

    def test_all_excluded_emits_one_decisive_reason(self):
        row = self.rows(self.products())[0]
        row.update(form_count=0, form_excluded_share=1, form_excluded_reasons="compliance risk")
        result = evaluate_shape_row(row, self.rules)
        self.assertEqual(result["validation_flags"], "all products excluded: compliance risk")
        self.assertEqual(result["decisive_reject_reason"], result["validation_flags"])

    def test_rotation_reserves_rescans_without_duplicate_slots(self):
        tree = {"Data":[{"NodeId":str(i), "name":f"Storage {i}", "children":[]} for i in range(10)]}
        rules = {"max_categories":4, "category_filters":{}, "rotation":{"new_category_quota":50, "rescan_quota":50}}
        state = {str(i):{"scan_count":1,"last_scanned_at":"2026-07-01"} for i in range(5)}
        selected = select_categories(tree, rules, state)
        self.assertEqual(sum(c["previous_scan_count"] > 0 for c in selected), 2)
        self.assertEqual(len({c["category_id"] for c in selected}), 4)
