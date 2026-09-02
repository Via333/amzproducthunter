import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from build_dashboard import dashboard_product_image, web_path


class DashboardWebPathTest(unittest.TestCase):
    def test_web_assets_are_relative_to_the_published_web_root(self):
        self.assertEqual(web_path("web/category/553582.html"), "category/553582.html")
        self.assertEqual(
            web_path("web/assets/reports/category_deep_research_553582.md"),
            "assets/reports/category_deep_research_553582.md",
        )

    def test_non_web_artifacts_remain_reachable_from_local_file_dashboard(self):
        self.assertEqual(web_path("reports/example.md"), "../reports/example.md")

    def test_dashboard_product_image_uses_published_asset_and_skips_missing_file(self):
        with TemporaryDirectory() as temp_dir, patch("build_dashboard.WEB_DIR", Path(temp_dir)):
            asset = Path(temp_dir) / "research" / "assets" / "B0TEST123" / "01_B0TEST123.jpg"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"image")
            row = {"asin": "B0TEST123", "image_file": "research/B0TEST123/images/01_B0TEST123.jpg"}
            self.assertEqual(
                dashboard_product_image(row),
                "research/assets/B0TEST123/01_B0TEST123.jpg",
            )
            self.assertEqual(
                dashboard_product_image({"asin": "B0MISS123", "image_file": "research/missing.jpg"}),
                "",
            )


if __name__ == "__main__":
    unittest.main()
