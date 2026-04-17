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
    # FastMCP ships a DNS rebinding protection that only allows Host:
    # 127.0.0.1 / localhost by default. For LAN deployments behind bearer
    # auth + (recommended) Tailscale/VPN, this is redundant and blocks every
    # non-loopback client (e.g. Claude Code on another machine). We default
    # to False; enable explicitly when operating behind a reverse proxy
    # alongside allowed_hosts.
    dns_rebinding_protection: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)


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


# --- Recipe models (added in Task 5) ---


class RecipeDefaults(BaseModel):
    """Default overrides a recipe applies before run-recipe.py invocation."""

    model_config = ConfigDict(extra="allow")

    port: int = Field(ge=1, le=65535)
    host: str = "0.0.0.0"  # noqa: S104  # matches upstream vllm-docker recipe default
    tensor_parallel: int = Field(ge=1)
    gpu_memory_utilization: float = Field(gt=0, le=1)
    max_model_len: int | None = None


class Recipe(BaseModel):
    """Recipe schema matching eugr/spark-vllm-docker recipe YAMLs.

    `extra="allow"` keeps us forward-compatible with upstream-added fields.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    recipe_version: str
    name: str
    description: str
    model: str
    cluster_only: bool = False
    solo_only: bool = False
    container: str | None = None
    build_args: list[str] = Field(default_factory=list)
    mods: list[str] = Field(default_factory=list)
    defaults: RecipeDefaults
    command: str


class RecipeSummary(BaseModel):
    """Lightweight recipe listing entry, returned by `list_recipes`.

    `name` is the free-form YAML `name:` field (may contain uppercase, dots,
    spaces). `slug` is the filesystem-safe filename stem that every MCP tool
    accepting a `name` argument actually expects.
    """

    name: str
    slug: str
    description: str
    model: str
    supports_cluster: bool
    supports_solo: bool
    is_model_cached: dict[str, bool] = Field(default_factory=dict)
    is_active: bool = False
    path: Path


class ValidationResult(BaseModel):
    """Outcome of `validate_recipe` — valid=False populates errors."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    parsed: Recipe | None = None


class ErrorInfo(BaseModel):
    """Structured error payload returned by any MCP tool that fails."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    hint: str | None = None


class OperationResult(BaseModel):
    """Generic success-or-error wrapper used by mutation tools."""

    success: bool
    data: Any | None = None
    error: ErrorInfo | None = None


# --- Monitoring / cluster status models (added in Task 6) ---


class GpuMetrics(BaseModel):
    node: str
    name: str
    memory_used_mb: int
    memory_total_mb: int
    utilization_pct: int
    temperature_c: int
    power_watts: int


class NodeStatus(BaseModel):
    name: str
    reachable: bool
    hostname: str
    docker_running_containers: list[str] = Field(default_factory=list)
    gpu: GpuMetrics | None = None
    uptime_seconds: int = 0


class RayStatus(BaseModel):
    alive: bool
    head_address: str
    nodes: list[str]


class ClusterStatus(BaseModel):
    cluster_name: str
    head_node: NodeStatus
    workers: list[NodeStatus]
    active_model: ActiveModel | None = None
    ray_status: RayStatus | None = None
    total_vram_gb: float = 0.0
    used_vram_gb: float = 0.0


class CachedModel(BaseModel):
    hf_id: str
    nodes: list[str]
    size_gb: float
    last_modified: datetime


class HealthStatus(BaseModel):
    ok: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ClusterInfo(BaseModel):
    name: str
    nodes: list[str]
    vram_per_node_gb: dict[str, float]
    total_vram_gb: float
    vllm_docker_version: str | None = None


class HfSearchResult(BaseModel):
    """Subset of Hugging Face Hub API response for `search_huggingface`."""

    model_config = ConfigDict(extra="allow")

    id: str
    author: str | None = None
    downloads: int | None = None
    likes: int | None = None
    tags: list[str] = Field(default_factory=list)
    last_modified: datetime | None = None


# --- Recipe launch + download models (added in Task 7) ---


class LaunchArgs(BaseModel):
    """Typed input for `launch_recipe`; persisted so `restart_cluster` can replay."""

    recipe_name: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    setup: bool = False
    solo: bool = False


class LaunchResult(BaseModel):
    success: bool
    recipe: str
    pid: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: ErrorInfo | None = None


class StopResult(BaseModel):
    success: bool
    per_node: dict[str, int]
    escalated_to_kill: list[str] = Field(default_factory=list)


class RestartResult(BaseModel):
    success: bool
    stopped: StopResult
    launched: LaunchResult | None = None


class ReadyResult(BaseModel):
    ready: bool
    elapsed_s: float
    last_error: str | None = None


class DownloadResult(BaseModel):
    download_id: str
    hf_id: str
    started_at: datetime


class DownloadProgress(BaseModel):
    download_id: str
    status: Literal["queued", "in_progress", "completed", "failed", "cancelled"]
    bytes_transferred: int
    percent: float | None = None
    progress_text: str | None = None
    error: str | None = None
