import json
import unittest
from pathlib import Path

from category_shape_validation import build_rows_from_category_report, form_summary_rows, normalized_category_product, opportunity_thesis
from product_family import product_identity
from product_taxonomy import classify_product_form
from product_exclusions import hard_exclusion_reason


class TitleProductNamesTest(unittest.TestCase):
    def test_decorating_tweezers_and_broad_supplies_stay_separate(self):
        for title in ("4PCS Sprinkles Tools For Cake Tweezers For Decorating",
                      "Sprinkle Tool for Cookie Tweezers for Decorating",
                      "Cake Decorating Tweezers", "Tweezers for Cake Decorating"):
            with self.subTest(title=title):
                row = product_identity(title, form="kitchen tools")
                self.assertEqual(row["product_family"], "decorating_tweezers")
                self.assertEqual(row["classification_status"], "needs_specification")
                self.assertEqual(classify_product_form(title, product_type="KITCHEN TOOLS"),
                                 row["product_form"])
        for title in ("Cookie Decorating Supplies", "Sprinkles Tools for Decorating",
                      "Sprinkles Tools with Cake Tweezers", "Case for Cake Tweezers",
                      "Cake Tweezers Case", "Eyebrow Tweezers"):
            with self.subTest(title=title):
                self.assertEqual(product_identity(title, form="kitchen tools")["classification_status"], "unresolved")
        row = product_identity("Cookie Decorating Supplies", form="kitchen tools")
        explanation = opportunity_thesis({**row, "shape_recommendation": "Needs classification"})
        self.assertIn("规则漏识别", explanation)
        self.assertIn("标题过于宽泛", explanation)
        self.assertNotIn("标题证据不足", explanation)
        for title in ("4Pcs Stainless Steel Cake Decoration Tools Set, 4 Claw Sprinkle Pen Pearl Grabber Cake Tweezers Pearl Sprinkle Spoon with Shovel Head for Cookie, Cupcake, and Dessert Artistry",
                      "5Pcs Stainless Steel Cake Decoration Tools Set, Cake Decorating Tweezers, Jewelry Making Tweezers, Pearl Grabber, Sprinkles Pen, Shovel Head Spoon for Cookie, Cupcake, and Dessert Artistry"):
            with self.subTest(title=title):
                self.assertEqual(product_identity(title)["product_family"], "cake_decorating_kit")

    def test_wrong_provider_labels_do_not_override_title(self):
        cases = [
            ("Dolly Parton Butterfly Cooling Rack in Blush - Carbon Steel Nonstick Wire Rack", "FURNITURE CART", "cooling_rack"),
            ("3/4 HP Corded Garbage Disposal, Continuous Feed Kitchen Disposer", "FUNNEL", "garbage_disposal"),
            ("INSINKERATOR Garbage Disposal Putty-Free Rubber Sink Seal, FLG-Seal", "GASKETS", "sink_seal"),
            ("Plastic Concrete Forms for Walkway, Concrete Edging Curb Molds", "GARDEN EDGING", "concrete_mold"),
            ("Kitchen Faucets with Deck Plate, Stainless Steel Pull Down Kitchen Sink Faucets", "FAUCET", "kitchen_faucet"),
            ("Bellemain Stainless Steel Flour Sifter for Baking with Hand Crank", "FOOD STRAINER", "flour_sifter"),
            ("Ateco Disposable Decorating Bags, 18-Inch, Pack of 100", "PIPING TIP", "piping_bag"),
            ("Piping Bags and Tips Set, Cake Decorating Kit Supplies", "PIPING TIP", "piping_kit"),
            ("Sandwich Cutter and Sealer, Cookie Cutter for Kids Lunch", "COOKIE CUTTER", "sandwich_cutter"),
            ("Amazon Basics Microfiber Cleaning Cloths, Reusable and Washable", "UTILITY SPONGE", "cleaning_cloth"),
            ("Scotch-Brite Heavy Duty Scrub Sponges, 6-Pack", "HEALTH PERSONAL CARE", "cleaning_sponge"),
            ("Stainless Steel Offset Icing Spatulas, Cake Decorating", "COOKIE CUTTER", "icing_spatula"),
            ("Schneider Medical Nitrile Exam Gloves, Disposable", "PROTECTIVE GLOVE", "disposable_glove"),
            ("JULBEAR Halloween Cookie Stencils, Reusable", "TEMPLATE STENCIL", "decorating_stencil"),
            ("Cotton Cheese Cloth for Straining", "THREAD CORD", "cheesecloth"),
            ("Toilet Aids for Wiping Bottom, Long Reach Butt Wiper", "TOILET PAPER", "toilet_wiping_aid"),
        ]
        for title, vendor, expected in cases:
            with self.subTest(title=title):
                row = normalized_category_product({"asin": "TEST", "title": title, "product_category": vendor}, "Home > Mixed")
                self.assertEqual(row["product_family"], expected)
                self.assertNotEqual(row["classification_status"], "unresolved")
                self.assertEqual(classify_product_form(title, product_type=vendor), row["product_form"])

    def test_synonyms_and_plurals_share_product_identity(self):
        for titles in (
            ("Stainless Steel Flour Sifter", "Baking Flour Sifters", "Flour Sieve for Baking"),
            ("Kitchen Faucet", "Kitchen Faucets", "Kitchen Sink Tap"),
            ("Rubber Sink Seal", "Rubber Sink Gaskets", "Sink Seals"),
            ("Bottle Pourer Spout", "Liquor Bottle Pourers", "Oil Pour Spouts"),
            ("Piping Tip", "Icing Piping Tips", "Cake Decorating Nozzles"),
            ("Cooling Rack", "Cooling Racks", "Wire Baking Rack"),
        ):
            with self.subTest(titles=titles):
                identities = [product_identity(t) for t in titles]
                self.assertTrue(all(p["classification_status"] != "unresolved" for p in identities))
                self.assertEqual(len({p["product_family"] for p in identities}), 1)

    def test_host_accessory_and_usage_context_stay_separate(self):
        cases = [
            ("Replacement Filters for Handheld Shower Head", "shower_filter_cartridge"),
            ("Shower Head Gasket, Rubber Washer with Wire Mesh", "shower_seal"),
            ("Shower Head Holder for Handheld Showerheads", "shower_holder"),
            ("Garbage Disposal Splash Guard for Waste King Garbage Disposals", "disposal_splash_guard"),
            ("Replacement Beaters for KitchenAid Stand Mixer", "mixer_beater"),
            ("Stand Mixer with Beaters and Whisk", "electric_mixer"),
            ("Replacement Lid for Cocktail Shaker", "shaker_lid"),
            ("Straw Hole Puncher Set with Muddler and Jigger", "straw_puncher"),
            ("Cooling Rack with Baking Pan", "cooling_rack"),
            ("Baking Pan with Cooling Rack", "baking_pan"),
            ("Bathroom Sink Drain Stopper", "drain_stopper"),
            ("Sink Drain Strainer", "drain_strainer"),
            ("Garbage Disposal & Sink Seal Set", "garbage_disposal"),
            ("Garbage Disposal Rocker Wall Switch", "disposal_switch"),
            ("Garbage Disposal Wireless Switch Kit", "disposal_switch"),
            ("Garbage Disposal 75257 Magnetic Stopper", "disposal_stopper"),
            ("Garbage Disposal Strainer and Stopper", "disposal_strainer"),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(product_identity(title)["product_family"], expected)
        for title in ("Case for Kitchen Faucet", "Kitchen Faucet Case", "Replacement Part for Garbage Disposal", "Garbage Disposal Parts for Insinkerator", "Model 100", "", "Universal Premium Kit"):
            with self.subTest(title=title):
                self.assertEqual(product_identity(title, "Home > Faucets", "faucet")["classification_status"], "unresolved")

    def test_known_use_without_critical_spec_is_not_unknown_use(self):
        row = product_identity("Kitchen Faucet")
        self.assertEqual(row["product_family"], "kitchen_faucet")
        self.assertEqual(row["classification_status"], "needs_specification")
        self.assertNotIn("用途待确认", row["product_family_label"])
        self.assertNotEqual(product_identity("Japanese Jigger")["classification_status"], "unresolved")

    def test_material_words_and_original_subtypes_are_preserved(self):
        self.assertIn("stainless steel", product_identity("Stainless Steel Japanese Jiggers")["product_form"])
        self.assertEqual(product_identity("Floating Fishing Net for Bass, Foldable Landing Net")["product_family"], "landing_net")
        self.assertNotEqual(product_identity("Kids Fishing Net with Butterfly Net")["product_family"], "landing_net")

    def test_recognition_does_not_bypass_exclusions_or_pending_gate(self):
        rules = json.loads(Path("config/category_shape_validation_rules.json").read_text())
        rules["_scoring_rules"] = json.loads(Path("config/scoring_rules.json").read_text())
        for title, expected in (("3/4 HP Corded Garbage Disposal", "Reject category/form"),
                                ("Kitchen Faucet", "Needs classification")):
            raw = [{"asin": f"PRODUCT{i}", "title": title, "product_category": "FUNNEL",
                    "monthly_sales_volume": 1500, "review_count": 20, "price": 30,
                    "brand": f"Brand {i}"} for i in range(5)]
            rows = build_rows_from_category_report({"source_category_path": "Home > Mixed"},
                                                  {"data": {"top100_products": raw}}, rules)
            self.assertEqual(rows[0]["shape_recommendation"], expected)

    def test_machine_risk_does_not_spread_to_passive_parts(self):
        for title in ("Garbage Disposal Rubber Sink Seal", "Garbage Disposal Splash Guard",
                      "Garbage Disposal Flange", "Replacement Parts for Garbage Disposals"):
            reason = hard_exclusion_reason({"product_name": title, "category": "Home > Parts"})
            self.assertNotIn("garbage_disposal_equipment", reason)

    def test_included_parts_do_not_hide_the_machine_risk(self):
        for title in ("Garbage Disposal with Air Switch", "Garbage Disposal & Antimicrobial Flange",
                      "Garbage Disposal with Plug", "1/2 hp in Sink Disposer"):
            self.assertEqual(product_identity(title)["product_family"], "garbage_disposal")
            reason = hard_exclusion_reason({"product_name": title, "category": "Home > Disposals"})
            self.assertIn("garbage_disposal_equipment", reason)

    def test_mixed_specification_evidence_is_order_independent(self):
        rows = [normalized_category_product({"asin": str(i), "title": title}, "Home > Drains")
                for i, title in enumerate(("Sink Strainer", "Sink Hair Catcher"))]
        self.assertEqual(len({p["product_form"] for p in rows}), 1)
        for order in (rows, list(reversed(rows))):
            summary = form_summary_rows(order)[0]
            self.assertEqual(summary["classification_status"], "needs_specification")


if __name__ == "__main__":
    unittest.main()
