import unittest

from product_taxonomy import classify_product_form


class TofuPressTaxonomyTests(unittest.TestCase):
    def test_steel_press_not_generic_strainer(self):
        for title in (
            "Tofu Press 304 Stainless Steel, Paneer Maker with Upgraded Deep-Press",
            "Tofu Press Stainless Steel Tofu Press & Paneer Maker",
            "Tofu Press, SUS304 Paneer Maker, Stainless Steel Texture",
        ):
            self.assertEqual(
                classify_product_form(title, "Cheese Makers", "FOOD STRAINER"),
                "stainless steel tofu press",
            )

    def test_different_products_remain_separate(self):
        self.assertEqual(
            classify_product_form("Tofu Press Mould, 2PCS Food Grade Plastic", "Cheese Makers"),
            "tofu press mould",
        )
        for title in ("Cotton Cheesecloth for Straining", "Plastic Cheese Making Basket"):
            self.assertNotEqual(
                classify_product_form(title, "Cheese Makers", "FOOD STRAINER"),
                "stainless steel tofu press",
            )


if __name__ == "__main__":
    unittest.main()
