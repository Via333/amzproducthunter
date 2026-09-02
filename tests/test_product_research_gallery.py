import unittest

from build_product_research_pages import render_gallery, render_gallery_form_options


class ProductResearchGalleryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.products = [
            {
                "asin": "DIRECT01",
                "title": "Flexible Magnetic Tool Mat",
                "listing_url": "https://example.com/direct",
                "product_form": "flexible magnetic tool mat set",
                "material": "PVC leather",
                "pack_count": "3",
            },
            {
                "asin": "TRAY01",
                "title": "Rigid Magnetic Parts Tray",
                "listing_url": "https://example.com/tray",
                "product_form": "rigid magnetic parts tray",
                "material": "steel",
                "pack_count": "1",
            },
        ]

    def test_gallery_cards_include_filter_metadata(self) -> None:
        html = render_gallery(self.products)

        self.assertIn('data-form="flexible magnetic tool mat set"', html)
        self.assertIn('data-search="direct01 flexible magnetic tool mat', html)
        self.assertIn('data-form="rigid magnetic parts tray"', html)

    def test_form_options_are_unique_and_keep_product_order(self) -> None:
        html = render_gallery_form_options([self.products[0], self.products[0], self.products[1]])

        self.assertEqual(html.count("flexible magnetic tool mat set"), 2)
        self.assertLess(html.index("flexible magnetic tool mat set"), html.index("rigid magnetic parts tray"))


if __name__ == "__main__":
    unittest.main()
