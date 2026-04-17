"""Tests for spark_mcp.operations."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from spark_mcp.cluster import Cluster, FakeShellRunner
from spark_mcp.models import ClusterSettings, SshSettings
from spark_mcp.operations import Operations

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _load_mock_responses() -> dict[tuple[str, tuple[str, ...]], tuple[int, str, str]]:
    raw = yaml.safe_load((FIXTURES / "mock_ssh_responses.yaml").read_text())
    result: dict[tuple[str, tuple[str, ...]], tuple[int, str, str]] = {}
    for key, value in raw.items():
        node, *argv = key.split("|")
        result[(node, tuple(argv))] = (
            int(value["exit"]),
            str(value.get("stdout", "")).rstrip("\n"),
            str(value.get("stderr", "")).rstrip("\n"),
        )
    return result


@pytest.fixture
def runner() -> FakeShellRunner:
    return FakeShellRunner(_load_mock_responses())


@pytest.fixture
def cluster(runner: FakeShellRunner, tmp_path: Path) -> Cluster:
    return Cluster(
        settings=ClusterSettings(name="t", head_node="localhost", workers=[], interconnect_ip=""),
        ssh=SshSettings(max_connections_per_worker=1, connection_timeout=5),
        ssh_user="x",
        ssh_key_path=tmp_path / "key",
        runner=runner,
    )


async def test_list_containers(cluster: Cluster) -> None:
    ops = Operations(cluster, hf_cache_dir=Path("/tmp"))
    containers = await ops.list_containers("localhost")
    assert containers == ["vllm_node"]


async def test_gpu_metrics(cluster: Cluster) -> None:
    ops = Operations(cluster, hf_cache_dir=Path("/tmp"))
    metrics = await ops.gpu_metrics("localhost")
    assert metrics.memory_used_mb == 94000
    assert metrics.memory_total_mb == 128000
    assert metrics.utilization_pct == 87
    assert metrics.temperature_c == 71


async def test_node_status(cluster: Cluster) -> None:
    ops = Operations(cluster, hf_cache_dir=Path("/tmp"))
    status = await ops.node_status("localhost")
    assert status.reachable is True
    assert "vllm_node" in status.docker_running_containers
    assert status.gpu is not None
    assert status.uptime_seconds == 12345


async def test_container_logs(cluster: Cluster) -> None:
    ops = Operations(cluster, hf_cache_dir=Path("/tmp"))
    logs = await ops.container_logs("localhost", "vllm_node", 100)
    assert "INFO vllm ready" in logs


async def test_stop_container_graceful(cluster: Cluster) -> None:
    ops = Operations(cluster, hf_cache_dir=Path("/tmp"))
    rc = await ops.stop_container("localhost", "vllm_node", timeout_s=30)
    assert rc == 0


async def test_list_cached_models_empty(tmp_path: Path, cluster: Cluster) -> None:
    ops = Operations(cluster, hf_cache_dir=tmp_path / "missing")
    assert await ops.list_cached_models() == []


async def test_list_cached_models_scans_hub(tmp_path: Path, cluster: Cluster) -> None:
    hub = tmp_path / "hub"
    model_dir = hub / "models--Qwen--Qwen3-7B"
    (model_dir / "snapshots" / "abc").mkdir(parents=True)
    payload = model_dir / "snapshots" / "abc" / "weights.bin"
    payload.write_bytes(b"x" * 1024)
    ops = Operations(cluster, hf_cache_dir=tmp_path)
    models = await ops.list_cached_models()
    assert len(models) == 1
    assert models[0].hf_id == "Qwen/Qwen3-7B"
    assert models[0].size_gb > 0
