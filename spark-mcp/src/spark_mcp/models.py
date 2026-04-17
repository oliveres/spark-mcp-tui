"""Pydantic data models for spark-mcp.

Every model must be JSON-serialisable so FastMCP can pass it across the wire.
Additional domain models (Recipe, NodeStatus, ...) are appended in later tasks.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ServerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    port: int = Field(ge=1, le=65535)
    transport: Literal["http", "stdio"]
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    metrics_enabled: bool
    metrics_auth: Literal["bearer", "none"] = "bearer"
    rate_limit_per_minute: int = Field(default=120, ge=0)
    cors_allow_origins: list[str] = Field(default_factory=list)


class ClusterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    head_node: str
    workers: list[str]
    interconnect_ip: str = ""


class VllmDockerSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    repo_path: Path
    container_name: str


class PathSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hf_cache: Path
    state_file: Path
    cache_dir: Path


class SshSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_connections_per_worker: int = Field(ge=1)
    connection_timeout: int = Field(ge=1)


class LimitsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_concurrent_models: int = Field(ge=1)
    launch_timeout_s: int = Field(default=900, ge=60)
    stop_timeout_s: int = Field(default=30, ge=5)
    max_concurrent_downloads: int = Field(default=2, ge=1)
    recipe_command_policy: Literal["permissive", "vllm-only"] = "permissive"


# --- Runtime models (added in Task 4) ---


class ShellResult(BaseModel):
    """Result of a single shell command invocation (local or remote)."""

    node: str
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float


class ActiveModel(BaseModel):
    """Currently loaded vLLM model, persisted in state.json."""

    recipe: str
    started_at: datetime
    overrides: dict[str, Any] = Field(default_factory=dict)
    container_id: str | None = None
    launch_pid: int | None = None


class DownloadRecord(BaseModel):
    """Persisted record of an hf-download.sh invocation."""

    download_id: str
    hf_id: str
    status: Literal["queued", "in_progress", "completed", "failed", "cancelled"]
    bytes_transferred: int = 0
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


class PersistedState(BaseModel):
    """Top-level state file schema. Forbids unknown fields so schema drift surfaces."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    active_model: ActiveModel | None = None
    last_launch_args: dict[str, Any] | None = None  # populated in Task 8
    downloads: dict[str, DownloadRecord] = Field(default_factory=dict)
