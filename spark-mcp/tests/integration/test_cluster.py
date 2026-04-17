"""End-to-end integration tests against a real DGX Spark cluster.

Skipped by default; run with `pytest -m integration` once env vars are set:
  SPARK_INTEGRATION_HOST=spark-head.local:8765
  SPARK_INTEGRATION_TOKEN=sk-spark-...
  SPARK_INTEGRATION_RECIPE=gemma4-26b-a4b  (optional; default shown)

Uses the official MCP SDK `ClientSession` + `streamablehttp_client` so we
exercise the same protocol path real clients (Claude Code, spark-tui) use.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

pytestmark = pytest.mark.integration


async def _call_tool(
    host: str, token: str, name: str, arguments: dict[str, Any] | None = None
) -> Any:
    url = f"http://{host}/mcp"
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncExitStack() as stack:
        read, write, _close = await stack.enter_async_context(
            streamablehttp_client(url, headers=headers)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        result = await session.call_tool(name, arguments or {})
        if result.structuredContent is not None:
            return result.structuredContent
        for block in result.content:
            if getattr(block, "type", None) == "text":
                import json

                text = getattr(block, "text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return None


async def test_health_and_list_recipes(integration_host: str, integration_token: str) -> None:
    health = await _call_tool(integration_host, integration_token, "health_check")
    assert health.get("ok") is True

    recipes = await _call_tool(integration_host, integration_token, "list_recipes")
    assert isinstance(recipes, list)


async def test_stop_cluster_idempotent(integration_host: str, integration_token: str) -> None:
    """B17: stop_cluster must succeed twice in a row."""
    first = await _call_tool(integration_host, integration_token, "stop_cluster")
    assert first.get("success") is True
    second = await _call_tool(integration_host, integration_token, "stop_cluster")
    assert second.get("success") is True


async def test_launch_wait_stop(
    integration_host: str, integration_token: str, integration_recipe: str
) -> None:
    launched = await _call_tool(
        integration_host,
        integration_token,
        "launch_recipe",
        {"recipe_name": integration_recipe},
    )
    assert launched.get("success") is True
    ready = await _call_tool(
        integration_host,
        integration_token,
        "wait_ready",
        {"recipe_name": integration_recipe, "timeout_s": 180},
    )
    assert ready.get("ready") is True
    stopped = await _call_tool(integration_host, integration_token, "stop_cluster")
    assert stopped.get("success") is True


async def test_delete_recipe_idempotent(integration_host: str, integration_token: str) -> None:
    """B17: deleting a non-existent recipe returns success with was_present=False."""
    result = await _call_tool(
        integration_host,
        integration_token,
        "delete_recipe",
        {"name": "this-recipe-should-not-exist-for-tests"},
    )
    assert result.get("success") is True
    assert result.get("data", {}).get("was_present") is False
