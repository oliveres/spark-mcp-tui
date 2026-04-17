"""Async MCP client wrapper (A5 iteration-2 fix).

Uses the official SDK's `ClientSession` + `streamablehttp_client` so the TUI
speaks the exact same protocol as Claude Code. Structured tool results are
returned via `structuredContent`; if absent, the first text content block is
parsed as JSON.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class OfflineError(RuntimeError):
    """Raised when the MCP server is unreachable or returns a network error."""


class McpClient:
    def __init__(self, url: str, token: str, timeout_s: float = 15.0) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout_s = timeout_s
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None

    async def connect(self) -> None:
        self._stack = AsyncExitStack()
        read, write, _close = await self._stack.enter_async_context(
            streamablehttp_client(self._url, headers=self._headers, timeout=self._timeout_s)
        )
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise OfflineError("Client not connected")
        try:
            result = await self._session.call_tool(tool, arguments or {})
        except (httpx.RequestError, httpx.HTTPStatusError, ConnectionError) as exc:
            raise OfflineError(str(exc)) from exc
        if getattr(result, "structuredContent", None) is not None:
            return result.structuredContent
        for block in getattr(result, "content", []):
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return None

    async def aclose(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None
