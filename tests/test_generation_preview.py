import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import build_dashboard
from preview_service import make_server
from workflow_storage import atomic_write_text


class GenerationPreviewTest(unittest.TestCase):
    def test_serves_committed_generation_and_blocks_symlink_after_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            generation = root / ".local/weekly_generations/test"
            atomic_write_text(root / "web/index.html", "old")
            atomic_write_text(root / "secret.txt", "private")
            atomic_write_text(generation / "web/index.html", "new")
            atomic_write_text(generation / "generation.json", "{}")
            atomic_write_text(root / ".local/current_generation.json", json.dumps({"root":str(generation.relative_to(root))}))
            (generation / "web/leak.txt").symlink_to(root / "secret.txt")
            server = make_server(root, port=0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            try:
                connection.request("GET", "/")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"new")
                connection.request("GET", "/leak.txt")
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertNotIn(b"private", response.read())
            finally:
                connection.close()
                server.shutdown()
                thread.join()
                server.server_close()

    def test_manual_research_reads_newer_workspace_but_weekly_data_is_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            generation = source / "generation"
            atomic_write_text(source / "research/EXAMPLE/summary.json", "{}")
            with patch.object(build_dashboard, "SOURCE_ROOT", source), patch.object(build_dashboard, "ROOT", generation), \
                 patch.dict("os.environ", {}, clear=True):
                self.assertEqual(build_dashboard.data_path("research/EXAMPLE/summary.json"), source / "research/EXAMPLE/summary.json")
                self.assertEqual(build_dashboard.data_path("data/category_shape_validation.csv"), generation / "data/category_shape_validation.csv")
