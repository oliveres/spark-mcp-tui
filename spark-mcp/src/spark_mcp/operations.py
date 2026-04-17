"""Low-level cluster ops: Docker, GPU, HuggingFace cache introspection.

All operations are thin wrappers around `Cluster.run` (which in turn routes
local calls through asyncio subprocess and remote calls through the
shell-escaped asyncssh path from `cluster.py`).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from .cluster import Cluster
from .models import CachedModel, GpuMetrics, NodeStatus

# nvidia-smi reports "[N/A]" / "Not Supported" for metrics unavailable on some
# hardware (DGX Spark GB10 uses unified memory, so memory.used / memory.total
# are [N/A]). Treat any unparseable value as 0 so gpu_metrics() never raises.
_NA_TOKENS: frozenset[str] = frozenset({"", "N/A", "[N/A]", "NOT SUPPORTED"})


def _safe_int(value: str) -> int:
    stripped = value.strip().upper()
    if stripped in _NA_TOKENS:
        return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


class Operations:
    """Primitive operations composed from `cluster.run` + result parsing."""

    def __init__(self, cluster: Cluster, hf_cache_dir: Path) -> None:
        self._cluster = cluster
        self._hf_cache_dir = hf_cache_dir

    async def list_containers(self, node: str) -> list[str]:
        result = await self._cluster.run(
            node, ["docker", "ps", "--format", "{{json .}}"], timeout=10.0
        )
        names: list[str] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if data.get("State") == "running":
                names.append(str(data["Names"]))
        return names

    async def stop_container(self, node: str, container: str, *, timeout_s: int = 30) -> int:
        stop_result = await self._cluster.run(
            node,
            ["docker", "stop", "-t", str(timeout_s), container],
            timeout=timeout_s + 10,
        )
        if stop_result.exit_code == 0:
            return 0
        kill = await self._cluster.run(node, ["docker", "kill", container], timeout=10.0)
        return kill.exit_code

    async def container_logs(self, node: str, container: str, lines: int = 100) -> str:
        result = await self._cluster.run(
            node, ["docker", "logs", "--tail", str(lines), container], timeout=15.0
        )
        return result.stdout + result.stderr

    async def gpu_metrics(self, node: str) -> GpuMetrics:
        argv = [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        result = await self._cluster.run(node, argv, timeout=10.0)
        row = result.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in row.split(",")]
        return GpuMetrics(
            node=node,
            name=parts[0] if parts else "",
            memory_used_mb=_safe_int(parts[1] if len(parts) > 1 else ""),
            memory_total_mb=_safe_int(parts[2] if len(parts) > 2 else ""),
            utilization_pct=_safe_int(parts[3] if len(parts) > 3 else ""),
            temperature_c=_safe_int(parts[4] if len(parts) > 4 else ""),
            power_watts=_safe_int(parts[5] if len(parts) > 5 else ""),
        )

    async def node_status(self, node: str) -> NodeStatus:
        try:
            hostname = await self._cluster.run(node, ["hostname"], timeout=5.0)
            uptime = await self._cluster.run(node, ["cat", "/proc/uptime"], timeout=5.0)
            containers = await self.list_containers(node)
            gpu = await self.gpu_metrics(node)
        except Exception:
            return NodeStatus(name=node, reachable=False, hostname="")
        try:
            uptime_seconds = int(float(uptime.stdout.split()[0]))
        except (IndexError, ValueError):
            uptime_seconds = 0
        return NodeStatus(
            name=node,
            reachable=True,
            hostname=hostname.stdout.strip(),
            docker_running_containers=containers,
            gpu=gpu,
            uptime_seconds=uptime_seconds,
        )

    async def all_node_status(self) -> list[NodeStatus]:
        """Per-node status in parallel. Node-level failures are converted to
        `NodeStatus(reachable=False)` so a single bad worker cannot abort the
        whole tool call (anyio TaskGroup otherwise wraps and reraises).
        """
        nodes = self._cluster.all_nodes
        results = await asyncio.gather(
            *[self.node_status(n) for n in nodes], return_exceptions=True
        )
        statuses: list[NodeStatus] = []
        for node, res in zip(nodes, results, strict=True):
            if isinstance(res, BaseException):
                statuses.append(NodeStatus(name=node, reachable=False, hostname=""))
            else:
                statuses.append(res)
        return statuses

    async def list_cached_models(self) -> list[CachedModel]:
        """Scan the local HF cache. `hub/models--<org>--<repo>` is the canonical layout."""
        if not self._hf_cache_dir.exists():
            return []
        hub = self._hf_cache_dir / "hub"
        if not hub.exists():
            return []
        models: list[CachedModel] = []
        for d in hub.iterdir():
            if not d.is_dir() or not d.name.startswith("models--"):
                continue
            parts = d.name.removeprefix("models--").split("--", 1)
            if len(parts) != 2:
                continue
            hf_id = "/".join(parts)

            def _du(model_dir: Path = d) -> int:
                return sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())

            size_bytes = await asyncio.to_thread(_du)
            mtime = datetime.fromtimestamp(d.stat().st_mtime, tz=UTC)
            models.append(
                CachedModel(
                    hf_id=hf_id,
                    nodes=["localhost"],
                    size_gb=size_bytes / 1e9,
                    last_modified=mtime,
                )
            )
        return models

    async def list_cached_models_remote(self, node: str) -> list[CachedModel]:
        """SSH-mediated scan for a worker's HF cache directory (A13 / amendment)."""
        argv = [
            "find",
            "~/.cache/huggingface/hub",
            "-maxdepth",
            "1",
            "-name",
            "models--*",
            "-type",
            "d",
            "-printf",
            "%p\t%s\t%T@\n",
        ]
        result = await self._cluster.run(node, argv, timeout=15.0)
        if result.exit_code != 0:
            return []
        models: list[CachedModel] = []
        for line in result.stdout.splitlines():
            try:
                path_s, size_s, mtime_s = line.split("\t")
            except ValueError:
                continue
            name = Path(path_s).name
            parts = name.removeprefix("models--").split("--", 1)
            if len(parts) != 2:
                continue
            try:
                size_bytes = int(size_s)
                mtime = datetime.fromtimestamp(float(mtime_s), tz=UTC)
            except ValueError:
                continue
            models.append(
                CachedModel(
                    hf_id="/".join(parts),
                    nodes=[node],
                    size_gb=size_bytes / 1e9,
                    last_modified=mtime,
                )
            )
        return models
