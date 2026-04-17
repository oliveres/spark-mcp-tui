"""Config loader for spark-tui."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

DEFAULT_CONFIG = Path("~/.config/spark-tui").expanduser()


class ProfileConfig(BaseModel):
    mcp_url: str


class UiConfig(BaseModel):
    theme: str = "dracula"
    refresh_interval_ms: int = 3000
    log_tail_lines: int = 200


class ConnectionConfig(BaseModel):
    default_profile: str


class TuiConfig(BaseModel):
    connection: ConnectionConfig
    profiles: dict[str, ProfileConfig]
    ui: UiConfig


MCP_DEFAULT_CONFIG = Path("~/.config/spark-mcp").expanduser()


def _autodetect_from_spark_mcp() -> tuple[TuiConfig, str, str] | None:
    """When no TUI config exists but spark-mcp is installed locally, build an
    implicit single-profile config pointing at 127.0.0.1:<port> with the
    token read out of spark-mcp's .env file.
    """
    mcp_toml = MCP_DEFAULT_CONFIG / "config.toml"
    mcp_env = MCP_DEFAULT_CONFIG / ".env"
    if not mcp_toml.exists() or not mcp_env.exists():
        return None
    try:
        raw = tomllib.loads(mcp_toml.read_text())
        port = int(raw["server"]["port"])
    except (KeyError, ValueError, tomllib.TOMLDecodeError):
        return None
    token = ""
    for line in mcp_env.read_text().splitlines():
        if line.startswith("SPARK_MCP_AUTH_TOKEN="):
            token = line.split("=", 1)[1].strip()
            break
    if not token:
        return None
    cfg = TuiConfig(
        connection=ConnectionConfig(default_profile="local"),
        profiles={"local": ProfileConfig(mcp_url=f"http://127.0.0.1:{port}/mcp")},
        ui=UiConfig(),
    )
    return cfg, "local", token


def load_tui_config(
    profile: str | None = None, config_dir: Path | None = None
) -> tuple[TuiConfig, str, str]:
    base = config_dir or DEFAULT_CONFIG
    toml_path = base / "config.toml"
    env_path = base / ".env"

    # Zero-config path: no TUI config, but spark-mcp is installed locally -> use it.
    if not toml_path.exists() and config_dir is None and profile is None:
        discovered = _autodetect_from_spark_mcp()
        if discovered is not None:
            return discovered
        raise FileNotFoundError(
            f"TUI config not found at {toml_path} and no local spark-mcp install "
            f"detected at {MCP_DEFAULT_CONFIG}."
        )

    if not toml_path.exists():
        raise FileNotFoundError(f"TUI config not found at {toml_path}")
    raw = tomllib.loads(toml_path.read_text())
    cfg = TuiConfig.model_validate(raw)
    active_profile = profile or cfg.connection.default_profile
    if active_profile not in cfg.profiles:
        raise ValueError(f"Unknown profile {active_profile!r}")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("SPARK_TUI_TOKEN_"):
                key, _, val = line.partition("=")
                os.environ.setdefault(key, val)
    token_var = f"SPARK_TUI_TOKEN_{active_profile.upper()}"
    token = os.environ.get(token_var)
    if not token:
        raise RuntimeError(f"Missing token env var {token_var}")
    url = cfg.profiles[active_profile].mcp_url
    if url.startswith("http://") and not url.startswith(
        ("http://localhost", "http://127.0.0.1", "http://[::1]")
    ):
        raise RuntimeError(
            "Refusing to send bearer token over plain HTTP to a non-localhost URL. "
            "Use HTTPS or a Tailscale-protected LAN."
        )
    return cfg, active_profile, token
