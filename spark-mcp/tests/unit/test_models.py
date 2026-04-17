"""Tests for spark_mcp.models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from spark_mcp.models import (
    ClusterSettings,
    LimitsSettings,
    ServerSettings,
    SshSettings,
)


def _full_server_settings(**overrides: object) -> ServerSettings:
    base = {
        "host": "0.0.0.0",
        "port": 8765,
        "transport": "http",
        "log_level": "INFO",
        "metrics_enabled": True,
    }
    base.update(overrides)
    return ServerSettings.model_validate(base)


def test_server_settings_accepts_valid_values() -> None:
    s = _full_server_settings()
    assert s.transport == "http"
    assert s.metrics_auth == "bearer"
    assert s.rate_limit_per_minute == 120
    assert s.cors_allow_origins == []


def test_server_settings_rejects_bad_transport() -> None:
    with pytest.raises(ValidationError):
        _full_server_settings(transport="grpc")


def test_server_settings_rejects_bad_log_level() -> None:
    with pytest.raises(ValidationError):
        _full_server_settings(log_level="CHATTY")


def test_ssh_settings_positive_bounds() -> None:
    with pytest.raises(ValidationError):
        SshSettings(max_connections_per_worker=0, connection_timeout=10)
    with pytest.raises(ValidationError):
        SshSettings(max_connections_per_worker=2, connection_timeout=0)


def test_limits_min_one() -> None:
    with pytest.raises(ValidationError):
        LimitsSettings(
            max_concurrent_models=0,
            launch_timeout_s=900,
            stop_timeout_s=30,
            max_concurrent_downloads=2,
            recipe_command_policy="permissive",
        )


def test_limits_defaults() -> None:
    limits = LimitsSettings(max_concurrent_models=1)
    assert limits.launch_timeout_s == 900
    assert limits.stop_timeout_s == 30
    assert limits.max_concurrent_downloads == 2
    assert limits.recipe_command_policy == "permissive"


def test_cluster_workers_may_be_empty() -> None:
    c = ClusterSettings(name="solo", head_node="localhost", workers=[], interconnect_ip="")
    assert c.workers == []
