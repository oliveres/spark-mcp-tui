"""Async MCP client wrapper.

Opens a fresh `ClientSession` per call (stateless transport on the server side
makes this cheap and avoids anyio task-scope mismatches that happen when an
`AsyncExitStack` is entered from one asyncio task and closed from another —
which is what Textual does between `on_mount` and `on_unmount`).

Returns `result.structuredContent` when present; otherwise parses the first
text content block as JSON.
"""

from __future__ import annotations

import json
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

    async def connect(self) -> None:
        """No-op; sessions are opened per-call."""
        return None

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        try:
            async with (
                streamablehttp_client(
                    self._url, headers=self._headers, timeout=self._timeout_s
                ) as (read, write, _close),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(tool, arguments or {})
        except (httpx.RequestError, httpx.HTTPStatusError, ConnectionError) as exc:
            raise OfflineError(str(exc)) from exc
        except Exception as exc:
            raise OfflineError(f"MCP call failed: {exc}") from exc
        if getattr(result, "structuredContent", None) is not None:
            sc = result.structuredContent
            # FastMCP wraps non-object return types (list, str, int, ...) as
            # {"result": value} because MCP structuredContent must be a JSON
            # object. Unwrap the envelope transparently so callers see the
            # underlying value.
            if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
                return sc["result"]
            return sc
        for block in getattr(result, "content", []):
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    return text
                if isinstance(parsed, dict) and set(parsed.keys()) == {"result"}:
                    return parsed["result"]
                return parsed
        return None

    async def aclose(self) -> None:
        """No persistent resources; each call is self-contained."""
        return None
