"""Tests for spark_mcp.config."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from spark_mcp.config import load_config, resolve_paths


def test_load_config_success(config_dir: Path) -> None:
    cfg = load_config(config_dir=config_dir)
    assert cfg.server.port == 8765
    assert cfg.cluster.workers == ["worker-1", "worker-2"]
    assert cfg.vllm_docker.container_name == "vllm_node"
    assert cfg.secrets.auth_token.get_secret_value().startswith("sk-spark-test")


def test_load_config_expands_home(config_dir: Path) -> None:
    cfg = load_config(config_dir=config_dir)
    assert str(cfg.paths.hf_cache).startswith(os.path.expanduser("~"))


def test_load_config_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(config_dir=tmp_path)


def test_profile_resolution(profile_dir: Path) -> None:
    cfg = load_config(profile="homelab", config_dir=profile_dir)
    assert cfg.profile == "homelab"
    assert cfg.config_path.name == "homelab.toml"


def test_explicit_env_var_overrides(
    tmp_path: Path,
    sample_toml: str,
    sample_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom = tmp_path / "custom.toml"
    custom.write_text(sample_toml)
    custom.with_suffix(".env").write_text(sample_env)
    monkeypatch.setenv("SPARK_MCP_CONFIG", str(custom))
    cfg = load_config()
    assert cfg.config_path == custom


def test_resolve_paths_default() -> None:
    toml_path, env_path = resolve_paths(profile=None, config_dir=Path("/tmp/x"))
    assert toml_path == Path("/tmp/x/config.toml")
    assert env_path == Path("/tmp/x/.env")


def test_resolve_paths_profile() -> None:
    toml_path, env_path = resolve_paths(profile="office", config_dir=Path("/tmp/x"))
    assert toml_path == Path("/tmp/x/profiles/office.toml")
    assert env_path == Path("/tmp/x/profiles/office.env")


def test_invalid_port_rejected(tmp_path: Path, sample_env: str) -> None:
    (tmp_path / "config.toml").write_text(
        """
[server]
host = "0.0.0.0"
port = 99999
transport = "http"
log_level = "INFO"
metrics_enabled = true
metrics_auth = "bearer"
rate_limit_per_minute = 120
cors_allow_origins = []
[cluster]
name = "x"
head_node = "localhost"
workers = []
interconnect_ip = ""
[spark-vllm-docker]
repo_path = "~/x"
container_name = "vllm_node"
[paths]
hf_cache = "~/x"
state_file = "~/x"
cache_dir = "~/x"
[ssh]
max_connections_per_worker = 4
connection_timeout = 10
[limits]
max_concurrent_models = 1
launch_timeout_s = 900
stop_timeout_s = 30
max_concurrent_downloads = 2
recipe_command_policy = "permissive"
"""
    )
    (tmp_path / ".env").write_text(sample_env)
    with pytest.raises(ValidationError):
        load_config(config_dir=tmp_path)


def test_missing_auth_token_rejected(tmp_path: Path, sample_toml: str) -> None:
    """When env file has an empty/missing token, SecretSettings rejects."""
    (tmp_path / "config.toml").write_text(sample_toml)
    (tmp_path / ".env").write_text(
        "SPARK_MCP_AUTH_TOKEN=\nSPARK_MCP_SSH_USER=u\nSPARK_MCP_SSH_KEY_PATH=~/.ssh/k\n"
    )
    with pytest.raises(ValidationError):
        load_config(config_dir=tmp_path)


def test_short_auth_token_rejected(tmp_path: Path, sample_toml: str) -> None:
    """Tokens below 32 chars fail the B9 min_length validator."""
    (tmp_path / "config.toml").write_text(sample_toml)
    (tmp_path / ".env").write_text(
        "SPARK_MCP_AUTH_TOKEN=too-short\nSPARK_MCP_SSH_USER=u\nSPARK_MCP_SSH_KEY_PATH=~/.ssh/k\n"
    )
    with pytest.raises(ValidationError) as excinfo:
        load_config(config_dir=tmp_path)
    assert "at least 32" in str(excinfo.value)


def test_unsupported_env_var_in_path_rejected(tmp_path: Path, sample_env: str) -> None:
    toml = tmp_path / "config.toml"
    toml.write_text(
        """
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
name = "x"
head_node = "localhost"
workers = []
interconnect_ip = ""
[spark-vllm-docker]
repo_path = "$EVIL/spark"
container_name = "vllm_node"
[paths]
hf_cache = "~/x"
state_file = "~/x"
cache_dir = "~/x"
[ssh]
max_connections_per_worker = 4
connection_timeout = 10
[limits]
max_concurrent_models = 1
launch_timeout_s = 900
stop_timeout_s = 30
max_concurrent_downloads = 2
recipe_command_policy = "permissive"
"""
    )
    (tmp_path / ".env").write_text(sample_env)
    with pytest.raises(ValueError, match="Unsupported env var"):
        load_config(config_dir=tmp_path)
