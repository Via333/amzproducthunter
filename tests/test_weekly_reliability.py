import csv
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import http.client
from workflow_storage import (atomic_write_text, current_root, prepare_generation,
                              publish_generation, run_quality, workflow_lock, atomic_write_csv)
from sorftime_mcp_client import SorftimeMcpClient
from category_shape_validation import update_shape_archive


class WeeklyReliabilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "config").mkdir()
        for name, text in [("web/index.html", "old page"), ("data/category_shape_validation.csv", "old data"),
                           ("archive/shape_opportunity_library.csv", "old pool")]:
            atomic_write_text(self.root / name, text)

    def manifest(self, successful=100):
        return {"run_id": "fixture", "selected_categories": 100, "successful_categories": successful,
                "failed_categories": 100-successful, "products_examined": successful*100,
                "data_provider": "mcp", "status": "success"}

    def test_quality_counts_unattempted_and_empty_categories(self):
        m = self.manifest(31)
        m["failed_categories"] = 5
        self.assertFalse(run_quality(m)["publishable"])
        self.assertEqual(run_quality(m)["missing_categories"], 69)
        self.assertTrue(run_quality(self.manifest(80))["publishable"])

    def test_partial_batch_cannot_touch_current_outputs(self):
        generation = prepare_generation(self.root, "partial")
        atomic_write_text(generation / "web/index.html", "bad batch")
        with self.assertRaises(ValueError):
            publish_generation(self.root, generation, self.manifest(30))
        self.assertEqual(current_root(self.root), self.root)
        for name, text in [("web/index.html", "old page"), ("data/category_shape_validation.csv", "old data"),
                           ("archive/shape_opportunity_library.csv", "old pool")]:
            self.assertEqual((self.root / name).read_text(), text)

    def test_complete_batch_switch_and_failed_next_publication(self):
        first = prepare_generation(self.root, "first")
        publish_generation(self.root, first, self.manifest())
        second = prepare_generation(self.root, "second")
        real_replace = os.replace
        def fail_pointer(source, target):
            if str(target).endswith("current_generation.json"):
                raise OSError("simulated crash")
            real_replace(source, target)
        with patch("workflow_storage.os.replace", side_effect=fail_pointer):
            with self.assertRaises(OSError):
                publish_generation(self.root, second, self.manifest())
        self.assertEqual(current_root(self.root), first)

    def test_replay_and_duplicate_run_ids_never_publish(self):
        generation = prepare_generation(self.root, "replay")
        with self.assertRaises(FileExistsError):
            prepare_generation(self.root, "replay")
        with self.assertRaises(ValueError):
            publish_generation(self.root, generation, {**self.manifest(), "data_provider": "mcp_replay"})
        self.assertEqual(current_root(self.root), self.root)

    def test_lock_released_after_exception(self):
        with self.assertRaises(RuntimeError):
            with workflow_lock(self.root):
                with workflow_lock(self.root):
                    pass
        with workflow_lock(self.root):
            pass

    def test_archive_retains_unscanned_and_rule_blocked(self):
        rows = [{"category_path": "Tools > Organizers", "product_form": "cable clip",
                 "shape_recommendation": "Shape opportunity", "shape_score": 80,
                 "data_quality": "category_top100", "source_category_id": "A"}]
        directory = self.root / "pool"
        update_shape_archive(rows, directory, "first")
        update_shape_archive([], directory, "second")
        with (directory / "shape_opportunity_library.csv").open() as f:
            result = list(csv.DictReader(f))[0]
        self.assertEqual(result["archive_status"], "active")
        self.assertEqual(result["last_validated_run_id"], "first")
        result["archive_status"] = "ip_review_required"
        atomic_write_csv(directory / "shape_opportunity_library.csv", [result], list(result))
        update_shape_archive(rows, directory, "third")
        with (directory / "shape_opportunity_library.csv").open() as f:
            self.assertEqual(list(csv.DictReader(f))[0]["archive_status"], "ip_review_required")


class McpRetryTest(unittest.TestCase):
    def client(self):
        with patch.dict(os.environ, {"AMZ_OFFLINE": "0"}):
            return SorftimeMcpClient(url="https://fixture.invalid", max_attempts=3)

    @patch("sorftime_mcp_client.time.sleep")
    def test_transient_read_disconnect_retries_bounded(self, sleep):
        client = self.client()
        with patch("urllib.request.urlopen", side_effect=http.client.RemoteDisconnected("secret")):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts") as error:
                client._post({"method": "tools/call"})
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(client.request_attempt_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("sorftime_mcp_client.time.sleep")
    def test_auth_failure_not_retried(self, sleep):
        client = self.client()
        error = urllib.error.HTTPError("https://secret", 401, "secret", {}, io.BytesIO(b"secret"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                client._post({})
        self.assertEqual(client.request_attempt_count, 1)
        sleep.assert_not_called()

    @patch("sorftime_mcp_client.time.sleep")
    def test_rate_limit_respects_retry_after(self, sleep):
        client = self.client()
        error = urllib.error.HTTPError("https://fixture", 429, "busy", {"Retry-After": "7"}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError):
                client._post({})
        self.assertEqual(sleep.call_args.args, (7.0,))

    def test_offline_guard(self):
        with patch.dict(os.environ, {"AMZ_OFFLINE": "1"}):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                SorftimeMcpClient(url="https://fixture.invalid")
