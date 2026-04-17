"""Smoke tests for server.py tool registration.

The iteration-3 omit-from-coverage decision hid a bug where `_instrument`
registered every tool as `wrapper` (no functools.wraps), so only the
last-decorated tool was reachable via the MCP protocol. These tests
prevent that regression by spot-checking a handful of tool names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spark_mcp.config import AppConfig, SecretSettings
from spark_mcp.models import (
    ClusterSettings,
    LimitsSettings,
    PathSettings,
    ServerSettings,
    SshSettings,
    VllmDockerSettings,
)
from spark_mcp.server import ServerContext, build_mcp, build_metrics


@pytest.fixture
def fake_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """Minimal AppConfig + a fake spark-vllm-docker layout so ServerContext.create works."""
    monkeypatch.setenv("SPARK_MCP_AUTH_TOKEN", "sk-spark-" + "a" * 40)
    monkeypatch.setenv("SPARK_MCP_SSH_USER", "u")
    key = tmp_path / "id_ed25519"
    key.write_text("fake-key")
    key.chmod(0o600)
    monkeypatch.setenv("SPARK_MCP_SSH_KEY_PATH", str(key))
    repo = tmp_path / "spark-vllm-docker"
    (repo / "recipes").mkdir(parents=True)
    return AppConfig(
        server=ServerSettings(
            host="127.0.0.1",
            port=8765,
            transport="http",
            log_level="INFO",
            metrics_enabled=True,
        ),
        cluster=ClusterSettings(name="t", head_node="localhost", workers=[], interconnect_ip=""),
        vllm_docker=VllmDockerSettings(repo_path=repo, container_name="vllm_node"),
        paths=PathSettings(
            hf_cache=tmp_path, state_file=tmp_path / "state.json", cache_dir=tmp_path
        ),
        ssh=SshSettings(max_connections_per_worker=1, connection_timeout=5),
        limits=LimitsSettings(max_concurrent_models=1),
        secrets=SecretSettings(),  # type: ignore[call-arg]
        profile=None,
        config_path=tmp_path / "config.toml",
        env_path=tmp_path / ".env",
    )


PRD_TOOLS = {
    "list_recipes",
    "get_recipe",
    "create_recipe",
    "update_recipe",
    "delete_recipe",
    "validate_recipe",
    "get_cluster_status",
    "launch_recipe",
    "stop_cluster",
    "restart_cluster",
    "get_gpu_status",
    "get_container_logs",
    "tail_logs",
    "list_cached_models",
    "download_model",
    "get_download_progress",
    "cancel_download",
    "search_huggingface",
    "get_cluster_info",
    "health_check",
}


async def test_every_prd_tool_registered_without_metrics(fake_cfg: AppConfig) -> None:
    ctx = await ServerContext.create(fake_cfg)
    try:
        mcp = build_mcp(ctx, metrics=None)
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        missing = PRD_TOOLS - names
        assert not missing, f"PRD tools missing from registration (no-metrics path): {missing}"
    finally:
        await ctx.aclose()


async def test_every_prd_tool_registered_with_metrics(fake_cfg: AppConfig) -> None:
    """Regression: _instrument must preserve __name__ via functools.wraps."""
    ctx = await ServerContext.create(fake_cfg)
    try:
        _registry, metrics = build_metrics()
        mcp = build_mcp(ctx, metrics=metrics)
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "wrapper" not in names, (
            "A tool registered as 'wrapper' — _instrument must use functools.wraps"
        )
        missing = PRD_TOOLS - names
        assert not missing, f"PRD tools missing from registration (with-metrics path): {missing}"
    finally:
        await ctx.aclose()
