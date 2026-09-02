import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from category_health import category_health_from_records, rank_categories_by_health
from category_shape_validation import (
    build_validation_rows,
    group_seeds_by_category,
    is_brand_dependent,
    load_json,
    load_ranked_categories,
    manual_asin_seeds,
    new_entrant_metrics,
    select_seed_rows,
    update_shape_archive,
)
from weekly_scan_observability import seed_reject_reason


def days_ago(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def product(asin, title, form, sales, reviews, brand, online_date=None, price=24.99):
    return {
        "asin": asin,
        "title": title,
        "product_category": form,
        "monthly_sales_volume": str(sales),
        "review_count": reviews,
        "star_rating": 4.4,
        "price": price,
        "brand": brand,
        "delivery_type": "FBA",
        "seller_origin": "中国",
        "online_date": online_date or days_ago(1200),
    }


def seed(asin, category_id, title, rank_score="60.0", path="Tools > Tool Holsters", name="Tool Holsters"):
    return {
        "source_asin": asin,
        "source_category_id": category_id,
        "source_category_name": name,
        "source_category_path": path,
        "product_name": title,
        "opportunity_score": rank_score,
        "recommendation": "Watch or collect more data",
    }


class ShapeFirstValidationTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.runs_root = self.root / "discovery_runs"
        self.run_dir = self.runs_root / "20260901_test"
        self.raw_dir = self.run_dir / "raw_category_reports"
        self.raw_dir.mkdir(parents=True)
        (self.run_dir / "run_manifest.json").write_text(
            json.dumps({"status": "success", "finished_at": "2026-09-01T21:42:46"}),
            encoding="utf-8",
        )
        base = load_json("config/category_shape_validation_rules.json")
        self.rules = {
            "discovery_runs_root": str(self.runs_root),
            "seed_recommendation_contains": "Watch",
            "seed_limit": 60,
            "category_limit": base["category_limit"],
            "allow_adjacent_shape_opportunity": True,
            "allow_deep_dive_fallback": False,
            "research_root": str(self.root / "research"),
            "thresholds": base["thresholds"],
            "_scoring_rules": load_json("config/scoring_rules.json"),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def write_report(self, category_id, products):
        (self.raw_dir / f"{category_id}.json").write_text(
            json.dumps({"data": {"top100_products": products}}),
            encoding="utf-8",
        )

    def write_categories(self, rows):
        fields = sorted({key for row in rows for key in row})
        with (self.run_dir / "categories.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_ranked_categories_default_to_all_successful_run_categories(self):
        self.write_report("1001", [product("A1", "Boat Caddy", "BOAT CADDY", 500, 50, "Brand A")])
        self.write_report("1002", [product("B1", "Tool Holster", "TOOL HOLSTER", 400, 40, "Brand B")])
        self.write_categories(
            [
                {"category_id": "1001", "name": "A", "path": "Tools > A", "scan_status": "success", "category_health_score": "72", "category_health_rank": "1"},
                {"category_id": "1002", "name": "B", "path": "Tools > B", "scan_status": "success", "category_health_score": "", "category_health_rank": ""},
                {"category_id": "1003", "name": "C", "path": "Tools > C", "scan_status": "failed", "category_health_score": "90", "category_health_rank": "1"},
            ]
        )
        rules = {**self.rules, "category_ranking": "", "category_ranking_limit": 0}

        ranked = load_ranked_categories(rules, self.run_dir)

        self.assertEqual([group[1][0]["source_category_id"] for group in ranked], ["1001", "1002"])
        self.assertEqual(ranked[0][1][0]["category_health_score"], "72")

    def test_manual_asin_marks_its_category_as_seed_scope(self):
        self.write_report(
            "1001",
            [
                product("MANUAL1", "Magnetic Tool Mat", "MAGNETIC TOOL MAT", 600, 50, "Brand A"),
                product("OTHER1", "Large Magnetic Tool Mat", "MAGNETIC TOOL MAT", 500, 70, "Brand B"),
            ],
        )
        self.write_categories(
            [{"category_id": "1001", "name": "Mats", "path": "Tools > Mats", "scan_status": "success"}]
        )

        seeds = manual_asin_seeds(["manual1"], self.run_dir)
        rows = build_validation_rows(seeds, [], self.rules, self.run_dir)

        self.assertEqual(seeds[0]["source_asin"], "MANUAL1")
        self.assertEqual({row["shape_scope"] for row in rows}, {"seed_form"})

    def test_product_exclusion_majority_rejects_shape(self):
        self.write_report(
            "1004",
            [
                product("V1", "Cordless Vacuum Attachment", "VACUUM TOOL", 600, 50, "Brand A", days_ago(100)),
                product("V2", "Portable Vacuum Accessory", "VACUUM TOOL", 550, 40, "Brand B", days_ago(120)),
                product("V3", "Vacuum Cleaning Tool", "VACUUM TOOL", 500, 30, "Brand C", days_ago(140)),
                product("V4", "Floor Cleaning Tool", "VACUUM TOOL", 450, 20, "Brand D", days_ago(160)),
            ],
        )

        rows = build_validation_rows([seed("V1", "1004", "Cordless Vacuum Attachment")], [], self.rules, self.run_dir)
        row = rows[0]

        self.assertEqual(row["form_excluded_share"], 0.75)
        self.assertEqual(row["form_count"], 1)
        self.assertNotIn("V1", row["form_reference_asins"])
        self.assertIn("V4", row["form_reference_asins"])
        self.assertIn("excluded products", row["validation_flags"])
        self.assertEqual(row["shape_recommendation"], "Reject category/form")

    def test_generic_form_name_cannot_enter_opportunity_pool(self):
        self.write_report(
            "1006",
            [
                product(f"G{i}", f"Model {i}", "SPORTING GOODS", 700, 80, f"Brand {i}", days_ago(100 + i * 20))
                for i in range(1, 6)
            ],
        )
        generic_seed = seed("", "1006", "", path="Sports & Outdoors", name="Sporting Goods")
        rows = build_validation_rows([], [], self.rules, self.run_dir, extra_categories=[("id:1006", [generic_seed])])
        row = rows[0]
        self.assertIn("generic form classification", row["validation_flags"])
        self.assertNotEqual(row["shape_recommendation"], "Shape opportunity")

    def test_price_band_outside_target_caps_shape_at_watch(self):
        self.write_report(
            "1005",
            [
                product("P1", "Premium Boat Caddy", "BOAT CADDY", 700, 80, "Brand A", days_ago(100), 140),
                product("P2", "Marine Boat Caddy", "BOAT CADDY", 650, 70, "Brand B", days_ago(120), 150),
                product("P3", "Rail Boat Caddy", "BOAT CADDY", 600, 60, "Brand C", days_ago(140), 160),
            ],
        )

        rows = build_validation_rows([seed("P1", "1005", "Premium Boat Caddy")], [], self.rules, self.run_dir)
        row = rows[0]

        self.assertEqual(row["form_price_median"], 150)
        self.assertIn("price band outside target", row["validation_flags"])
        self.assertEqual(row["shape_recommendation"], "Watch shape")

    # --- category dedupe ---------------------------------------------------

    def test_seeds_in_same_category_share_one_validation(self):
        self.write_report(
            "1001",
            [
                product("B0SEEDA", "Magnetic Tool Mat", "MAGNETIC TOOL MAT", 600, 50, "Brand A"),
                product("B0SEEDB", "Quick Draw Tool Holster", "UTILITY HOLSTER POUCH", 400, 80, "Brand B"),
                product("B0OTHER1", "Large Magnetic Tool Mat", "MAGNETIC TOOL MAT", 500, 70, "Brand C"),
                product("B0OTHER2", "Universal Tool Holster", "UTILITY HOLSTER POUCH", 350, 90, "Brand D"),
            ],
        )
        seeds = select_seed_rows(
            [seed("B0SEEDA", "1001", "Magnetic Tool Mat"), seed("B0SEEDB", "1001", "Quick Draw Tool Holster", "58.0")],
            self.rules,
        )
        rows = build_validation_rows(seeds, [], self.rules, self.run_dir)

        forms = {row["product_form"] for row in rows}
        self.assertEqual(len(rows), len(forms), "each form of a category must appear once, not once per seed")
        self.assertEqual({row["seed_asins"] for row in rows}, {"B0SEEDA; B0SEEDB"})
        self.assertEqual({row["seed_count"] for row in rows}, {2})
        self.assertEqual({row["shape_scope"] for row in rows}, {"seed_form"}, "both seed forms are seed_form")
        self.assertEqual(len(group_seeds_by_category(seeds)), 1)

    # --- new-entrant metrics ----------------------------------------------

    def test_new_entrant_metrics_and_reference_asins(self):
        products = [
            {"asin": "OLD1", "monthly_sales": 900, "reviews": 2000, "online_date": days_ago(2000)},
            {"asin": "NEW1", "monthly_sales": 650, "reviews": 40, "online_date": days_ago(120)},
            {"asin": "NEW2", "monthly_sales": 300, "reviews": 15, "online_date": days_ago(300)},
            {"asin": "NEW3", "monthly_sales": 50, "reviews": 3, "online_date": days_ago(60)},
            {"asin": "NODATE", "monthly_sales": 400, "reviews": 20, "online_date": ""},
        ]
        metrics = new_entrant_metrics(products, self.rules["thresholds"])
        self.assertEqual(metrics["dated_count"], 4)
        self.assertEqual(metrics["new_entrant_count"], 3)
        self.assertEqual(metrics["new_entrant_success_count"], 2)
        self.assertAlmostEqual(metrics["new_entrant_success_rate"], 0.667, places=3)
        self.assertEqual(metrics["reference_asins"], "NEW1:650/40; NEW2:300/15")

    def test_form_with_winning_new_entrants_scores_above_closed_form(self):
        self.write_report(
            "1002",
            [
                # open form: recent launches are selling
                product("B0OPEN1", "Foldable Boat Caddy", "BOAT CADDY", 500, 60, "Brand A", days_ago(200)),
                product("B0OPEN2", "Boat Caddy Organizer", "BOAT CADDY", 420, 35, "Brand B", days_ago(300)),
                product("B0OPEN3", "Marine Caddy Cup Holder", "BOAT CADDY", 380, 90, "Brand C", days_ago(150)),
                product("B0OPEN4", "Rail Mount Boat Caddy", "BOAT CADDY", 300, 200, "Brand D", days_ago(1500)),
                # closed form: same aggregate numbers, but every recent launch failed
                product("B0SHUT1", "Boat Trash Can", "TRASH CAN", 500, 60, "Brand E", days_ago(1500)),
                product("B0SHUT2", "Marine Trash Bin", "TRASH CAN", 420, 35, "Brand F", days_ago(1600)),
                product("B0SHUT3", "Collapsible Boat Bin", "TRASH CAN", 30, 2, "Brand G", days_ago(200)),
                product("B0SHUT4", "Boat Waste Basket", "TRASH CAN", 40, 5, "Brand H", days_ago(250)),
            ],
        )
        seeds = select_seed_rows([seed("B0OPEN1", "1002", "Foldable Boat Caddy")], self.rules)
        rows = {row["product_form"]: row for row in build_validation_rows(seeds, [], self.rules, self.run_dir)}

        open_form, closed_form = rows["boat caddy"], rows["trash can"]
        self.assertEqual(open_form["form_new_entrant_count"], 3)
        self.assertEqual(open_form["form_new_entrant_success_count"], 3)
        self.assertGreater(open_form["shape_score"], closed_form["shape_score"])
        self.assertIn("new entrants not selling", closed_form["validation_flags"])
        self.assertEqual(closed_form["shape_recommendation"], "Reject category/form")
        self.assertTrue(open_form["form_reference_asins"].startswith("B0OPEN1:500/60"))

    # --- brand-dependent accessories -------------------------------------

    def test_brand_dependent_detection(self):
        brands = {"swiffer", "brand a"}
        self.assertTrue(is_brand_dependent({"title": "Duster Refills Compatible with Swiffer Duster", "brand": "Generic"}, brands))
        self.assertTrue(is_brand_dependent({"title": "80 Count Refills for Swiffer 360", "brand": "Other"}, brands))
        self.assertFalse(is_brand_dependent({"title": "Swiffer Duster Refills 20 Count", "brand": "Swiffer"}, brands))
        self.assertFalse(is_brand_dependent({"title": "Microfiber Duster with Extension Pole", "brand": "Brand A"}, brands))

    def test_brand_dependent_form_is_rejected_even_when_numbers_look_good(self):
        self.write_report(
            "1003",
            [
                product("B0SWIF1", "Swiffer Duster Refills 20 Count", "DUSTER REFILL", 9000, 5000, "Swiffer", days_ago(3000)),
                product("B0COMP1", "Duster Refills Compatible with Swiffer", "DUSTER REFILL", 2500, 120, "Brand A", days_ago(200)),
                product("B0COMP2", "80 Count Refills for Swiffer Duster", "DUSTER REFILL", 2000, 90, "Brand B", days_ago(300)),
                product("B0COMP3", "Unscented Duster Refills Compatible with Swiffer", "DUSTER REFILL", 1800, 60, "Brand C", days_ago(150)),
                product("B0POLE1", "Extendable Microfiber Duster", "DUSTER", 700, 200, "Brand D", days_ago(400)),
                product("B0POLE2", "Telescoping Ceiling Fan Duster", "DUSTER", 600, 150, "Brand E", days_ago(500)),
                product("B0POLE3", "Bendable Microfiber Duster", "DUSTER", 500, 80, "Brand F", days_ago(250)),
            ],
        )
        seeds = select_seed_rows([seed("B0COMP1", "1003", "Duster Refills Compatible with Swiffer")], self.rules)
        rows = {row["product_form"]: row for row in build_validation_rows(seeds, [], self.rules, self.run_dir)}

        refill = rows["duster refill"]
        self.assertGreaterEqual(refill["form_brand_dependent_share"], 0.5)
        self.assertIn("brand-dependent accessory", refill["validation_flags"])
        self.assertEqual(refill["shape_recommendation"], "Reject category/form")
        self.assertNotIn("brand-dependent accessory", rows["duster"]["validation_flags"])

    # --- adjacent forms can enter the pool --------------------------------

    def test_strong_adjacent_form_becomes_shape_opportunity(self):
        self.write_report(
            "1004",
            [
                product("B0SEED1", "Trading Card Binder 4 Pocket", "CARD BINDER", 250, 20, "Brand A", days_ago(100)),
                product("B0BOX01", "Index Card Storage Box", "STORAGE BOX", 800, 90, "Brand B", days_ago(200)),
                product("B0BOX02", "3x5 Index Card Holder Box", "STORAGE BOX", 700, 60, "Brand C", days_ago(300)),
                product("B0BOX03", "Flashcard Storage Box with Dividers", "STORAGE BOX", 650, 110, "Brand D", days_ago(400)),
                product("B0BOX04", "Recipe Card Box", "STORAGE BOX", 500, 40, "Brand E", days_ago(1800)),
                product("B0BOX05", "Photo Storage Box 4x6", "STORAGE BOX", 600, 130, "Brand F", days_ago(2400)),
            ],
        )
        seeds = select_seed_rows([seed("B0SEED1", "1004", "Trading Card Binder 4 Pocket")], self.rules)
        rows = {row["product_form"]: row for row in build_validation_rows(seeds, [], self.rules, self.run_dir)}

        box = rows["storage box"]
        self.assertEqual(box["shape_scope"], "adjacent_form")
        self.assertEqual(box["shape_recommendation"], "Shape opportunity")
        # the seed's own form is a single listing: never an opportunity on its own
        self.assertNotEqual(rows["card binder"]["shape_recommendation"], "Shape opportunity")

    def test_adjacent_form_respects_larger_sample_floor(self):
        strict = {**self.rules, "thresholds": {**self.rules["thresholds"], "form_min_count_adjacent": 8}}
        self.write_report(
            "1005",
            [
                product("B0SEED1", "Trading Card Binder", "CARD BINDER", 250, 20, "Brand A", days_ago(100)),
                product("B0BOX01", "Index Card Storage Box", "STORAGE BOX", 800, 90, "Brand B", days_ago(200)),
                product("B0BOX02", "3x5 Index Card Holder Box", "STORAGE BOX", 700, 60, "Brand C", days_ago(300)),
                product("B0BOX03", "Flashcard Storage Box", "STORAGE BOX", 650, 110, "Brand D", days_ago(400)),
            ],
        )
        seeds = select_seed_rows([seed("B0SEED1", "1005", "Trading Card Binder")], strict)
        rows = {row["product_form"]: row for row in build_validation_rows(seeds, [], strict, self.run_dir)}
        self.assertEqual(rows["storage box"]["shape_recommendation"], "Watch shape")

    # --- health-ranked categories without seeds ---------------------------

    def test_health_ranked_category_without_seed_is_validated(self):
        self.write_report(
            "2001",
            [
                product("B0RANK1", "Silicone Sink Caddy", "SINK CADDY", 900, 80, "Brand A", days_ago(150)),
                product("B0RANK2", "Sink Sponge Holder", "SINK CADDY", 700, 60, "Brand B", days_ago(250)),
                product("B0RANK3", "Kitchen Sink Organizer", "SINK CADDY", 600, 40, "Brand C", days_ago(350)),
            ],
        )
        ranking_path = self.root / "discovered_categories.csv"
        with ranking_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["category_id", "name", "path", "scan_status", "category_health_score"])
            writer.writeheader()
            writer.writerow({"category_id": "2001", "name": "Sink Caddies", "path": "Kitchen > Sink Caddies", "scan_status": "success", "category_health_score": "81.2"})
            writer.writerow({"category_id": "2002", "name": "Missing Report", "path": "Kitchen > Missing", "scan_status": "success", "category_health_score": "90.0"})
        rules = {**self.rules, "category_ranking": str(ranking_path), "category_ranking_limit": 5}

        extra = load_ranked_categories(rules, self.run_dir)
        self.assertEqual([key for key, _ in extra], ["id:2001"], "categories without a raw report are skipped")
        rows = build_validation_rows([], [], rules, self.run_dir, extra_categories=extra)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["shape_scope"], "category_form")
        self.assertEqual(rows[0]["seed_asin"], "")
        self.assertEqual(rows[0]["seed_count"], 0)
        self.assertEqual(rows[0]["source_category_id"], "2001")
        self.assertEqual(rows[0]["shape_recommendation"], "Shape opportunity")

    # --- category health ---------------------------------------------------

    def test_category_health_prefers_open_markets(self):
        open_market = [
            product(f"B0OPEN{i}", f"Open item {i}", "FORM", 600, 40, f"Brand {i}", days_ago(100 + i * 30)) for i in range(10)
        ]
        walled_market = [
            product(f"B0WALL{i}", f"Walled item {i}", "FORM", 600, 4000, "Big Brand", days_ago(3000)) for i in range(10)
        ]
        open_health = category_health_from_records(open_market, "Home > Open")
        walled_health = category_health_from_records(walled_market, "Home > Walled")

        self.assertGreater(open_health["category_health_score"], walled_health["category_health_score"])
        self.assertIn("brand concentration", walled_health["category_health_flags"])
        self.assertIn("category review wall", walled_health["category_health_flags"])
        self.assertEqual(open_health["category_health_flags"], "")

        categories = [
            {"path": "a", "scan_status": "success", **walled_health},
            {"path": "b", "scan_status": "success", **open_health},
            {"path": "c", "scan_status": "failed"},
        ]
        ranked = rank_categories_by_health(categories)
        self.assertEqual([c["path"] for c in ranked], ["b", "a"])
        self.assertEqual(categories[1]["category_health_rank"], 1)
        self.assertNotIn("category_health_rank", categories[2])

    def test_shape_archive_is_idempotent_within_the_same_run(self):
        row = {
            "category_path": "Tools > Tool Holsters",
            "product_form": "utility holster pouch",
            "shape_score": "74.6",
            "shape_recommendation": "Shape opportunity",
        }
        update_shape_archive([row], self.root, "run-1")
        update_shape_archive([row], self.root, "run-1")
        with (self.root / "shape_opportunity_library.csv").open(encoding="utf-8-sig", newline="") as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual(saved[0]["archive_seen_count"], "1")

        update_shape_archive([row], self.root, "run-2")
        with (self.root / "shape_opportunity_library.csv").open(encoding="utf-8-sig", newline="") as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual(saved[0]["archive_seen_count"], "2")

    def test_old_invalid_opportunity_is_retained_but_marked_invalid(self):
        row = {
            "seed_title": "160 Quart Wheeled Stacker Extra Large Storage Box",
            "category_path": "Home > Storage Boxes",
            "product_form": "extra-large storage box",
            "shape_score": "72",
            "shape_recommendation": "Shape opportunity",
        }
        update_shape_archive([row], self.root, "run-old")
        update_shape_archive([], self.root, "run-new")
        with (self.root / "shape_opportunity_library.csv").open(encoding="utf-8-sig", newline="") as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["archive_status"], "invalidated_by_rule")
        self.assertIn("excluded title term", saved[0]["archive_notes"])

    # --- weekly report reject reasons -------------------------------------

    def test_seed_reject_reason_ignores_informational_flags(self):
        self.assertEqual(
            seed_reject_reason({"key_flags": "supplier quote required; estimated profit is not a rejection gate", "opportunity_score": "44.0"}),
            "score below watch threshold",
        )
        self.assertEqual(
            seed_reject_reason({"key_flags": "supplier quote required; review count above 600"}),
            "review count above 600",
        )
        self.assertEqual(seed_reject_reason({"hard_stop_reason": "excluded title term: vacuum", "key_flags": "x"}), "excluded title term: vacuum")
        self.assertEqual(seed_reject_reason({"brand_moat_reason": "brand moat: OXO"}), "brand moat: OXO")


if __name__ == "__main__":
    unittest.main()
