"""Shared pytest fixtures for spark-mcp tests."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def sample_toml() -> str:
    """Minimal valid TOML payload for AppConfig."""
    return dedent("""
        [server]
        host = "0.0.0.0"
        port = 8765
        transport = "http"
        log_level = "INFO"
        metrics_enabled = true
        metrics_auth = "bearer"
        rate_limit_per_minute = 120
        cors_allow_origins = []

        [cluster]
        name = "test-cluster"
        head_node = "localhost"
        workers = ["worker-1", "worker-2"]
        interconnect_ip = ""

        [spark-vllm-docker]
        repo_path = "~/spark-vllm-docker"
        container_name = "vllm_node"

        [paths]
        hf_cache = "~/.cache/huggingface"
        state_file = "~/.cache/spark-mcp/state.json"
        cache_dir = "~/.cache/spark-mcp/"

        [ssh]
        max_connections_per_worker = 4
        connection_timeout = 10

        [limits]
        max_concurrent_models = 1
        launch_timeout_s = 900
        stop_timeout_s = 30
        max_concurrent_downloads = 2
        recipe_command_policy = "permissive"
    """).strip()


@pytest.fixture
def sample_env() -> str:
    """32+ char token that satisfies the B9 min_length validator."""
    return dedent("""
        SPARK_MCP_AUTH_TOKEN=sk-spark-test-token-0123456789abcdef
        SPARK_MCP_SSH_USER=tester
        SPARK_MCP_SSH_KEY_PATH=~/.ssh/id_ed25519
    """).strip()


@pytest.fixture
def config_dir(tmp_path: Path, sample_toml: str, sample_env: str) -> Path:
    (tmp_path / "config.toml").write_text(sample_toml)
    (tmp_path / ".env").write_text(sample_env)
    return tmp_path


@pytest.fixture
def profile_dir(tmp_path: Path, sample_toml: str, sample_env: str) -> Path:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "homelab.toml").write_text(sample_toml)
    (profiles / "homelab.env").write_text(sample_env)
    return tmp_path


@pytest.fixture(autouse=True)
def clean_spark_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any ambient SPARK_MCP_* env vars so tests rely only on fixtures."""
    for key in (
        "SPARK_MCP_AUTH_TOKEN",
        "SPARK_MCP_SSH_USER",
        "SPARK_MCP_SSH_KEY_PATH",
        "SPARK_MCP_CONFIG",
    ):
        monkeypatch.delenv(key, raising=False)
