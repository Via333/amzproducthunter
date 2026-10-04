import csv
import json
import tempfile
import unittest
from pathlib import Path

from category_shape_validation import product_exclusion_reason
from product_selection import hard_exclusion_reason
from product_risk import infer_oversize_risk
from research_landscape import (
    analyze_landscape, assess_landscape_product, build_landscape,
    merge_landscape_product, normalize_landscape_product,
)
from build_product_research_pages import render_landscape_products, render_landscape


class LandscapeScreeningTest(unittest.TestCase):
    def product(self, category="", **fields):
        return normalize_landscape_product({
            "asin": "B000000001", "title": "Silicone Bathtub Drain Stopper",
            "price": 20, "monthly_sales_volume": 600, "review_count": 50,
            "online_date": "2026-01-01", **fields,
        }, category, {"kind": "keyword_search", "path": "search.json"})

    def test_missing_category_is_pending_not_eligible_or_rejected(self):
        p = self.product()
        form = analyze_landscape([p], p["asin"])["forms"][0]
        self.assertEqual((form["count"], form["eligible_count"], form["pending_count"], form["excluded_count"]), (1, 0, 1, 0))
        self.assertEqual(form["exclusion_reasons"], {})
        self.assertIn("暂不判断", form["next_check"])
        self.assertEqual(form["products"][0]["screening_status"], "needs_evidence")
        self.assertEqual(form["products"][0]["excluded_reason"], "")
        self.assertEqual(form["new_entrants"]["new_entrant_count"], 0)
        self.assertEqual(form["monthly_sales"], 600)

    def test_top100_rank_and_keyword_subcategory_are_real_evidence(self):
        p = self.product(category_rank="Category: Bathtub Drains, Rank: 9")
        self.assertEqual(p["bsr_category"], "Bathtub Drains")
        self.assertEqual(p["category_evidence"][0]["field"], "category_rank")
        self.assertEqual(assess_landscape_product(p, {})["screening_status"], "eligible")
        p = self.product(top_category=["Tools & Home Improvement", "hi"],
                         subcategory=[["Bathtub Drains", "6808090011", 9, "20260904"]])
        self.assertEqual(p["bsr_category"], "Tools & Home Improvement > Bathtub Drains")
        self.assertNotIn("6808090011", p["bsr_category"])
        self.assertEqual(assess_landscape_product(p, {})["screening_status"], "eligible")

    def test_type_label_or_invalid_category_is_not_category_evidence(self):
        for category in ("", "Unknown", "N/A", "6808090011"):
            p = self.product(category, product_category="DRAIN STRAINER")
            self.assertEqual(assess_landscape_product(p, {})["screening_status"], "needs_evidence")

    def test_missing_category_does_not_hide_real_risks(self):
        for fields in ({"title": "Glass Food Storage Jar"},
                       {"title": "OXO Silicone Bathtub Drain Stopper", "brand": "OXO"},
                       {"source_exclusion_reason": "oversize risk"}):
            with self.subTest(fields=fields):
                result = assess_landscape_product(self.product(**fields), {})
                self.assertEqual(result["screening_status"], "excluded")
                self.assertNotIn("category evidence", result["excluded_reason"])
        result = assess_landscape_product(self.product(title="Unsafe Drain Stopper"),
                                         {"hard_exclusions": {"title_contains": ["unsafe"]}})
        self.assertEqual(result["screening_status"], "excluded")

    def test_existing_selection_gates_stay_strict_by_default(self):
        p = self.product()
        self.assertEqual(product_exclusion_reason(p, {}), "missing or invalid category evidence")
        row = {"product_name": p["title"], "category": ""}
        self.assertEqual(hard_exclusion_reason(row, {}), "missing or invalid category evidence")
        self.assertEqual(hard_exclusion_reason(row, {}, require_valid_category=False), "")

    def test_category_only_size_signal_requires_review(self):
        p = self.product("Patio > Deck Boxes")
        result = assess_landscape_product(p, {})
        self.assertEqual(result["screening_status"], "needs_evidence")
        self.assertIn("实际尺寸", result["pending_reason"])
        p = self.product("Patio > Deck Boxes", title="60 inch Bathtub Drain Stopper Assembly")
        self.assertEqual(assess_landscape_product(p, {})["screening_status"], "excluded")

    def test_decimal_dimensions_are_not_truncated_to_oversize_inches(self):
        for dimension in ('1.45 in', '1.45 inches', '1.45"', '2.40 inch', '23.99 inches'):
            with self.subTest(dimension=dimension):
                self.assertLess(infer_oversize_risk(f"Drain Stopper Fits {dimension}"), 65)
        for dimension in ('24 in', '24.5 inches', '60"'):
            self.assertGreaterEqual(infer_oversize_risk(f"Drain Stopper Assembly {dimension}"), 65)

    def test_drain_component_keyword_conflict_requires_review_without_allowing_weapons(self):
        p = self.product("Tools > Bathtub Drains", title="Bathtub Drain Stopper Brass Bullet Core")
        result = assess_landscape_product(p, {})
        self.assertEqual(result["screening_status"], "needs_evidence")
        self.assertIn("Bullet Core", result["pending_reason"])
        p = self.product("Tools > Bathtub Drains", title="Bathtub Drain Stopper Rifle Bullet Core")
        self.assertEqual(assess_landscape_product(p, {})["screening_status"], "excluded")

    def test_merge_keeps_categories_and_risks_from_all_sources(self):
        valid = self.product("Tools > Bathtub Drains")
        empty = self.product()
        merged = merge_landscape_product(valid, empty)
        self.assertEqual(merged["bsr_category"], valid["bsr_category"])
        self.assertEqual(merged["category_evidence"], valid["category_evidence"])
        self.assertEqual(assess_landscape_product(merged, {})["screening_status"], "eligible")
        risky = self.product("Grocery > Culinary Salt")
        merged = merge_landscape_product(risky, valid)
        self.assertEqual(len(merged["category_evidence"]), 2)
        self.assertEqual(assess_landscape_product(merged, {})["screening_status"], "excluded")
        marked = self.product(source_exclusion_reason="oversize risk")
        self.assertEqual(assess_landscape_product(merge_landscape_product(marked, valid), {})["excluded_reason"], "oversize risk")

    def test_mixed_form_counts_partition_samples_without_promoting_pending(self):
        rows = [self.product("Tools > Bathtub Drains"), self.product(asin="B000000002"),
                self.product(asin="B000000003", brand="OXO")]
        result = analyze_landscape(rows, rows[0]["asin"])
        self.assertEqual(len(result["forms"]), 1)
        form = result["forms"][0]
        self.assertEqual((form["count"], form["eligible_count"], form["pending_count"], form["excluded_count"]), (3, 1, 1, 1))
        self.assertEqual(form["new_entrants"]["new_entrant_count"], 1)
        html = render_landscape(result)
        for label in ("待补 1", "排除 1", "待补证据：", "通过基础筛选，尚非机会结论", "已排除："):
            self.assertIn(label, html)

    def test_render_legacy_missing_evidence_as_pending_and_escape_source(self):
        html = render_landscape_products([{
            "asin": "B000000001", "excluded_reason": "missing or invalid category evidence",
            "category_evidence": [{"category_path": "Tools > <script>", "path": 'x" onclick="bad'}],
        }], {})
        self.assertIn("待补证据：", html)
        self.assertNotIn("已排除：", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn(' onclick="bad', html)

    def test_build_report_context_only_applies_to_its_members_and_survives_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            study = root / "research/B000000001"
            study.mkdir(parents=True)
            (study / "research_sources.json").write_text(json.dumps({"site": "US", "category_source": "category.json"}))
            title = "Silicone Bathtub Drain Stopper"
            (root / "category.json").write_text(json.dumps({"data": {
                "category_stats_report": {"node_id": "6808090011", "category_name": "Bathtub Drains"},
                "top100_products": [{"asin": study.name, "title": title, "price": 20}],
            }}))
            (study / "search_test.json").write_text(json.dumps({
                "tool": "product_search_from_name", "arguments": {"amz_site": "US"}, "result": {"products": [
                    {"asin": "B000000002", "title": title, "price": 20},
                    {"asin": "B000000003", "title": title, "price": 20,
                     "top_category": ["Tools", "hi"], "subcategory": [["Drain Plugs", "456", 1]]},
                ]},
            }))
            with (study / "top_products.csv").open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["asin", "title", "bsr_category"])
                writer.writeheader()
                writer.writerow({"asin": study.name, "title": title})
            result = build_landscape(root, study)
            products = {p["asin"]: p for form in result["forms"] for p in form["products"]}
            self.assertEqual(products[study.name]["screening_status"], "eligible")
            self.assertEqual(products[study.name]["category_evidence"][0]["path"], "category.json")
            self.assertEqual(products["B000000002"]["screening_status"], "needs_evidence")
            self.assertEqual(products["B000000003"]["category_evidence"][0]["category_path"], "Tools > Drain Plugs")
            self.assertEqual(products["B000000003"]["screening_status"], "eligible")


if __name__ == "__main__":
    unittest.main()
