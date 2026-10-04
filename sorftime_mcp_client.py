#!/usr/bin/env python3
"""Minimal Streamable HTTP client for Sorftime MCP.

The MCP URL contains the account key. It is read from the user's Codex config
or an environment variable and is never written to project files or logs.
"""

from __future__ import annotations

import json
import http.client
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_SERVER_NAME = "sorftime-server"


def load_mcp_url(server_name: str = DEFAULT_SERVER_NAME) -> str:
    environment_url = os.environ.get("SORFTIME_MCP_URL", "").strip()
    if environment_url:
        return environment_url

    config_path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    if not config_path.exists():
        raise RuntimeError(
            "Sorftime MCP is not configured. Set SORFTIME_MCP_URL or add "
            f"mcp_servers.{server_name} to {config_path}."
        )
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    server = config.get("mcp_servers", {}).get(server_name, {})
    url = str(server.get("url") or "").strip()
    if not url:
        raise RuntimeError(f"Sorftime MCP URL is missing from {config_path}.")
    return url


def parse_http_payload(body: bytes, content_type: str = "") -> dict[str, Any]:
    text = body.decode("utf-8").strip()
    if not text:
        return {}
    if "text/event-stream" in content_type or text.startswith("event:") or text.startswith("data:"):
        messages = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            messages.append(json.loads(raw))
        if not messages:
            return {}
        return messages[-1]
    return json.loads(text)


def unpack_tool_result(response: dict[str, Any]) -> Any:
    if response.get("error"):
        error = response["error"]
        message = error.get("message") if isinstance(error, dict) else error
        raise RuntimeError(f"Sorftime MCP error: {message}")

    result = response.get("result", response)
    if not isinstance(result, dict):
        return result
    if result.get("isError"):
        content = result.get("content") or []
        message = next(
            (str(item.get("text")) for item in content if isinstance(item, dict) and item.get("text")),
            "Unknown Sorftime MCP tool error",
        )
        raise RuntimeError(f"Sorftime MCP tool error: {message}")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]

    content = result.get("content") or []
    text_blocks = [
        str(item.get("text"))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text") is not None
    ]
    if not text_blocks:
        return result
    combined = "\n".join(text_blocks).strip()
    try:
        return json.loads(combined)
    except json.JSONDecodeError:
        return combined


class SorftimeMcpClient:
    def __init__(self, url: str | None = None, timeout: int = 120, max_attempts: int | None = None):
        if os.environ.get("AMZ_OFFLINE") == "1":
            raise RuntimeError("Sorftime is disabled during offline validation")
        self._url = url or load_mcp_url()
        self._timeout = timeout
        self._session_id = ""
        self._initialized = False
        self._request_id = 0
        self.tool_call_count = 0
        self.request_attempt_count = 0
        from workflow_storage import load_rules
        self.retry_rules = load_rules(Path(__file__).resolve().parent).get("network", {})
        self._max_attempts = max(1, max_attempts if max_attempts is not None else self.retry_rules.get("max_attempts", 3))

    def preflight(self, max_seconds=None, interval=None):
        max_seconds = self.retry_rules.get("preflight_max_seconds", 900) if max_seconds is None else max_seconds
        interval = self.retry_rules.get("preflight_interval_seconds", 60) if interval is None else interval
        deadline = time.monotonic() + max_seconds
        while True:
            try:
                self.initialize()
                return
            except RuntimeError as exc:
                if any(code in str(exc) for code in ("HTTP 401", "HTTP 403")) or time.monotonic() >= deadline:
                    raise
                time.sleep(min(interval, max(0, deadline - time.monotonic())))

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _post(self, payload: dict[str, Any], expect_response: bool = True) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
            "User-Agent": "amz-product-hunter/1.0",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        last_error: Exception | None = None
        renewed_session = False
        for attempt in range(1, self._max_attempts + 1):
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
            else:
                headers.pop("Mcp-Session-Id", None)
            delay = min(self.retry_rules.get("retry_max_seconds", 60), self.retry_rules.get("retry_base_seconds", 2) * (3 ** (attempt - 1)) * random.uniform(0.7, 1.3))
            request = urllib.request.Request(self._url, data=body, headers=headers, method="POST")
            self.request_attempt_count += 1
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    session_id = response.headers.get("Mcp-Session-Id")
                    if session_id:
                        self._session_id = session_id
                    response_body = response.read()
                    if not expect_response or not response_body:
                        return {}
                    return parse_http_payload(response_body, response.headers.get("Content-Type", ""))
            except urllib.error.HTTPError as exc:
                # Never log response bodies or URLs, which may contain credentials.
                message = f"Sorftime MCP HTTP {exc.code}"
                if exc.code == 404 and self._session_id and payload.get("method") == "tools/call" and not renewed_session:
                    renewed_session = True
                    self._session_id, self._initialized = "", False
                    self.initialize()
                elif exc.code == 429:
                    raw = (exc.headers or {}).get("Retry-After", "")
                    try:
                        delay = max(0, float(raw))
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                    if delay > self.retry_rules.get("retry_max_seconds", 60):
                        raise RuntimeError("Sorftime MCP HTTP 429: retry delay exceeds budget") from exc
                elif not 500 <= exc.code < 600:
                    raise RuntimeError(message) from exc
                last_error = RuntimeError(message)
            except (OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as exc:
                last_error = RuntimeError(type(exc).__name__)
            if attempt < self._max_attempts:
                time.sleep(delay)
        raise RuntimeError(f"Sorftime MCP request failed after {self._max_attempts} attempts: {last_error}")

    def initialize(self) -> None:
        if self._initialized:
            return
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "amz-product-hunter", "version": "1.0"},
                },
            }
        )
        if response.get("error"):
            unpack_tool_result(response)
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            expect_response=False,
        )
        self._initialized = True

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.initialize()
        self.tool_call_count += 1
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return unpack_tool_result(response)
