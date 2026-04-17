"""Pydantic data models for spark-mcp.

Every model must be JSON-serialisable so FastMCP can pass it across the wire.
Additional domain models (Recipe, NodeStatus, ...) are appended in later tasks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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
