import argparse
from contextlib import closing
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from keyword_opportunity_search import search_responses, run_search, search_candidate
from preview_service import make_server, ResearchWorker
from research_workbench import Workbench


class KeywordExecutionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Workbench(self.root)

    def test_duplicate_keywords_and_request_retries(self):
        first = self.store.enqueue_keyword(" Rolling   Pin ", "request-000000001")
        duplicate = self.store.enqueue_keyword("rolling pin", "request-000000002")
        self.assertEqual(first["id"], duplicate["id"])
        self.store.finish(first["id"], "completed", result={"rows": [], "summary": {}})
        self.assertEqual(first["id"], self.store.enqueue_keyword("Rolling Pin", "request-000000001")["id"])
        self.assertNotEqual(first["id"], self.store.enqueue_keyword("Rolling Pin", "request-000000003")["id"])
        with self.assertRaises(ValueError):
            self.store.enqueue_keyword("Another keyword", "request-000000001")
        self.assertEqual(self.store.state()["jobs"], [])
        self.assertEqual(len(self.store.state()["keyword_jobs"]), 2)

    def test_research_and_search_share_one_serial_queue(self):
        research = self.store.enqueue("B0FRD3NQQG")
        keyword = self.store.enqueue_keyword("Rolling Pin", "request-000000001")
        self.assertEqual(self.store.claim()["id"], research["id"])
        self.assertIsNone(self.store.claim())
        self.store.finish(research["id"], "completed")
        self.assertEqual(self.store.claim()["id"], keyword["id"])
        self.assertIsNone(self.store.claim())
        self.store.set_pid(keyword["id"], None)
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.state()["keyword_jobs"][0]["status"], "interrupted")

    def test_distinct_keywords_queue_and_cancel(self):
        first = self.store.enqueue_keyword("Rolling Pin", "request-000000001")
        second = self.store.enqueue_keyword("Cake Scraper", "request-000000002")
        self.store.cancel(first["id"])
        self.assertEqual(self.store.claim()["id"], second["id"])

    def test_invalid_keywords(self):
        for keyword in [None, {}, [], "", "   ", "a" * 201, "bad\nkeyword", "bad\x00keyword"]:
            with self.subTest(keyword=keyword), self.assertRaises(ValueError):
                self.store.enqueue_keyword(keyword, "request-000000001")
        for request_id in [None, "", "short", "x" * 81, "./invalid-request-id"]:
            with self.assertRaises(ValueError):
                self.store.enqueue_keyword("Rolling Pin", request_id)

    def test_parallel_keywords_deduplicate(self):
        threads = [threading.Thread(target=self.store.enqueue_keyword, args=("Rolling Pin", f"request-{i:09}")) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.store.state()["keyword_jobs"]), 1)

    def test_old_database_migrates_without_losing_jobs(self):
        legacy = self.root / "legacy"
        (legacy / ".local").mkdir(parents=True)
        with closing(sqlite3.connect(legacy / ".local/research-workbench.sqlite3")) as db:
            db.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY, asin TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', pid INTEGER)")
            db.execute("INSERT INTO jobs(asin,title,status,created,updated) VALUES('B0FRD3NQQG','Saved','queued','before','before')")
            db.commit()
        store = Workbench(legacy)
        self.assertEqual(store.state()["jobs"][0]["title"], "Saved")
        self.assertEqual(Workbench(legacy).state()["jobs"][0]["status"], "queued")
        store.enqueue_keyword("Rolling Pin", "request-000000001")
        self.assertEqual(store.claim()["kind"], "research")

    def test_keyword_worker_uses_argument_list_and_publishes_fixture(self):
        keyword = 'Rolling Pin; "quoted"'
        job = self.store.enqueue_keyword(keyword, "request-000000001")
        worker = ResearchWorker(self.store)
        result = {"summary": {"keyword": keyword, "raw_result_count": 2, "eligible_candidate_count": 0}, "rows": []}

        def complete(timeout):
            directory = self.root / "web/keyword/rolling-pin-quoted" / f"job-{job['id']}"
            directory.mkdir(parents=True)
            (directory / "results.json").write_text(json.dumps(result))
            return 0

        with patch("preview_service.subprocess.Popen") as start:
            process = start.return_value
            process.pid = None
            process.wait.side_effect = complete
            thread = threading.Thread(target=worker.run)
            thread.start()
            try:
                for _ in range(100):
                    if self.store.state()["keyword_jobs"][0]["status"] == "completed":
                        break
                    time.sleep(.01)
            finally:
                worker.stop.set()
                thread.join(timeout=3)
            start.assert_called_once()
            self.assertIn("keyword_opportunity_search.py", start.call_args.args[0])
            self.assertIn(keyword, start.call_args.args[0])
            self.assertNotIn("shell", start.call_args.kwargs)
        self.assertEqual(self.store.keyword_result(job["id"]), result)
        self.assertEqual(Workbench(self.root).keyword_result(job["id"]), result)

    def test_http_keyword_security_and_results(self):
        server = make_server(self.root, port=0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        port = server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

        def request(method, path, headers=None, body=None):
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())

        try:
            _, state = request("GET", "/api/workbench")
            headers = {"Content-Type":"application/json", "Origin":f"http://127.0.0.1:{port}", "X-Workbench-Token":state["token"]}
            body = json.dumps({"keyword":"Rolling Pin", "request_id":"request-000000001"})
            for invalid in [{"Origin":"null"}, {"Origin":"https://evil.example"}, {"X-Workbench-Token":"wrong"}, {"Sec-Fetch-Site":"cross-site"}]:
                self.assertEqual(request("POST", "/api/keyword-search", {**headers, **invalid}, body)[0], 403)
            self.assertEqual(server.store.state()["keyword_jobs"], [])
            status, job = request("POST", "/api/keyword-search", headers, body)
            self.assertEqual(status, 200)
            self.assertEqual(request("POST", "/api/keyword-search", headers, body)[1]["id"], job["id"])
            path = f"/api/keyword-search/results?job={job['id']}"
            self.assertEqual(request("GET", path)[0], 404)
            server.store.finish(job["id"], "completed", result={"summary":{}, "rows":[]})
            self.assertEqual(request("GET", path)[0], 200)
            self.assertEqual(request("GET", path, {"Origin":"https://evil.example"})[0], 403)
        finally:
            connection.close()
            server.shutdown()
            thread.join()
            server.server_close()

    def test_mcp_pages_keep_evidence_and_stop_duplicate_page(self):
        args = argparse.Namespace(pages=None, from_json=[], keyword="Rolling Pin")
        rules = {"transport":"mcp", "pages":5, "max_results":100}
        rows = [{"asin":"B0FRD3NQQG", "title":"Rolling Pin", "price":20}]
        with patch("keyword_opportunity_search.SorftimeMcpClient") as client:
            client.return_value.call_tool.side_effect = [rows, rows]
            result = search_responses(args, rules, self.root)
            self.assertEqual(len(result), 2)
            self.assertEqual(client.return_value.call_tool.call_count, 2)
            client.return_value.call_tool.assert_called_with("product_search_from_name", {"name":"Rolling Pin", "amz_site":"US", "page":2})
        self.assertEqual(json.loads((self.root / "response_page_1.json").read_text()), rows)

    def test_error_payload_is_not_zero_results(self):
        args = argparse.Namespace(pages=1, from_json=[], keyword="Rolling Pin")
        with patch("keyword_opportunity_search.SorftimeMcpClient") as client:
            client.return_value.call_tool.return_value = {"error":"quota exceeded"}
            with self.assertRaises(ValueError):
                search_responses(args, {"transport":"mcp"}, self.root)
        self.assertTrue((self.root / "response_page_1.json").is_file())

    def test_mcp_sales_and_category_are_not_dropped(self):
        defaults = json.loads(Path("config/import_defaults.json").read_text())
        candidate = search_candidate({"asin":"B0FRD3NQQG", "title":"Rolling Pin", "price":19.99,
                                      "monthly_sale_volume":650, "star_rating":4.5, "review_count":81,
                                      "top_category":["Kitchen & Dining", "kitchen"],
                                      "subcategory":[["Rolling Pins", "289724", 2, "20260907"]]}, defaults)
        self.assertEqual(candidate["est_monthly_sales"], 650)
        self.assertEqual(candidate["category"], "Kitchen & Dining > Rolling Pins")
        self.assertEqual(candidate["source_category_id"], "289724")

    def test_no_eligible_candidates_still_publishes_real_zero(self):
        response = self.root / "source.json"
        response.write_text(json.dumps([{"asin":"B0FRD3NQQG", "title":"Rolling Pin", "price":1, "sales":1, "review_count":9000}]))
        args = argparse.Namespace(keyword="Rolling Pin", run_id="fixture-1", archive_root=str(self.root / "runs"),
                                  publish_root=str(self.root / "published"), index=str(self.root / "index.csv"),
                                  rules="config/keyword_search_rules.json", defaults="config/import_defaults.json",
                                  discovery_rules="config/autodiscovery_rules.json", pages=1, from_json=[str(response)], no_dashboard=True)
        with patch("keyword_opportunity_search.SorftimeMcpClient") as client:
            run_search(args)
            client.assert_not_called()
        result = json.loads((self.root / "published/rolling-pin/fixture-1/results.json").read_text())
        self.assertEqual(result["summary"]["raw_result_count"], 1)
        self.assertEqual(result["summary"]["eligible_candidate_count"], 0)
        self.assertEqual(result["rows"], [])
        self.assertTrue((self.root / "published/rolling-pin/fixture-1/keyword_opportunity_report.md").is_file())


if __name__ == "__main__":
    unittest.main()
