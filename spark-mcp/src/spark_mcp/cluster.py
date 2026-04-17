"""Cluster abstraction: node inventory, SSH pool, persistent state.

Security-sensitive boundaries (these are the hot paths audited in iterations 1-3):
- Remote SSH commands must go through `shell_escape_argv` (B1-revised).
- SSH host keys are always verified (B3): `known_hosts=None` is forbidden.
- The SSH private key must be 0o600 or stricter (B10) — fail-fast at construction.
- State writes are atomic (temp + os.replace) and guarded by asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import time
from asyncio import create_subprocess_exec as _spawn_subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

import asyncssh

from .models import ClusterSettings, PersistedState, ShellResult, SshSettings


def shell_escape_argv(argv: list[str]) -> str:
    """Join argv into a single shell-safe command string (B1-revised).

    Each element is individually shell-quoted via shlex so metacharacters like
    `;`, `|`, `$(...)`, backticks, newlines, spaces are delivered as a single
    token to the remote shell.
    """
    return shlex.join(argv)


FakeKey = tuple[str, tuple[str, ...]]


class ShellRunner(Protocol):
    """Abstract interface for executing a command on a node."""

    async def run(
        self,
        node: str,
        argv: list[str],
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> ShellResult: ...

    async def close(self) -> None: ...


class FakeShellRunner:
    """In-memory ShellRunner for unit tests.

    Keys are `(node, tuple(argv))`; values are `(exit_code, stdout, stderr)`.
    """

    def __init__(self, responses: dict[FakeKey, tuple[int, str, str]]) -> None:
        self._responses = responses

    async def run(self, node: str, argv: list[str], *, timeout: float) -> ShellResult:  # noqa: ASYNC109
        key = (node, tuple(argv))
        if key not in self._responses:
            raise KeyError(f"No fake response configured for {key!r}")
        code, out, err = self._responses[key]
        return ShellResult(
            node=node,
            argv=argv,
            exit_code=code,
            stdout=out,
            stderr=err,
            duration_s=0.0,
        )

    async def close(self) -> None:
        return None


def _verify_key_permissions(path: Path) -> None:
    """Raise if the SSH key file has group/world permissions set (B10)."""
    if not path.exists():
        raise RuntimeError(f"SSH key not found at {path}")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise RuntimeError(
            f"SSH key {path} has insecure permissions {oct(mode)}. Run: chmod 600 {path}"
        )


class AsyncSshRunner:
    """Real ShellRunner using asyncssh. Maintains a per-worker connection pool.

    Security notes:
    - `known_hosts` is always set to a file (never None). Construction fails if
      the file does not exist, so first-time users are forced through the
      `spark-mcp ssh-trust <worker>` workflow.
    - The SSH private key is required to be 0o600 or stricter at construction.
    """

    def __init__(
        self,
        head_node: str,
        workers: list[str],
        ssh_user: str,
        ssh_key_path: Path,
        known_hosts_path: Path,
        max_per_worker: int,
        connect_timeout: int,
    ) -> None:
        _verify_key_permissions(ssh_key_path)
        if not known_hosts_path.exists():
            raise RuntimeError(
                f"SSH known_hosts not found at {known_hosts_path}. "
                "Run `spark-mcp ssh-trust <worker>` for each worker first."
            )
        self._head_node = head_node
        self._workers = workers
        self._ssh_user = ssh_user
        self._ssh_key_path = ssh_key_path
        self._known_hosts_path = known_hosts_path
        self._connect_timeout = connect_timeout
        self._sems: dict[str, asyncio.Semaphore] = {
            w: asyncio.Semaphore(max_per_worker) for w in workers
        }
        self._conns: dict[str, asyncssh.SSHClientConnection] = {}
        self._lock = asyncio.Lock()

    def _is_local(self, node: str) -> bool:
        return node == self._head_node or node in ("localhost", "127.0.0.1")

    def get_pool_size(self, node: str) -> int:
        """Return the number of open asyncssh connections for a worker."""
        conn = self._conns.get(node)
        return 1 if conn is not None and not conn.is_closed() else 0

    async def _get_conn(self, node: str) -> asyncssh.SSHClientConnection:  # pragma: no cover
        # Real SSH connections are exercised by integration tests.
        async with self._lock:
            existing = self._conns.get(node)
            if existing is None or existing.is_closed():
                self._conns[node] = await asyncssh.connect(
                    node,
                    username=self._ssh_user,
                    client_keys=[str(self._ssh_key_path)],
                    known_hosts=str(self._known_hosts_path),
                    connect_timeout=self._connect_timeout,
                )
            return self._conns[node]

    async def run(self, node: str, argv: list[str], *, timeout: float) -> ShellResult:  # noqa: ASYNC109  # pragma: no cover
        # Real local/remote shell invocations are exercised by integration tests.
        start = time.monotonic()
        if self._is_local(node):
            proc = await _spawn_subprocess(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                proc.kill()
                raise
            return ShellResult(
                node=node,
                argv=argv,
                exit_code=proc.returncode or 0,
                stdout=out.decode(errors="replace"),
                stderr=err.decode(errors="replace"),
                duration_s=time.monotonic() - start,
            )
        sem = self._sems.get(node)
        if sem is None:
            raise ValueError(f"Unknown node {node!r}")
        async with sem:
            conn = await self._get_conn(node)
            cmd = shell_escape_argv(argv)
            result = await conn.run(cmd, check=False, timeout=timeout)
            return ShellResult(
                node=node,
                argv=argv,
                exit_code=result.exit_status or 0,
                stdout=str(result.stdout or ""),
                stderr=str(result.stderr or ""),
                duration_s=time.monotonic() - start,
            )

    async def close(self) -> None:  # pragma: no cover
        async with self._lock:
            for conn in self._conns.values():
                conn.close()
            await asyncio.gather(
                *[c.wait_closed() for c in self._conns.values()],
                return_exceptions=True,
            )
            self._conns.clear()


class StateStore:
    """Atomic JSON state store with read/write locking."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def load(self) -> PersistedState:
        async with self._lock:
            if not self._path.exists():
                return PersistedState()
            raw = await asyncio.to_thread(self._path.read_text)
            return PersistedState.model_validate_json(raw)

    async def save(self, state: PersistedState) -> None:
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.parent / (self._path.name + ".tmp")
            await asyncio.to_thread(
                tmp.write_text, state.model_dump_json(indent=2, exclude_none=True)
            )
            await asyncio.to_thread(os.replace, tmp, self._path)
            with contextlib.suppress(OSError):
                self._path.chmod(0o600)


