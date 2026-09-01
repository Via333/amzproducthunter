#!/usr/bin/env python3
"""Offline tests for the Sorftime Streamable HTTP MCP adapter."""

from __future__ import annotations

import json
import unittest

from discover_sorftime_opportunities import extract_category_report_products
from sorftime_mcp_client import parse_http_payload, unpack_tool_result


class SorftimeMcpClientTest(unittest.TestCase):
    def test_parses_streamable_http_sse_response(self) -> None:
        body = b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n\n'
        parsed = parse_http_payload(body, "text/event-stream")
        self.assertTrue(parsed["result"]["ok"])

    def test_unpacks_json_text_tool_result(self) -> None:
        payload = {"data": {"top100_products": [{"asin": "A"}]}}
        response = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
        }
        self.assertEqual(unpack_tool_result(response), payload)

    def test_extracts_top100_products_without_recursive_guessing(self) -> None:
        report = {"doc": {}, "data": {"top100_products": [{"asin": "A"}, {"asin": "B"}]}}
        self.assertEqual([row["asin"] for row in extract_category_report_products(report)], ["A", "B"])

    def test_tool_error_does_not_leak_transport_details(self) -> None:
        response = {
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "Insufficient request quota"}],
            }
        }
        with self.assertRaisesRegex(RuntimeError, "Insufficient request quota"):
            unpack_tool_result(response)


if __name__ == "__main__":
    unittest.main()
