import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from build_product_research_pages import render_gallery, render_gallery_form_options
from research_images import allowed_image_url, complete_images, gallery_products, image_urls, representative_products


class ResearchImagesTest(unittest.TestCase):
    def test_representative_selection_is_stable_and_prefers_cached_same_form_image(self):
        products = [
            {"asin": "B000000001", "product_form": "bottle", "competitor_type": "seed", "monthly_sales": 9000},
            {"asin": "B000000002", "product_form": "bottle", "main_image_url": "https://m.media-amazon.com/images/I/a.jpg"},
            {"asin": "B000000003", "product_form": "bottle", "image_file": "cached.jpg"},
            {"asin": "B000000004", "product_form": "whisk", "monthly_sales": "unknown"},
            {"asin": "B000000005", "product_form": "whisk", "monthly_sales": 20},
        ]
        for order in (products, list(reversed(products))):
            chosen = representative_products(order)
            self.assertEqual(chosen["bottle"]["asin"], "B000000003")
            self.assertEqual(chosen["whisk"]["asin"], "B000000005")

    @patch("research_images.time.sleep")
    def test_snapshot_representative_collection_fetches_one_per_original_form(self, _sleep):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = self.prepare(root)
            products = [{"asin": f"B{i:09d}", "product_form": "bottle" if i < 2 else "whisk",
                         "monthly_sales": 100 - i} for i in range(3)]
            with (directory / "top_products.csv").open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=products[0].keys())
                writer.writeheader()
                writer.writerows(products)
            before = (directory / "top_products.csv").read_bytes()
            client = Mock()
            client.call_tool.side_effect = lambda tool, args: {"data": {"asin": args["asin"],
                "main_image": "https://m.media-amazon.com/images/I/" + args["asin"] + ".jpg"}}
            def download(url, path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"image fixture")
            with patch("research_images.build_landscape") as current_landscape:
                result = complete_images(root, directory, scope="snapshot", representatives_only=True,
                                         client=client, downloader=download)
                again = complete_images(root, directory, scope="snapshot", representatives_only=True,
                                        client=client, downloader=download)
            current_landscape.assert_not_called()
            self.assertEqual(set(result["items"]), {"B000000000", "B000000002"})
            self.assertEqual(result["last_run"]["tool_calls"], 2)
            self.assertEqual(again["last_run"]["tool_calls"], 0)
            self.assertEqual((directory / "top_products.csv").read_bytes(), before)

    def test_snapshot_keeps_all_forms_without_images_or_current_membership(self):
        old = [{"asin": f"B{i:09d}", "product_form": f"snapshot-{i % 6}",
                "visual_product_form": "one-visual-form"} for i in range(100)]
        with tempfile.TemporaryDirectory() as temp:
            rows = gallery_products(old, self.landscape(3), root=Path(temp), scope="snapshot")
        self.assertEqual(len(rows), 100)
        self.assertEqual(len({r["product_form"] for r in rows}), 6)
        self.assertEqual(old[0]["product_form"], "snapshot-0")
        self.assertTrue(all(r["listing_url"].endswith(r["asin"]) for r in rows))
        self.assertEqual(render_gallery(rows).count("data-gallery-item"), 100)
        self.assertEqual(render_gallery_form_options(rows).count("<option"), 6)
        self.assertNotIn('data-form="one-visual-form"', render_gallery(rows))

    def test_snapshot_reuses_only_matching_asin_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "existing.jpg"
            image.write_bytes(b"test fixture")
            products = [{"asin": "B000000002", "product_form": "other"},
                        {"asin": "B000000001", "product_form": "target"}]
            rows = gallery_products(products, self.landscape(),
                {"items": {"B000000001": {"image_file": "existing.jpg"}}}, root, scope="snapshot")
            lookup = {row["asin"]: row for row in rows}
            self.assertEqual(lookup["B000000001"]["image_file"], "existing.jpg")
            self.assertEqual(lookup["B000000002"]["image_file"], "")

    def landscape(self, count=2):
        return {"seed_asin": "B000000000", "forms": [{
            "form": "cocktail jigger - stainless steel - japanese", "label": "日式不锈钢量杯",
            "products": [{"asin": f"B{i:09d}", "title": "Japanese Steel Jigger",
                          "monthly_sales": 100 - i} for i in range(count)]}]}

    def prepare(self, root, count=2):
        directory = root / "research" / "B000000000"
        directory.mkdir(parents=True)
        products = self.landscape(count)["forms"][0]["products"]
        with (directory / "top_products.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["asin", "title", "monthly_sales"])
            writer.writeheader()
            writer.writerows(products)
        return directory

    def test_gallery_has_all_forms_and_products_beyond_36(self):
        rows = gallery_products([], self.landscape(99))
        rows[-1]["product_form"] = "last-form"
        rows[-1]["product_form_label"] = "Last form"
        html = render_gallery(rows)
        self.assertEqual(html.count("data-gallery-item"), 99)
        self.assertIn("B000000098", html)
        self.assertIn('value="last-form"', render_gallery_form_options(rows))
        self.assertIn("待补图", html)

    def test_gallery_uses_current_groups_without_mutating_old_research(self):
        old = [{"asin": "B000000000", "product_form": "kitchen", "title": "2 Pack Japanese Jiggers"}]
        rows = gallery_products(old, self.landscape())
        self.assertEqual(old[0]["product_form"], "kitchen")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["product_form"], "cocktail jigger - stainless steel - japanese")
        self.assertEqual(rows[0]["pack_count"], 2)
        self.assertEqual(rows[1]["listing_url"], "https://www.amazon.com/dp/B000000001")

    def test_urls_accept_sorftime_json_array_but_not_arbitrary_hosts(self):
        url = "https://m.media-amazon.com/images/I/example.jpg"
        self.assertEqual(image_urls(json.dumps([url, url, "http://127.0.0.1/private"])), [url])
        self.assertFalse(allowed_image_url("https://media-amazon.com.evil.test/a.jpg"))
        self.assertTrue(allowed_image_url("https://images-na.ssl-images-amazon.com/images/I/a.jpg"))
        self.assertFalse(allowed_image_url("https://images-na.ssl-images-amazon.com.evil.test/a.jpg"))
        self.assertEqual(image_urls("https://m.media-amazon.com/images/I/a b.jpg"),
                         ["https://m.media-amazon.com/images/I/a%20b.jpg"])

    @patch("research_images.time.sleep")
    def test_missing_only_fetch_and_repeat_run_has_zero_calls(self, _sleep):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = self.prepare(root)
            local = directory / "images" / "01_B000000000.jpg"
            local.parent.mkdir()
            local.write_bytes(b"existing test image")
            client = Mock()
            client.call_tool.return_value = {"data": {"asin": "B000000001", "main_image":
                '["https://m.media-amazon.com/images/I/new.jpg"]'}}
            def download(url, path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"downloaded test image")
            before = (directory / "top_products.csv").read_bytes()
            with patch("research_images.build_landscape", return_value=self.landscape()):
                result = complete_images(root, directory, client=client, downloader=download)
                self.assertEqual(result["last_run"]["tool_calls"], 1)
                self.assertEqual(result["last_run"]["downloaded"], 1)
                again = complete_images(root, directory, client=client, downloader=download)
            self.assertEqual(again["last_run"]["tool_calls"], 0)
            self.assertEqual(client.call_tool.call_count, 1)
            self.assertEqual((directory / "top_products.csv").read_bytes(), before)

    @patch("research_images.time.sleep")
    def test_budget_is_enforced_and_empty_image_is_not_success(self, _sleep):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = self.prepare(root)
            client = Mock()
            client.call_tool.return_value = {"data": {"asin": "B000000000", "main_image": "[]"}}
            with patch("research_images.build_landscape", return_value=self.landscape()):
                result = complete_images(root, directory, max_calls=1, client=client)
            self.assertEqual(client.call_tool.call_count, 1)
            self.assertEqual(result["items"]["B000000000"]["status"], "no_image")
            self.assertEqual(result["items"]["B000000001"]["status"], "budget_deferred")

    @patch("research_images.time.sleep")
    def test_failed_download_is_retained_and_does_not_auto_retry(self, _sleep):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = self.prepare(root, 1)
            client = Mock()
            client.call_tool.return_value = {"data": {"asin": "B000000000", "main_image":
                '["https://m.media-amazon.com/images/I/new.jpg"]'}}
            downloader = Mock(side_effect=RuntimeError("secret-url?key=DO_NOT_LOG"))
            with patch("research_images.build_landscape", return_value=self.landscape(1)):
                result = complete_images(root, directory, client=client, downloader=downloader)
                complete_images(root, directory, client=client, downloader=downloader)
            self.assertEqual(result["items"]["B000000000"]["status"], "download_failed")
            self.assertNotIn("DO_NOT_LOG", (directory / "image_manifest.json").read_text())
            self.assertEqual(client.call_tool.call_count, 1)
            self.assertEqual(downloader.call_count, 1)

    @patch("research_images.time.sleep")
    def test_alternate_product_image_is_used_without_another_mcp_call(self, _sleep):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = self.prepare(root, 1)
            client = Mock()
            first = "https://m.media-amazon.com/images/I/missing.jpg"
            second = "https://m.media-amazon.com/images/I/valid.jpg"
            client.call_tool.return_value = {"data": {"asin": "B000000000", "main_image": [first, second]}}
            def download(url, path):
                if url == first:
                    raise ValueError("missing image")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"alternate product image")
            with patch("research_images.build_landscape", return_value=self.landscape(1)):
                result = complete_images(root, directory, client=client, downloader=download)
            self.assertEqual(client.call_tool.call_count, 1)
            self.assertEqual(result["items"]["B000000000"]["main_image_url"], second)
            self.assertEqual(result["items"]["B000000000"]["status"], "ready")

    @patch("research_images.time.sleep")
    def test_three_service_errors_stop_batch(self, _sleep):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = self.prepare(root, 5)
            client = Mock()
            client.call_tool.side_effect = RuntimeError("quota exhausted")
            with patch("research_images.build_landscape", return_value=self.landscape(5)):
                result = complete_images(root, directory, client=client)
            self.assertEqual(client.call_tool.call_count, 3)
            self.assertEqual(result["last_run"]["processed"], 3)


if __name__ == "__main__":
    unittest.main()