class Cluster:
    """High-level cluster facade: node inventory + shell runner + state."""

    def __init__(
        self,
        settings: ClusterSettings,
        ssh: SshSettings,
        ssh_user: str,
        ssh_key_path: Path,
        runner: ShellRunner | None = None,
        known_hosts_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.ssh = ssh
        if runner is not None:
            self._runner: ShellRunner = runner
        else:
            if known_hosts_path is None:
                raise ValueError(
                    "known_hosts_path is required when constructing the default AsyncSshRunner"
                )
            self._runner = AsyncSshRunner(
                head_node=settings.head_node,
                workers=settings.workers,
                ssh_user=ssh_user,
                ssh_key_path=ssh_key_path,
                known_hosts_path=known_hosts_path,
                max_per_worker=ssh.max_connections_per_worker,
                connect_timeout=ssh.connection_timeout,
            )

    @property
    def runner(self) -> ShellRunner:
        return self._runner

    @property
    def all_nodes(self) -> list[str]:
        return [self.settings.head_node, *self.settings.workers]

    async def run(self, node: str, argv: list[str], *, timeout: float = 60.0) -> ShellResult:  # noqa: ASYNC109
        return await self._runner.run(node, argv, timeout=timeout)

    async def run_all(self, argv: list[str], *, timeout: float = 60.0) -> list[ShellResult]:  # noqa: ASYNC109
        tasks = [self.run(n, argv, timeout=timeout) for n in self.all_nodes]
        return list(await asyncio.gather(*tasks))

    async def run_workers(self, argv: list[str], *, timeout: float = 60.0) -> list[ShellResult]:  # noqa: ASYNC109
        tasks = [self.run(w, argv, timeout=timeout) for w in self.settings.workers]
        return list(await asyncio.gather(*tasks))

    async def aclose(self) -> None:
        await self._runner.close()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Cluster]:
        try:
            yield self
        finally:
            await self.aclose()
