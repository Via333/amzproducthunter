import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from preview_service import make_server, ResearchWorker
from research_workbench import Workbench, opportunity_keys
from product_family import product_identity
from category_shape_validation import normalized_category_product, form_summary_rows, shape_archive_key


class FamilyTest(unittest.TestCase):
    def test_same_use_different_material_same_family(self):
        a = product_identity("Steel Cake Scraper", form="food spatula")
        b = product_identity("Clear Acrylic Cake Scraper", form="kitchen tools")
        self.assertEqual(a["product_family"], b["product_family"])
        self.assertNotEqual(a["product_form"], b["product_form"])
        self.assertNotEqual(shape_archive_key(a), shape_archive_key(b))
        self.assertTrue(a["product_form"].isascii())

    def test_sugar_duster_is_not_a_cleaning_duster(self):
        food = product_identity("Powdered Sugar Duster for Baking", form="food shaker")
        cleaning = product_identity("Duster Refills for Cleaning", form="duster refill")
        self.assertNotEqual(food["product_family"], cleaning["product_family"])

    def test_generic_type_does_not_merge_different_uses(self):
        a = product_identity("Flexible Dough Scraper & Cutter Set", form="kitchen tools")
        b = product_identity("2 PCS Cake Arch Guide Tool", form="kitchen tools")
        c = product_identity("Unclear accessory", form="kitchen tools")
        self.assertNotEqual(a["product_family"], b["product_family"])
        self.assertEqual(c["classification_status"], "unresolved")

    def test_each_shape_recalculates_its_own_members(self):
        rows = [normalized_category_product(p, "Cake Tools") for p in [
            {"asin": "A", "title": "Steel Cake Scraper", "monthly_sales_volume": 100},
            {"asin": "B", "title": "Plastic Cake Scraper", "monthly_sales_volume": 700},
            {"asin": "C", "title": "Cake Arch Guide Tool", "monthly_sales_volume": 900},
        ]]
        forms = form_summary_rows(rows)
        self.assertEqual(len(forms), 3)
        self.assertEqual(sorted(x["total_monthly_sales"] for x in forms), [100, 700, 900])


class WorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "web/research").mkdir(parents=True)
        (self.root / "archive").mkdir()
        self.store = Workbench(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_favorites_persist_without_queueing(self):
        self.store.favorite("b0fxg8j58q", True, "Example", "Tools")
        again = Workbench(self.root)
        self.assertEqual(again.state()["favorites"][0]["asin"], "B0FXG8J58Q")
        self.assertEqual(again.state()["jobs"], [])
        again.favorite("B0FXG8J58Q", False)
        self.assertEqual(again.state()["favorites"], [])

    def test_dismissal_persists_and_restores_without_touching_research(self):
        key = opportunity_keys({"product_family": "cocktail_jigger", "product_form": "jigger steel"})["shape"]
        self.store.favorite("B0FXG8J58Q", True, "Original")
        self.store.dismiss("shape", key, True, "Steel jigger")
        self.store.dismiss("shape", key, True, "Steel jigger")
        again = Workbench(self.root)
        self.assertEqual(len(again.state()["dismissals"]), 1)
        self.assertEqual(again.state()["dismissals"][0]["key"], key)
        self.assertEqual(len(again.state()["favorites"]), 1)
        self.assertEqual(again.state()["jobs"], [])
        again.dismiss("shape", key, False, "Steel jigger")
        self.assertEqual(again.state()["dismissals"], [])
        with again.connect() as db:
            self.assertEqual(db.execute("SELECT discarded FROM dismissals").fetchone()[0], 0)

    def test_dismissal_keys_ignore_source_and_scan_but_keep_siblings_distinct(self):
        first = {"product_family": "cocktail_jigger", "product_form": "jigger steel", "source_category_id": "1", "rank": 1}
        other_source = {**first, "source_category_id": "2", "rank": 99, "snapshot_id": "next_scan"}
        a, b = opportunity_keys(first), opportunity_keys(other_source)
        self.assertEqual(a, b)
        self.assertEqual(a["market"], "barware")
        sibling = opportunity_keys({**first, "product_form": "jigger plastic"})
        self.assertEqual(a["family"], sibling["family"])
        self.assertNotEqual(a["shape"], sibling["shape"])
        unrelated = opportunity_keys({"product_family": "kitchen_tool", "product_form": "jigger steel"})
        self.assertNotEqual(a["shape"], unrelated["shape"])

    def test_dismissal_validation(self):
        for kind, key, value in [("unknown", "x", True), ("family", "", True), ("family", "x", "yes"),
                                 ("family", "x" * 601, True), ("shape", "{}", True), ("shape", '["x",""]', True)]:
            with self.subTest(kind=kind, key=key[:30]), self.assertRaises(ValueError):
                self.store.dismiss(kind, key, value)
        self.assertEqual(self.store.state()["dismissals"], [])

    def test_duplicate_clicks_and_serial_claims(self):
        first = self.store.enqueue("B0FXG8J58Q")
        self.assertEqual(first["id"], self.store.enqueue("B0FXG8J58Q")["id"])
        second = self.store.enqueue("B0GSTNJKZF")
        self.assertEqual(self.store.claim()["id"], first["id"])
        self.assertIsNone(self.store.claim())
        self.store.finish(first["id"], "completed")
        self.assertEqual(self.store.claim()["id"], second["id"])

    def test_parallel_enqueue_is_idempotent(self):
        threads = [threading.Thread(target=self.store.enqueue, args=("B0FXG8J58Q",)) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.store.state()["jobs"]), 1)

    def test_cancel_retry_and_invalid_asin(self):
        job = self.store.enqueue("B0FXG8J58Q")
        self.store.cancel(job["id"])
        self.assertIsNone(self.store.claim())
        retry = self.store.enqueue("B0FXG8J58Q")
        self.assertNotEqual(job["id"], retry["id"])
        with self.assertRaises(ValueError):
            self.store.enqueue("; touch evil")

    def test_existing_report_does_not_consume_requests(self):
        (self.root / "web/research/B0FXG8J58Q.html").write_text("existing")
        (self.root / "archive/product_research_index.csv").write_text("asin\nB0FXG8J58Q\n")
        job = self.store.enqueue("B0FXG8J58Q")
        self.assertEqual(job["status"], "completed")
        self.assertEqual(self.store.state()["jobs"], [])

    def test_interrupted_job_is_not_silently_retried(self):
        job = self.store.enqueue("B0FXG8J58Q")
        self.store.claim()
        self.store.set_pid(job["id"], None)
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.state()["jobs"][0]["status"], "interrupted")

    def test_worker_publishes_result_from_fixture_only(self):
        job = self.store.enqueue("B0FXG8J58Q")
        worker = ResearchWorker(self.store)

        def complete(timeout):
            (self.root / "web/research/B0FXG8J58Q.html").write_text("fixture report")
            (self.root / "archive/product_research_index.csv").write_text("asin\nB0FXG8J58Q\n")
            return 0

        with patch("preview_service.subprocess.Popen") as start:
            process = start.return_value
            process.pid = None
            process.wait.side_effect = complete
            thread = threading.Thread(target=worker.run)
            thread.start()
            for _ in range(100):
                if self.store.state()["jobs"][0]["status"] == "completed":
                    break
                time.sleep(.01)
            worker.stop.set()
            thread.join(timeout=3)
            start.assert_called_once()
            self.assertIn("refresh_product_research.py", start.call_args.args[0])
        result = self.store.state()["jobs"][0]
        self.assertEqual(result["id"], job["id"])
        self.assertEqual(result["url"], "/research/B0FXG8J58Q.html")

    def test_http_origin_token_validation_and_persistence(self):
        server = make_server(self.root, port=0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        port = server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", "/api/workbench")
            state = json.loads(connection.getresponse().read())
            body = json.dumps({"asin": "B0FXG8J58Q", "saved": True})
            headers = {"Content-Type":"application/json", "Origin":f"http://127.0.0.1:{port}", "X-Workbench-Token":state["token"]}
            connection.request("POST", "/api/favorites", body, headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.request("POST", "/api/research", body, {**headers, "Origin":"https://evil.example"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            connection.request("POST", "/api/research", body, {**headers, "X-Workbench-Token":"invalid"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            self.assertEqual(len(server.store.state()["favorites"]), 1)
            self.assertEqual(server.store.state()["jobs"], [])
            dismissal = json.dumps({"kind": "family", "key": "faucet", "discarded": True, "label": "Faucet"})
            for custom, expected in [(headers, 200), ({**headers, "Origin": "https://evil.example"}, 403),
                                     ({**headers, "X-Workbench-Token": "invalid"}, 403)]:
                connection.request("POST", "/api/dismissals", dismissal, custom)
                response = connection.getresponse()
                self.assertEqual(response.status, expected)
                response.read()
            self.assertEqual(len(server.store.state()["dismissals"]), 1)
            self.assertEqual(server.store.state()["jobs"], [])
        finally:
            connection.close()
            server.shutdown()
            thread.join()
            server.server_close()

    def test_embedded_browser_portless_origin_and_cross_site_rejection(self):
        server = make_server(self.root, port=0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        port = server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

        def request(method, path, headers, body=None):
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())

        try:
            for host in ("127.0.0.1", "localhost"):
                with self.subTest(host=host):
                    browser = {"Host": f"{host}:{port}", "Origin": f"http://{host}",
                               "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}
                    status, state = request("GET", "/api/workbench", browser)
                    self.assertEqual(status, 200)
                    headers = {**browser, "Content-Type": "application/json", "X-Workbench-Token": state["token"]}
                    body = json.dumps({"asin": "B0FHKR8MMT"})
                    # This test server has no worker; no research or external call runs.
                    status, job = request("POST", "/api/research", headers, body)
                    self.assertEqual(status, 200)
                    self.assertEqual(job["status"], "queued")
                    status, duplicate = request("POST", "/api/research", headers, body)
                    self.assertEqual(status, 200)
                    self.assertEqual(job["id"], duplicate["id"])
                    for untrusted in [
                        {"Origin": "https://evil.example"},
                        {"Origin": "http://127.0.0.1.evil.example"},
                        {"Origin": "null"},
                        {"Origin": "http://localhost:1234"},
                        {"Origin": "http://127.0.0.1:1234"},
                        {"Origin": "http://" + ("localhost" if host == "127.0.0.1" else "127.0.0.1")},
                        {"Sec-Fetch-Site": "same-site"},
                        {"Sec-Fetch-Site": "cross-site"},
                        {"Sec-Fetch-Site": ""},
                        {"Host": "evil.example"},
                    ]:
                        with self.subTest(untrusted=untrusted):
                            self.assertEqual(request("GET", "/api/workbench", {**browser, **untrusted})[0], 403)
                            self.assertEqual(request("POST", "/api/research", {**headers, **untrusted}, body)[0], 403)
                    self.assertEqual(request("POST", "/api/research", {**headers, "X-Workbench-Token": "invalid"}, body)[0], 403)
                    self.assertEqual(request("POST", "/api/research", {**headers, "Origin": ""}, body)[0], 403)
            self.assertEqual(request("GET", "/api/workbench", {"Sec-Fetch-Site": "cross-site"})[0], 403)
            self.assertEqual(len(server.store.state()["jobs"]), 1)
        finally:
            connection.close()
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
