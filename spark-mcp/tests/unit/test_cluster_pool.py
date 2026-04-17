"""Tests for spark_mcp.cluster."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spark_mcp.cluster import Cluster, FakeShellRunner, StateStore, shell_escape_argv
from spark_mcp.models import (
    ActiveModel,
    ClusterSettings,
    PersistedState,
    SshSettings,
)


@pytest.fixture
def cluster_settings() -> ClusterSettings:
    return ClusterSettings(
        name="t", head_node="localhost", workers=["w1", "w2"], interconnect_ip=""
    )


@pytest.fixture
def ssh_settings() -> SshSettings:
    return SshSettings(max_connections_per_worker=2, connection_timeout=5)


async def test_cluster_runs_on_head_locally(
    cluster_settings: ClusterSettings, ssh_settings: SshSettings, tmp_path: Path
) -> None:
    runner = FakeShellRunner({("localhost", ("echo", "hi")): (0, "hi", "")})
    cluster = Cluster(
        cluster_settings,
        ssh_settings,
        ssh_user="x",
        ssh_key_path=tmp_path / "key",
        runner=runner,
    )
    result = await cluster.run("localhost", ["echo", "hi"])
    assert result.exit_code == 0
    assert result.stdout == "hi"


async def test_cluster_runs_all_nodes_in_parallel(
    cluster_settings: ClusterSettings, ssh_settings: SshSettings, tmp_path: Path
) -> None:
    runner = FakeShellRunner(
        {
            ("localhost", ("hostname",)): (0, "head", ""),
            ("w1", ("hostname",)): (0, "w1-host", ""),
            ("w2", ("hostname",)): (0, "w2-host", ""),
        }
    )
    cluster = Cluster(
        cluster_settings,
        ssh_settings,
        ssh_user="x",
        ssh_key_path=tmp_path / "key",
        runner=runner,
    )
    results = await cluster.run_all(["hostname"])
    assert {r.node for r in results} == {"localhost", "w1", "w2"}


async def test_state_store_atomic_write(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    await store.save(PersistedState(active_model=None))
    assert (tmp_path / "state.json").exists()
    data = json.loads((tmp_path / "state.json").read_text())
    assert data["version"] == 1


async def test_state_store_roundtrip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    loaded = await store.load()
    assert loaded.active_model is None
    state = PersistedState(active_model=ActiveModel(recipe="x", started_at=datetime.now(UTC)))
    await store.save(state)
    reloaded = await store.load()
    assert reloaded.active_model is not None
    assert reloaded.active_model.recipe == "x"


def test_shell_escape_argv_with_metachars() -> None:
    """B1-revised: argv elements containing shell metachars must be single-quoted."""
    assert shell_escape_argv(["echo", "hi"]) == "echo hi"
    assert shell_escape_argv(["echo", "a; echo PWNED"]) == "echo 'a; echo PWNED'"
    assert shell_escape_argv(["docker", "logs", "x$(rm -rf /)"]) == "docker logs 'x$(rm -rf /)'"
    # Embedded single quote
    assert shell_escape_argv(["echo", "it's"]) == "echo 'it'\"'\"'s'"


def test_ssh_key_permissions_check_rejects_world_readable(
    tmp_path: Path, cluster_settings: ClusterSettings, ssh_settings: SshSettings
) -> None:
    """B10: starting up with a 0o644 key must fail fast."""
    from spark_mcp.cluster import AsyncSshRunner

    key = tmp_path / "id_ed25519"
    key.write_text("fake-key")
    key.chmod(0o644)
    with pytest.raises(RuntimeError, match="insecure permissions"):
        AsyncSshRunner(
            head_node="localhost",
            workers=["w1"],
            ssh_user="u",
            ssh_key_path=key,
            known_hosts_path=tmp_path / "known_hosts",
            max_per_worker=1,
            connect_timeout=5,
        )


def test_ssh_key_permissions_check_missing_known_hosts(
    tmp_path: Path,
) -> None:
    """B3: if known_hosts does not exist, construction fails (no MITM-silent connect)."""
    from spark_mcp.cluster import AsyncSshRunner

    key = tmp_path / "id_ed25519"
    key.write_text("fake-key")
    key.chmod(0o600)
    with pytest.raises(RuntimeError, match="known_hosts not found"):
        AsyncSshRunner(
            head_node="localhost",
            workers=["w1"],
            ssh_user="u",
            ssh_key_path=key,
            known_hosts_path=tmp_path / "does_not_exist",
            max_per_worker=1,
            connect_timeout=5,
        )
