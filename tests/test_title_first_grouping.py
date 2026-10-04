import json
import unittest
from datetime import datetime
from pathlib import Path

from category_shape_validation import build_rows_from_category_report, normalized_category_product
from mcp_product_opportunity_research import select_review_rows
from product_family import product_identity
from product_taxonomy import classify_product_form
from research_landscape import analyze_landscape


class TitleFirstGroupingTest(unittest.TestCase):
    def identity(self, title, source_type="KITCHEN", category="Home > Jiggers"):
        return normalized_category_product(
            {"asin": "TEST", "title": title, "product_category": source_type}, category
        )

    def test_same_jiggers_ignore_provider_type_and_multipack(self):
        titles = [
            "2-Pack Cocktail Jiggers Set, Japanese Premium 304 Stainless Steel Jigger for Bartending",
            "Japanese Style Stainless Steel Cocktail Jigger 1 oz 2 oz, 1 Pack",
            "3 Pcs Stainless Steel Japanese Jiggers for Bartending",
        ]
        rows = [self.identity(t, source_type, category) for t in titles
                for source_type in ("KITCHEN", "BAR TOOL SET", "MEASURING CUP", "COCKTAIL SHAKER")
                for category in ("Home > Jiggers", "Home > Bar Sets")]
        self.assertEqual({p["product_family"] for p in rows}, {"cocktail_jigger"})
        self.assertEqual({p["product_form"] for p in rows}, {"cocktail jigger - stainless steel - japanese"})
        self.assertTrue(all(p["classification_status"] == "title_rule" for p in rows))

    def test_material_and_structure_are_children_of_same_function(self):
        rows = [self.identity(t) for t in (
            "Japanese Stainless Steel Jigger", "Stainless Steel Bell Jigger",
            "Plastic Double Jigger", "Glass Cocktail Jigger", "100% Copper Bell Jigger",
        )]
        self.assertEqual(len({p["product_family"] for p in rows}), 1)
        self.assertEqual(len({p["product_form"] for p in rows}), 5)

    def test_accessories_do_not_turn_jigger_into_full_bar_kit(self):
        for title in (
            "Japanese Jigger for Bartending with 2 Pourers",
            "Measuring Jigger & Mixing Spoon Set, Cocktail Jigger for Bartending",
            "Japanese Jigger for Cocktails, Bar Kit Essential",
            "Stainless Steel Double Jigger with Measurements - Cocktail Shaker",
        ):
            with self.subTest(title=title):
                self.assertEqual(self.identity(title)["product_family"], "cocktail_jigger")

    def test_complete_kits_are_not_measuring_tool_multipacks(self):
        for title in (
            "Cocktail Shaker Set with Jigger and Strainer",
            "Bartender Kit 12 Pieces with Japanese Jigger",
            "Cocktail Shaker, Jigger, Spoon and Strainer Set",
            "Jigger with Cocktail Shaker and Spoon",
        ):
            with self.subTest(title=title):
                self.assertEqual(self.identity(title, "MEASURING CUP")["product_family"], "cocktail_tool_kit")

    def test_unclear_title_does_not_become_jigger_from_category(self):
        row = self.identity("10 Year Anniversary Tin Gifts for Him Her")
        self.assertEqual(row["classification_status"], "category_proxy")
        self.assertNotEqual(row["product_family"], "cocktail_jigger")
        self.assertEqual(self.identity("Model 100", "FOOD BLENDER")["classification_status"], "category_proxy")

    def test_title_overrides_conflicting_product_type(self):
        self.assertEqual(classify_product_form("Magnetic Tool Mat", product_type="tofu press"), "magnetic tool mat")
        title = "Japanese Stainless Steel Jigger"
        self.assertEqual(classify_product_form(title, product_type="cocktail shaker set"),
                         self.identity(title)["product_form"])

    def test_material_is_not_inferred_from_color(self):
        gold = self.identity("Japanese Jigger Gold")
        plated = self.identity("Japanese Jigger Stainless Steel Copper Plated")
        self.assertIn("material unspecified", gold["product_form"])
        self.assertIn("stainless steel", plated["product_form"])

    def test_double_wall_is_not_double_head(self):
        self.assertIn("structure unspecified", self.identity("Stainless Steel Jigger Double Walls")["product_form"])

    def test_tofu_alternatives_share_function_without_merging_forms(self):
        rows = [product_identity(t) for t in ("304 Stainless Steel Tofu Press", "Plastic Tofu Press", "Tofu Press Mold")]
        self.assertEqual({p["product_family"] for p in rows}, {"tofu_press"})
        self.assertEqual(len({p["product_form"] for p in rows}), 3)

    def test_unclear_group_is_pending_even_if_its_score_would_be_low(self):
        rules = json.loads(Path("config/category_shape_validation_rules.json").read_text())
        seed = {"source_category_path": "Home > Jiggers"}
        report = {"data": {"top100_products": [{"asin": "UNKNOWN", "title": "Model 100",
                   "product_category": "KITCHEN", "price": 3, "monthly_sales_volume": 1}]}}
        row = build_rows_from_category_report(seed, report, rules)[0]
        self.assertEqual(row["shape_recommendation"], "Watch shape")
        self.assertEqual(row["classification_status"], "category_proxy")
        self.assertIn("category proxy requires classification", row["validation_flags"])

    def test_other_product_family_does_not_impose_its_review_wall(self):
        rules = json.loads(Path("config/category_shape_validation_rules.json").read_text())
        rows = [{"asin": f"NET{i}", "title": "Landing Fishing Net", "brand": f"Brand {i}",
                 "monthly_sales_volume": 800, "review_count": 40, "price": 25,
                 "online_date": "2026-06-01"} for i in range(4)]
        rows += [{"asin": f"OTHER{i}", "title": "Magnetic Tool Mat", "brand": "Dominant",
                  "monthly_sales_volume": 20000, "review_count": 5000, "price": 25} for i in range(20)]
        result = build_rows_from_category_report({"source_category_path": "Tools > Mixed"},
                                                {"data": {"top100_products": rows}}, rules)
        net = next(r for r in result if r["product_family"] == "landing_net")
        self.assertEqual(net["family_sample_count"], 4)
        self.assertEqual(net["category_sample_count"], 24)
        self.assertNotIn("top10 review wall", net["validation_flags"])
        self.assertNotIn("brand concentration", net["validation_flags"])

    def test_landscape_dedupes_asins_and_keeps_pack_breakdown(self):
        rows = []
        for asin, title, sales in (
            ("SEED", "2 Pack Japanese Stainless Steel Cocktail Jigger", 600),
            ("OTHER", "Japanese Stainless Steel Cocktail Jigger 1 Pack", 400),
            ("BELL", "Stainless Steel Bell Jigger", 200),
            ("WRONG", "Cocktail Shaker Set with Jigger", 9000),
        ):
            row = self.identity(title)
            row.update(asin=asin, monthly_sales=sales, reviews=50, price=20, online_date="2026-01-01")
            rows.append(row)
        result = analyze_landscape(rows + [rows[0]], "SEED", now=datetime(2026, 9, 6))
        self.assertEqual(result["related_product_count"], 3)
        self.assertEqual(len(result["forms"]), 2)
        target = next(f for f in result["forms"] if f["is_target"])
        self.assertEqual(target["monthly_sales"], 1000)
        self.assertAlmostEqual(target["sales_share"], 1000 / 1200)
        self.assertEqual({p["quantity"] for p in target["packs"]}, {"1", "2"})

    def test_reviews_include_other_forms_but_not_unrelated_category_items(self):
        rows = []
        for i, (title, form, role) in enumerate([
            ("Japanese Stainless Steel Jigger", "japanese", "seed"),
            ("Japanese Stainless Steel Jigger", "japanese", "direct"),
            ("Japanese Stainless Steel Jigger", "japanese", "direct"),
            ("Stainless Steel Bell Jigger", "bell", "keyword"),
            ("Plastic Double Jigger", "plastic", "keyword"),
            ("Cocktail Shaker Set with Jigger", "kit", "keyword"),
        ]):
            rows.append({"asin": str(i), "title": title, "product_form": form,
                         "competitor_type": role, "monthly_sales": 1000 - i})
        selected = select_review_rows(rows, "0", 4)
        self.assertEqual({p["asin"] for p in selected}, {"0", "1", "3", "4"})


if __name__ == "__main__":
    unittest.main()
