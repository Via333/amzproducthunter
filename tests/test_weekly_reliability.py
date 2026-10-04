import csv
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import http.client
import json
import sys
from unittest.mock import MagicMock
from workflow_storage import (atomic_write_text, current_root, prepare_generation,
                              publish_generation, run_quality, workflow_lock, atomic_write_csv)
from sorftime_mcp_client import SorftimeMcpClient
from category_shape_validation import update_shape_archive
import discover_sorftime_opportunities as discovery
from weekly_scan_observability import latest_daily_shape_counts


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
        publish_generation(self.root, first, {**self.manifest(), "run_id": "first"})
        second = prepare_generation(self.root, "second")
        real_replace = os.replace
        def fail_pointer(source, target):
            if str(target).endswith("current_generation.json"):
                raise OSError("simulated crash")
            real_replace(source, target)
        with patch("workflow_storage.os.replace", side_effect=fail_pointer):
            with self.assertRaises(OSError):
                publish_generation(self.root, second, {**self.manifest(), "run_id": "second"})
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

    def test_failed_attempts_survive_without_advancing_success_or_replays(self):
        fields = ["category_id", "status", "last_scanned_at", "scan_count", "consecutive_failures", "last_attempt_at"]
        state = {"category_id":"A", "status":"scanned", "last_scanned_at":"2026-09-01", "scan_count":1}
        atomic_write_csv(self.root / "archive/category_scan_state.csv", [state], fields)
        for run_id, provider, failures in [("partial", "mcp", 3), ("replay", "mcp_replay", 99)]:
            directory = self.root / "archive/discovery_runs" / run_id
            atomic_write_text(directory / "run_manifest.json", json.dumps({"status":"partial", "data_provider":provider}))
            atomic_write_csv(directory / "category_scan_state.csv", [{**state, "status":"failed",
                "last_scanned_at":"2026-10-04", "scan_count":9, "consecutive_failures":failures,
                "last_attempt_at":"2026-10-04T08:00:00+08:00"}], fields)
        generation = prepare_generation(self.root, "next")
        with (generation / "archive/category_scan_state.csv").open() as f:
            result = next(csv.DictReader(f))
        self.assertEqual(result["consecutive_failures"], "3")
        self.assertEqual(result["last_scanned_at"], "2026-09-01")
        self.assertEqual(result["scan_count"], "1")

    def test_simulated_seventy_percent_failure_preserves_live_files(self):
        source = Path(discovery.__file__).resolve().parent
        categories = [{"category_id":str(i), "name":f"Storage {i}", "path":f"Tools > Storage {i}"} for i in range(10)]
        client = MagicMock(tool_call_count=10, request_attempt_count=10)
        results = [RuntimeError("temporary disconnect")] * 7 + [([], 10)] * 3
        argv = ["discover", "--strategy", "category", "--provider", "mcp", "--run-id", "failed_fixture",
                "--rules", str(source / "config/autodiscovery_rules.json"),
                "--defaults", str(source / "config/import_defaults.json"),
                "--category-exclusions", str(source / "config/category_exclusions.json")]
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.object(sys, "argv", argv), patch.object(discovery, "SorftimeMcpClient", return_value=client), \
                 patch.object(discovery, "load_category_tree", return_value=({}, "fixture")), \
                 patch.object(discovery, "select_categories", return_value=categories), \
                 patch.object(discovery, "collect_products_for_category_mcp", side_effect=results), \
                 patch.object(discovery, "load_rules", return_value={"network":{"circuit_breaker_consecutive_failures":100}}):
                with self.assertRaises(SystemExit):
                    discovery.main()
        finally:
            os.chdir(previous)
        manifest = json.loads((self.root / "archive/discovery_runs/failed_fixture/run_manifest.json").read_text())
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["failed_category_share"], .7)
        self.assertEqual((self.root / "data/category_shape_validation.csv").read_text(), "old data")
        self.assertEqual((self.root / "archive/shape_opportunity_library.csv").read_text(), "old pool")
        self.assertFalse((self.root / "archive/category_scan_state.csv").exists())

    def test_replay_and_bad_legacy_success_do_not_mask_successful_day(self):
        for run_id, success, provider in [("20260921_a", 100, "mcp"), ("20260921_z", 100, "mcp_replay"), ("20260928_a", 31, "mcp")]:
            atomic_write_csv(self.root / "archive/category_shape_runs" / run_id / "category_shape_validation.csv",
                             [{"shape_recommendation":"Watch shape"}], ["shape_recommendation"])
            atomic_write_text(self.root / "archive/discovery_runs" / run_id / "run_manifest.json",
                              json.dumps({**self.manifest(success), "data_provider":provider}))
        self.assertEqual([r["run_id"] for r in latest_daily_shape_counts(self.root)], ["20260921_a"])

    def test_five_failures_open_circuit_without_attempting_remaining_categories(self):
        source = Path(discovery.__file__).resolve().parent
        client = MagicMock(tool_call_count=5, request_attempt_count=5)
        client.preflight.side_effect = [None, RuntimeError("still offline")]
        categories = [{"category_id":str(i), "name":f"Storage {i}", "path":f"Tools > Storage {i}"} for i in range(20)]
        argv = ["discover", "--strategy", "category", "--provider", "mcp", "--run-id", "circuit_fixture",
                "--rules", str(source / "config/autodiscovery_rules.json"),
                "--defaults", str(source / "config/import_defaults.json"),
                "--category-exclusions", str(source / "config/category_exclusions.json")]
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.object(sys, "argv", argv), patch.object(discovery, "SorftimeMcpClient", return_value=client), \
                 patch.object(discovery, "load_category_tree", return_value=({}, "fixture")), \
                 patch.object(discovery, "select_categories", return_value=categories), \
                 patch.object(discovery, "collect_products_for_category_mcp", side_effect=RuntimeError("disconnect")) as collect, \
                 patch.object(discovery.time, "sleep") as sleep:
                with self.assertRaises(SystemExit):
                    discovery.main()
                self.assertEqual(collect.call_count, 5)
                sleep.assert_called_once_with(300)
        finally:
            os.chdir(previous)
        manifest = json.loads((self.root / "archive/discovery_runs/circuit_fixture/run_manifest.json").read_text())
        self.assertEqual(manifest["status"], "partial")
        self.assertTrue(manifest["circuit_breaker_open"])
        self.assertEqual(manifest["missing_categories"], 20)

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

    @patch("sorftime_mcp_client.time.sleep")
    def test_session_expiration_reinitializes_only_once(self, sleep):
        client = self.client()
        client._session_id = "expired"
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Type":"application/json"}
        response.read.return_value = b'{"result": {}}'
        expired = urllib.error.HTTPError("https://fixture", 404, "expired", {}, None)
        def renew():
            client._session_id = "new"
        with patch.object(client, "initialize", side_effect=renew) as initialize, \
             patch("urllib.request.urlopen", side_effect=[expired, response]) as request:
            self.assertEqual(client._post({"method":"tools/call"}), {"result":{}})
        initialize.assert_called_once()
        self.assertEqual(request.call_args.args[0].get_header("Mcp-session-id"), "new")

    @patch("sorftime_mcp_client.time.sleep")
    def test_malformed_payload_and_transport_errors_are_bounded(self, sleep):
        for error in [ConnectionResetError(), http.client.IncompleteRead(b""), ValueError("bad payload")]:
            client = self.client()
            response = MagicMock()
            response.__enter__.return_value = response
            response.headers = {"Content-Type":"application/json"}
            response.read.return_value = b'not-json'
            effect = response if isinstance(error, ValueError) else error
            with patch("urllib.request.urlopen", side_effect=[effect] * 3):
                with self.assertRaises(RuntimeError):
                    client._post({"method":"tools/call"})
            self.assertEqual(client.request_attempt_count, 3)
