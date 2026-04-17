"""Subprocess wrappers around eugr/spark-vllm-docker CLI scripts.

We only call these scripts; we never modify them. If upstream changes the CLI
contract, update this module's argv builders to match.

Security notes (amendments):
- A17/B8: `build_run_recipe_argv` rejects unknown override keys to block
  command-injection paths that flow through `run-recipe.py {command}`
  template substitution.
- A7: `VllmDocker.stop_all` discovers running containers per node via
  `Operations.list_containers` before issuing stop/kill so clusters with
  heterogeneous container names are handled safely.
"""

from __future__ import annotations

import asyncio
import contextlib
import re as _re
import uuid
from asyncio import create_subprocess_exec as _spawn_subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .cluster import Cluster
from .models import (
    DownloadProgress,
    DownloadResult,
    ErrorInfo,
    LaunchArgs,
    LaunchResult,
    ReadyResult,
    StopResult,
)
from .operations import Operations

# hf-download.sh / uvx progress lines look like:
#   "Downloading (incomplete total...):   1%|▉     | 141M/24.3G [00:08<11:57, 33.7MB/s]"
# or huggingface_hub's:
#   "Fetching 52 files:  12%|...| 6/52 [00:04<00:35,  1.31it/s]"
_PROGRESS_LINE_RE = _re.compile(r"(\d+(?:\.\d+)?)%\|")
_SIZE_PAIR_RE = _re.compile(r"\|\s*([\d.]+[KMGT]?)/([\d.]+[KMGT]?)\s")
_SIZE_UNITS: dict[str, int] = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _parse_size(token: str) -> int:
    if not token:
        return 0
    unit = ""
    if token[-1].isalpha() and token[-1].upper() in _SIZE_UNITS:
        unit = token[-1].upper()
        token = token[:-1]
    try:
        return int(float(token) * _SIZE_UNITS[unit])
    except (ValueError, KeyError):
        return 0


class ProgressTracker:
    """Continuously drains a subprocess's stderr, remembering the last
    non-blank line + any parsed percentage / byte counts.

    hf-download.sh (via uvx + huggingface_hub) prints tqdm-style progress
    bars to stderr. We read line-buffered (but handle \r-separated updates
    too) and expose the last seen state for `get_download_progress`.
    """

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self.last_line: str = ""
        self.percent: float | None = None
        self.bytes_done: int = 0
        self.bytes_total: int = 0
        self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:  # pragma: no cover
        if self._proc.stderr is None:
            return
        buffer = b""
        while True:
            chunk = await self._proc.stderr.read(1024)
            if not chunk:
                break
            buffer += chunk
            # tqdm uses \r to overwrite the same line; treat both \n and \r as terminators.
            parts = _re.split(rb"[\r\n]", buffer)
            buffer = parts.pop()  # last incomplete piece
            for raw in parts:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                self._update(line)

    def _update(self, line: str) -> None:
        self.last_line = line[:200]
        m = _PROGRESS_LINE_RE.search(line)
        if m:
            with contextlib.suppress(ValueError):
                self.percent = float(m.group(1))
        m2 = _SIZE_PAIR_RE.search(line)
        if m2:
            self.bytes_done = _parse_size(m2.group(1))
            self.bytes_total = _parse_size(m2.group(2))

    def cancel(self) -> None:
        self._task.cancel()


OVERRIDE_FLAG_MAP: dict[str, str] = {
    "port": "--port",
    "host": "--host",
    "tensor_parallel": "--tensor-parallel",
    "gpu_memory_utilization": "--gpu-memory-utilization",
    "max_model_len": "--max-model-len",
}

ALLOWED_OVERRIDES: frozenset[str] = frozenset(OVERRIDE_FLAG_MAP.keys())


class LaunchArgsError(ValueError):
    """Raised when override keys fall outside ALLOWED_OVERRIDES."""


def build_run_recipe_argv(repo_path: Path, args: LaunchArgs) -> list[str]:
    """Build argv for `run-recipe.py -d` with validated overrides only."""
    unknown = set(args.overrides) - ALLOWED_OVERRIDES
    if unknown:
        raise LaunchArgsError(
            f"Unknown override keys: {sorted(unknown)}. Allowed: {sorted(ALLOWED_OVERRIDES)}."
        )
    argv: list[str] = [str(repo_path / "run-recipe.py"), args.recipe_name, "-d"]
    if args.setup:
        argv.append("--setup")
    if args.solo:
        argv.append("--solo")
    for key, value in args.overrides.items():
        argv.extend([OVERRIDE_FLAG_MAP[key], str(value)])
    return argv


def build_launch_cluster_argv(repo_path: Path, workers: list[str]) -> list[str]:
    """Build argv for `launch-cluster.sh <worker> <worker>...`."""
    return [str(repo_path / "launch-cluster.sh"), *workers]


def build_hf_download_argv(
    repo_path: Path,
    hf_id: str,
    copy_to: str | None = None,
    copy_parallel: bool = False,
) -> list[str]:
    """Build argv for `hf-download.sh <hf_id> [--copy-to IP [--copy-parallel]]`."""
    argv: list[str] = [str(repo_path / "hf-download.sh"), hf_id]
    if copy_to:
        argv.extend(["--copy-to", copy_to])
        if copy_parallel:
            argv.append("--copy-parallel")
    return argv


