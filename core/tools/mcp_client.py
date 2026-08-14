"""Minimal MCP client boundary for local stdio servers."""

from __future__ import annotations

import json
import os
import select
import subprocess
from dataclasses import dataclass
from typing import Any


class McpProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpServerConfig:
    server_id: str
    command: tuple[str, ...]
    env: dict[str, str] | None = None
    cwd: str | None = None
    timeout_seconds: float = 30.0


class StdioMcpClient:
    """Synchronous MCP client for discovery and tool calls.

    Returned tools must still be mapped through the platform's manifest, risk,
    approval and audit pipeline before they become model-visible.
    """

    def __init__(self, config: McpServerConfig):
        self.config = config
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 0

    def __enter__(self) -> "StdioMcpClient":
        self._process = subprocess.Popen(
            list(self.config.command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=self.config.cwd,
            env={**os.environ, **(self.config.env or {})},
        )
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "lzcore", "version": "1"}})
        self.notify("notifications/initialized", {})
        return self

    def __exit__(self, *_: Any) -> None:
        if self._process and self._process.stdin:
            self._process.stdin.close()
        self._terminate()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise McpProtocolError("MCP client is not connected")
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        while True:
            ready, _, _ = select.select([self._process.stdout], [], [], self.config.timeout_seconds)
            if not ready:
                self._terminate()
                raise McpProtocolError(f"MCP request timed out after {self.config.timeout_seconds:g}s: {method}")
            line = self._process.stdout.readline()
            if not line:
                raise McpProtocolError("MCP server closed stdout")
            message = json.loads(line)
            if message.get("id") != request_id:
                continue
            if message.get("error"):
                raise McpProtocolError(str(message["error"]))
            return dict(message.get("result") or {})

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self.request("tools/list").get("tools") or [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def _send(self, message: dict[str, Any]) -> None:
        assert self._process and self._process.stdin
        self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if not process:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
