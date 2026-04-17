"""Config loading for spark-mcp.

Two-layer model:
- TOML file (structure + behavior)
- .env file (secrets only)

No overlap between layers. Profile support: --profile <name> selects
profiles/<name>.toml and profiles/<name>.env.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import (
    ClusterSettings,
    LimitsSettings,
    PathSettings,
    ServerSettings,
    SshSettings,
    VllmDockerSettings,
)

DEFAULT_CONFIG_DIR = Path("~/.config/spark-mcp").expanduser()
TEMPLATE_DIR = Path(__file__).parent / "templates"


class SecretSettings(BaseSettings):
    """Secrets from .env. Empty tokens are rejected (see B9)."""

    model_config = SettingsConfigDict(
        env_prefix="SPARK_MCP_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    auth_token: SecretStr
    ssh_user: str
    ssh_key_path: Path

    @field_validator("auth_token")
    @classmethod
    def _min_token_length(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError(
                "SPARK_MCP_AUTH_TOKEN must be at least 32 characters. "
                "Run `spark-mcp init` to generate a valid token."
            )
        return v

    @field_validator("ssh_key_path")
    @classmethod
    def _expand_ssh_key_path(cls, v: Path) -> Path:
        """Expand ~ and $HOME/$USER references from the .env file value."""
        return Path(os.path.expandvars(str(v))).expanduser()


class AppConfig(BaseModel):
    """Aggregated config: TOML structure + secrets."""

    server: ServerSettings
    cluster: ClusterSettings
    vllm_docker: VllmDockerSettings
    paths: PathSettings
    ssh: SshSettings
    limits: LimitsSettings
    secrets: SecretSettings
    profile: str | None = None
    config_path: Path
    env_path: Path


def resolve_paths(profile: str | None, config_dir: Path | None = None) -> tuple[Path, Path]:
    """Return (toml_path, env_path) for the selected profile.

    Precedence: SPARK_MCP_CONFIG env var > --profile > default.
    """
    explicit = os.environ.get("SPARK_MCP_CONFIG")
    if explicit:
        toml_path = Path(explicit).expanduser()
        return toml_path, toml_path.with_suffix(".env")
    base = config_dir or DEFAULT_CONFIG_DIR
    if profile:
        return (
            base / "profiles" / f"{profile}.toml",
            base / "profiles" / f"{profile}.env",
        )
    return base / "config.toml", base / ".env"


def _expand(value: str | Path) -> Path:
    """Allow-listed expansion: only $HOME / ~ / $USER references."""
    text = str(value)
    # Reject arbitrary $VAR references; only HOME and USER are honored.
    if "$" in text and not any(token in text for token in ("$HOME", "${HOME}", "$USER", "${USER}")):
        raise ValueError(
            f"Unsupported env var reference in path {text!r}. Only $HOME and $USER are expanded."
        )
    return Path(os.path.expandvars(text)).expanduser()


def _normalize_toml(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert dashed TOML keys to snake_case and expand paths."""
    vllm = raw.get("spark-vllm-docker", {})
    return {
        "server": raw["server"],
        "cluster": raw["cluster"],
        "vllm_docker": {
            "repo_path": _expand(vllm["repo_path"]),
            "container_name": vllm["container_name"],
        },
        "paths": {k: _expand(v) for k, v in raw["paths"].items()},
        "ssh": raw["ssh"],
        "limits": raw["limits"],
    }


def load_config(profile: str | None = None, config_dir: Path | None = None) -> AppConfig:
    """Load and validate config from TOML + .env. Raises FileNotFoundError if TOML missing."""
    toml_path, env_path = resolve_paths(profile, config_dir)
    if not toml_path.exists():
        raise FileNotFoundError(f"Config file not found: {toml_path}. Run `spark-mcp init` first.")
    with toml_path.open("rb") as fh:
        raw = tomllib.load(fh)
    normalized = _normalize_toml(raw)
    secrets = (
        SecretSettings(_env_file=str(env_path))  # type: ignore[call-arg]
        if env_path.exists()
        else SecretSettings()  # type: ignore[call-arg]  # pydantic-settings pulls from env
    )
    return AppConfig(
        **normalized,
        secrets=secrets,
        profile=profile,
        config_path=toml_path,
        env_path=env_path,
    )


def default_template_path() -> Path:
    """Packaged `config.toml` template; used by `spark-mcp init`."""
    return TEMPLATE_DIR / "config.toml"


def default_env_template_path() -> Path:
    """Packaged `.env` template."""
    return TEMPLATE_DIR / "env.template"