class VllmDocker:
    """High-level wrapper around vllm-docker scripts.

    `stop_all` discovers containers per node via `Operations` before stopping,
    honoring the A7 amendment from the security review.
    """

    def __init__(
        self,
        cluster: Cluster,
        repo_path: Path,
        container_name: str,
        ops: Operations,
        *,
        launch_timeout_s: int = 900,
    ) -> None:
        self._cluster = cluster
        self._repo = repo_path
        self._container = container_name
        self._ops = ops
        self._launch_timeout_s = launch_timeout_s

    async def launch_recipe(self, args: LaunchArgs) -> LaunchResult:  # pragma: no cover
        try:
            argv = build_run_recipe_argv(self._repo, args)
        except LaunchArgsError as exc:
            return LaunchResult(
                success=False,
                recipe=args.recipe_name,
                error=ErrorInfo(code="LAUNCH_FAILED", message=str(exc)),
            )
        result = await self._cluster.run(
            self._cluster.settings.head_node, argv, timeout=float(self._launch_timeout_s)
        )
        if result.exit_code != 0:
            return LaunchResult(
                success=False,
                recipe=args.recipe_name,
                stdout=result.stdout,
                stderr=result.stderr,
                error=ErrorInfo(
                    code="LAUNCH_FAILED",
                    message=f"run-recipe.py exited with {result.exit_code}",
                    details={"stderr": result.stderr[-1000:]},
                ),
            )
        return LaunchResult(
            success=True,
            recipe=args.recipe_name,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def stop_all(self, *, timeout_s: int = 30) -> StopResult:  # pragma: no cover
        """Stop every node's configured container in parallel; escalate on timeout."""
        nodes = self._cluster.all_nodes

        async def stop_node(node: str) -> tuple[str, int, bool]:
            containers = await self._ops.list_containers(node)
            if self._container not in containers:
                return node, 0, False
            stop = await self._cluster.run(
                node,
                ["docker", "stop", "-t", str(timeout_s), self._container],
                timeout=float(timeout_s + 10),
            )
            if stop.exit_code == 0:
                return node, 0, False
            kill = await self._cluster.run(node, ["docker", "kill", self._container], timeout=10.0)
            return node, kill.exit_code, True

        outcomes = await asyncio.gather(*[stop_node(n) for n in nodes], return_exceptions=True)
        per_node: dict[str, int] = {}
        escalated: list[str] = []
        for res, node in zip(outcomes, nodes, strict=True):
            if isinstance(res, BaseException):
                per_node[node] = -1
                continue
            name, code, did_kill = res
            per_node[name] = code
            if did_kill:
                escalated.append(name)
        return StopResult(
            success=all(code == 0 for code in per_node.values()),
            per_node=per_node,
            escalated_to_kill=escalated,
        )

    async def wait_ready(self, port: int, timeout_s: int = 120) -> ReadyResult:  # pragma: no cover
        """Poll vLLM's /health endpoint until 200 or timeout."""
        url = f"http://localhost:{port}/health"
        loop = asyncio.get_event_loop()
        start = loop.time()
        last_error: str | None = None
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            while (elapsed := loop.time() - start) < timeout_s:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return ReadyResult(ready=True, elapsed_s=elapsed)
                    last_error = f"HTTP {resp.status_code}"
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                await asyncio.sleep(2.0)
        return ReadyResult(ready=False, elapsed_s=float(timeout_s), last_error=last_error)

    async def start_download(
        self, hf_id: str, interconnect_ip: str | None
    ) -> tuple[DownloadResult, asyncio.subprocess.Process, ProgressTracker]:  # pragma: no cover
        """Spawn hf-download.sh as an async subprocess + attach a progress tracker.

        Detects immediate failures (script missing, non-executable, exits
        within the first 500 ms) and surfaces them as a RuntimeError with
        the captured stderr.

        The returned ProgressTracker drains stderr continuously so later calls
        to `get_download_progress` can report the latest `hf-download.sh`
        progress line (percentage + bytes transferred).
        """
        import os as _os

        script = self._repo / "hf-download.sh"
        if not script.exists():
            raise RuntimeError(f"hf-download.sh not found at {script}")
        if not _os.access(script, _os.X_OK):
            raise RuntimeError(f"hf-download.sh at {script} is not executable")
        argv = build_hf_download_argv(
            self._repo,
            hf_id,
            copy_to=interconnect_ip,
            copy_parallel=bool(interconnect_ip),
        )
        proc = await _spawn_subprocess(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Early-failure probe: if the script exits within 500 ms, surface
        # the stderr instead of pretending the download started.
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.5)
        except TimeoutError:
            pass  # still running — genuine download in flight
        else:
            stderr = b""
            if proc.stderr is not None:
                stderr = await proc.stderr.read()
            raise RuntimeError(
                f"hf-download.sh exited immediately with code {proc.returncode}: "
                f"{stderr.decode(errors='replace')[-1000:]}"
            )
        tracker = ProgressTracker(proc)
        return (
            DownloadResult(
                download_id=str(uuid.uuid4()),
                hf_id=hf_id,
                started_at=datetime.now(tz=UTC),
            ),
            proc,
            tracker,
        )

    @staticmethod
    def progress_snapshot(download_id: str) -> DownloadProgress:
        """Minimal placeholder progress record for server-side use."""
        return DownloadProgress(download_id=download_id, status="in_progress", bytes_transferred=0)
