"""FastMCP server wiring: context, tool registration, HTTP transport, auth, metrics.

Inlines security-critical amendments from iteration-3 review:
- A1/B7: Starlette lifespan enters `mcp.session_manager.run()` before yield and
  runs `ctx.aclose()` in finally after the session manager drains.
- A3: stdio transport uses `asyncio.to_thread(mcp.run, "stdio")`.
- A12/B4: BearerAuthMiddleware uses `secrets.compare_digest`; protects /mcp
  and (by default) /metrics; /health stays public.
- B6: ServerContext.create is async so interconnect-IP auto-detection can run.
- B13: in-memory per-IP token-bucket RateLimitMiddleware (cap 10k entries).
- B19: CORSMiddleware default-denies browser origins.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import secrets as _secrets
import time as _time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from .cluster import Cluster, StateStore
from .config import AppConfig
from .models import (
    ActiveModel,
    CachedModel,
    ClusterInfo,
    ClusterStatus,
    DownloadProgress,
    DownloadRecord,
    DownloadResult,
    ErrorInfo,
    GpuMetrics,
    HealthStatus,
    HfSearchResult,
    LaunchArgs,
    LaunchResult,
    NodeStatus,
    OperationResult,
    Recipe,
    RecipeSummary,
    RestartResult,
    StopResult,
    ValidationResult,
)
from .operations import Operations
from .recipes import RecipeStore, validate_recipe_name
from .vllm_docker import VllmDocker

log = logging.getLogger(__name__)


# --- Metrics ---------------------------------------------------------------


def build_metrics() -> tuple[CollectorRegistry, dict[str, Any]]:
    registry = CollectorRegistry()
    metrics = {
        "tool_calls": Counter(
            "spark_mcp_tool_calls_total",
            "MCP tool invocations",
            ["tool"],
            registry=registry,
        ),
        "tool_duration": Histogram(
            "spark_mcp_tool_duration_seconds",
            "MCP tool duration",
            ["tool"],
            buckets=(0.005, 0.05, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0),
            registry=registry,
        ),
        "nodes_reachable": Gauge(
            "spark_mcp_cluster_nodes_reachable",
            "1 if node reachable, 0 otherwise",
            ["node"],
            registry=registry,
        ),
        "active_model": Gauge(
            "spark_mcp_active_model_info",
            "Active model indicator (1 while active)",
            ["recipe", "started_at"],
            registry=registry,
        ),
        "gpu_memory_used": Gauge(
            "spark_mcp_gpu_memory_used_bytes",
            "GPU memory in use (bytes)",
            ["node"],
            registry=registry,
        ),
        "ssh_pool_size": Gauge(
            "spark_mcp_ssh_pool_size",
            "Active SSH connections per worker",
            ["node"],
            registry=registry,
        ),
    }
    return registry, metrics


# --- Server context --------------------------------------------------------


@dataclass
class ServerContext:
    cfg: AppConfig
    cluster: Cluster
    operations: Operations
    recipes: RecipeStore
    vllm_docker: VllmDocker
    state: StateStore
    http: httpx.AsyncClient
    _downloads: dict[str, tuple[DownloadRecord, asyncio.subprocess.Process]] = field(
        default_factory=dict
    )

    @classmethod
    async def create(cls, cfg: AppConfig) -> ServerContext:
        kh_path = cfg.config_path.parent / "known_hosts"
        # Fail-fast with an actionable message when workers require SSH but the
        # operator hasn't run `spark-mcp ssh-trust <worker>` yet.
        if cfg.cluster.workers and not kh_path.exists():
            workers = ", ".join(cfg.cluster.workers)
            raise RuntimeError(
                f"SSH known_hosts not found at {kh_path}. "
                f"Before starting the server, run `spark-mcp ssh-trust <worker>` "
                f"for each worker: {workers}."
            )
        cluster = Cluster(
            cfg.cluster,
            cfg.ssh,
            ssh_user=cfg.secrets.ssh_user,
            ssh_key_path=cfg.secrets.ssh_key_path,
            known_hosts_path=kh_path if kh_path.exists() else None,
        )
        ops = Operations(cluster, hf_cache_dir=cfg.paths.hf_cache)
        vllm = VllmDocker(
            cluster,
            cfg.vllm_docker.repo_path,
            cfg.vllm_docker.container_name,
            ops=ops,
            launch_timeout_s=cfg.limits.launch_timeout_s,
        )
        return cls(
            cfg=cfg,
            cluster=cluster,
            operations=ops,
            recipes=RecipeStore(cfg.vllm_docker.repo_path / "recipes"),
            vllm_docker=vllm,
            state=StateStore(cfg.paths.state_file),
            http=httpx.AsyncClient(timeout=15.0, follow_redirects=False),
        )

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.cluster.aclose()


# --- Tool + resource registration -----------------------------------------


def _instrument(
    metrics: dict[str, Any] | None, name: str
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator: bumps tool-call counter and observes duration when metrics is set.

    Uses functools.wraps so FastMCP reads the wrapped function's __name__ /
    __doc__ / signature — not "wrapper". Previous versions silently registered
    every tool as "wrapper", making only the last-decorated tool reachable.
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        if metrics is None:
            return fn

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            metrics["tool_calls"].labels(tool=name).inc()
            start = _time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                metrics["tool_duration"].labels(tool=name).observe(_time.perf_counter() - start)

        return wrapper

    return decorator


def _refresh_metrics(
    metrics: dict[str, Any] | None,
    statuses: list[NodeStatus] | None = None,
    active: ActiveModel | None = None,
) -> None:
    if metrics is None:
        return
    if statuses is not None:
        for s in statuses:
            metrics["nodes_reachable"].labels(node=s.name).set(1 if s.reachable else 0)
            if s.gpu is not None:
                metrics["gpu_memory_used"].labels(node=s.name).set(
                    s.gpu.memory_used_mb * 1024 * 1024
                )
    metrics["active_model"].clear()
    if active is not None:
        metrics["active_model"].labels(
            recipe=active.recipe, started_at=active.started_at.isoformat()
        ).set(1)


def build_mcp(ctx: ServerContext, metrics: dict[str, Any] | None = None) -> FastMCP:
    """Build a FastMCP instance with every PRD-listed tool and resource registered."""
    mcp = FastMCP(
        "spark-mcp",
        streamable_http_path="/",  # mount root, avoids /mcp/mcp double prefix
        stateless_http=True,  # no Mcp-Session-Id handshake required
        json_response=True,
    )
    cfg = ctx.cfg

    # ---- Recipe management ----

    @mcp.tool()
    @_instrument(metrics, "list_recipes")
    async def list_recipes() -> list[RecipeSummary]:
        """List every vLLM recipe available on this cluster."""
        summaries = await ctx.recipes.list_recipes()
        state = await ctx.state.load()
        active = state.active_model.recipe if state.active_model else None
        for s in summaries:
            if s.name == active:
                s.is_active = True
        return summaries

    @mcp.tool()
    @_instrument(metrics, "get_recipe")
    async def get_recipe(name: str) -> Recipe:
        """Return the parsed Recipe for the given filename-slug name."""
        validate_recipe_name(name)
        return await ctx.recipes.load_recipe(name)

    @mcp.tool()
    @_instrument(metrics, "create_recipe")
    async def create_recipe(name: str, content: str) -> OperationResult:
        """Create a new recipe file from validated YAML content."""
        return await ctx.recipes.create_recipe(name, content)

    @mcp.tool()
    @_instrument(metrics, "update_recipe")
    async def update_recipe(name: str, content: str) -> OperationResult:
        """Replace an existing recipe file (atomic write)."""
        return await ctx.recipes.update_recipe(name, content)

    @mcp.tool()
    @_instrument(metrics, "delete_recipe")
    async def delete_recipe(name: str) -> OperationResult:
        """Delete a recipe. Idempotent — success whether the file existed or not."""
        return await ctx.recipes.delete_recipe(name)

    @mcp.tool()
    @_instrument(metrics, "validate_recipe")
    async def validate_recipe(content: str) -> ValidationResult:
        """Validate recipe YAML without writing it to disk."""
        return await ctx.recipes.validate_text(content)

    # ---- Cluster lifecycle ----

    @mcp.tool()
    @_instrument(metrics, "get_cluster_status")
    async def get_cluster_status() -> ClusterStatus:
        """Return aggregated per-node status plus active-model info."""
        statuses = await ctx.operations.all_node_status()
        head = next(s for s in statuses if s.name == cfg.cluster.head_node)
        workers = [s for s in statuses if s.name != cfg.cluster.head_node]
        state = await ctx.state.load()
        total_vram = sum((s.gpu.memory_total_mb / 1024 if s.gpu else 0) for s in statuses)
        used_vram = sum((s.gpu.memory_used_mb / 1024 if s.gpu else 0) for s in statuses)
        _refresh_metrics(metrics, statuses=statuses, active=state.active_model)
        return ClusterStatus(
            cluster_name=cfg.cluster.name,
            head_node=head,
            workers=workers,
            active_model=state.active_model,
            total_vram_gb=total_vram,
            used_vram_gb=used_vram,
        )

    @mcp.tool()
    @_instrument(metrics, "launch_recipe")
    async def launch_recipe(
        recipe_name: str,
        overrides: dict[str, Any] | None = None,
        setup: bool = False,
        solo: bool = False,
    ) -> LaunchResult:
        """Launch a recipe via run-recipe.py -d. Enforces max_concurrent_models."""
        validate_recipe_name(recipe_name)
        state = await ctx.state.load()
        if state.active_model and cfg.limits.max_concurrent_models <= 1:
            return LaunchResult(
                success=False,
                recipe=recipe_name,
                error=ErrorInfo(
                    code="CLUSTER_BUSY",
                    message=f"Active model {state.active_model.recipe!r}; stop it first.",
                    hint="Call stop_cluster before launching another.",
                ),
            )
        args = LaunchArgs(
            recipe_name=recipe_name,
            overrides=overrides or {},
            setup=setup,
            solo=solo,
        )
        result = await ctx.vllm_docker.launch_recipe(args)
        if result.success:
            state.active_model = ActiveModel(
                recipe=recipe_name,
                started_at=datetime.now(tz=UTC),
                overrides=overrides or {},
            )
            state.last_launch_args = args.model_dump()
            await ctx.state.save(state)
            _refresh_metrics(metrics, active=state.active_model)
        return result

    @mcp.tool()
    @_instrument(metrics, "stop_cluster")
    async def stop_cluster() -> StopResult:
        """Stop the active model on every node (head + workers)."""
        result = await ctx.vllm_docker.stop_all(timeout_s=cfg.limits.stop_timeout_s)
        if result.success:
            state = await ctx.state.load()
            state.active_model = None
            await ctx.state.save(state)
            _refresh_metrics(metrics, active=None)
        return result

    @mcp.tool()
    @_instrument(metrics, "restart_cluster")
    async def restart_cluster() -> RestartResult:
        """Stop the active model and relaunch with the persisted last-launch args."""
        state = await ctx.state.load()
        if state.last_launch_args is None:
            return RestartResult(
                success=False, stopped=StopResult(success=False, per_node={}), launched=None
            )
        stopped = await ctx.vllm_docker.stop_all(timeout_s=cfg.limits.stop_timeout_s)
        if not stopped.success:
            return RestartResult(success=False, stopped=stopped)
        launched = await ctx.vllm_docker.launch_recipe(LaunchArgs(**state.last_launch_args))
        return RestartResult(success=launched.success, stopped=stopped, launched=launched)

    # ---- Monitoring ----

    @mcp.tool()
    @_instrument(metrics, "get_gpu_status")
    async def get_gpu_status() -> list[GpuMetrics]:
        """Per-node GPU metrics."""
        tasks = [ctx.operations.gpu_metrics(n) for n in ctx.cluster.all_nodes]
        return list(await asyncio.gather(*tasks))

    @mcp.tool()
    @_instrument(metrics, "get_container_logs")
    async def get_container_logs(node: str, container: str | None = None, lines: int = 100) -> str:
        """Last N lines of docker logs for the given container."""
        if node not in ctx.cluster.all_nodes:
            raise ValueError(f"Unknown node {node!r}")
        lines = max(1, min(lines, 10_000))
        return await ctx.operations.container_logs(
            node, container or cfg.vllm_docker.container_name, lines
        )

    @mcp.tool()
    @_instrument(metrics, "tail_logs")
    async def tail_logs(node: str, container: str | None = None, lines: int = 200) -> str:
        """Snapshot of the most recent container log lines (streaming deferred to v0.2)."""
        if node not in ctx.cluster.all_nodes:
            raise ValueError(f"Unknown node {node!r}")
        bounded = max(1, min(lines, 10_000))
        return await ctx.operations.container_logs(
            node, container or cfg.vllm_docker.container_name, bounded
        )

    # ---- Model management ----

    @mcp.tool()
    @_instrument(metrics, "list_cached_models")
    async def list_cached_models(node: str = "all") -> list[CachedModel]:
        """List HuggingFace-cached models. 'all' scans every known node."""
        if node == "all":
            tasks = [ctx.operations.list_cached_models()] + [
                ctx.operations.list_cached_models_remote(w) for w in cfg.cluster.workers
            ]
            groups = await asyncio.gather(*tasks, return_exceptions=True)
            merged: dict[str, CachedModel] = {}
            for grp in groups:
                if isinstance(grp, BaseException):
                    continue
                for m in grp:
                    existing = merged.get(m.hf_id)
                    if existing is None:
                        merged[m.hf_id] = m
                    else:
                        existing.nodes = sorted(set(existing.nodes) | set(m.nodes))
            return list(merged.values())
        if node in (cfg.cluster.head_node, "localhost"):
            return await ctx.operations.list_cached_models()
        if node in cfg.cluster.workers:
            return await ctx.operations.list_cached_models_remote(node)
        raise ValueError(f"Unknown node {node!r}")

    @mcp.tool()
    @_instrument(metrics, "download_model")
    async def download_model(hf_id: str, distribute_to_workers: bool = True) -> DownloadResult:
        """Start an hf-download.sh download in the background."""
        # Gate on max_concurrent_downloads
        active = sum(1 for d, _ in ctx._downloads.values() if d.status == "in_progress")
        if active >= cfg.limits.max_concurrent_downloads:
            raise RuntimeError(
                f"max_concurrent_downloads={cfg.limits.max_concurrent_downloads} reached"
            )
        interconnect = cfg.cluster.interconnect_ip if distribute_to_workers else None
        # start_download now raises with a clear message if hf-download.sh is
        # missing / non-executable / exits within 500 ms. We propagate that
        # as the MCP tool error so the TUI/Claude sees the real failure.
        result, proc = await ctx.vllm_docker.start_download(hf_id, interconnect or None)
        record = DownloadRecord(
            download_id=result.download_id,
            hf_id=hf_id,
            status="in_progress",
            started_at=result.started_at,
        )
        ctx._downloads[result.download_id] = (record, proc)
        state = await ctx.state.load()
        state.downloads[result.download_id] = record
        await ctx.state.save(state)
        return result

    @mcp.tool()
    @_instrument(metrics, "get_download_progress")
    async def get_download_progress(download_id: str) -> DownloadProgress:
        """Report status of a running download."""
        if download_id not in ctx._downloads:
            return DownloadProgress(
                download_id=download_id,
                status="failed",
                bytes_transferred=0,
                error="not found",
            )
        record, proc = ctx._downloads[download_id]
        if proc.returncode is None:
            return DownloadProgress(
                download_id=download_id, status="in_progress", bytes_transferred=0
            )
        record.status = "completed" if proc.returncode == 0 else "failed"
        state = await ctx.state.load()
        state.downloads[download_id] = record
        await ctx.state.save(state)
        return DownloadProgress(
            download_id=download_id,
            status=record.status,
            bytes_transferred=0,
            error=None if record.status == "completed" else f"exit {proc.returncode}",
        )

    @mcp.tool()
    @_instrument(metrics, "cancel_download")
    async def cancel_download(download_id: str) -> OperationResult:
        """Terminate an in-progress download; fall back to kill after 10 s."""
        if download_id not in ctx._downloads:
            return OperationResult(
                success=False,
                error=ErrorInfo(code="DOWNLOAD_NOT_FOUND", message="Unknown download id"),
            )
        record, proc = ctx._downloads[download_id]
        if proc.returncode is not None:
            return OperationResult(
                success=True, data={"download_id": download_id, "status": "already_complete"}
            )
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        record.status = "cancelled"
        state = await ctx.state.load()
        state.downloads[download_id] = record
        await ctx.state.save(state)
        return OperationResult(success=True, data={"download_id": download_id})

    # ---- Discovery / utility ----

    @mcp.tool()
    @_instrument(metrics, "search_huggingface")
    async def search_huggingface(
        query: str, limit: int = 10, filter: dict[str, Any] | None = None
    ) -> list[HfSearchResult]:
        """Search the Hugging Face Hub for models matching the query."""
        if len(query) > 200 or not query:
            raise ValueError("query must be 1..200 printable characters")
        limit = max(1, min(limit, 100))
        params: dict[str, Any] = {"search": query, "limit": limit}
        if filter:
            params.update(filter)
        resp = await ctx.http.get("https://huggingface.co/api/models", params=params)
        resp.raise_for_status()
        data = resp.json()
        return [HfSearchResult.model_validate(item) for item in data[:limit]]

    @mcp.tool()
    @_instrument(metrics, "get_cluster_info")
    async def get_cluster_info() -> ClusterInfo:
        """Static cluster metadata (nodes, VRAM totals)."""
        tasks = [ctx.operations.gpu_metrics(n) for n in ctx.cluster.all_nodes]
        metrics_list = await asyncio.gather(*tasks, return_exceptions=True)
        vram_per_node: dict[str, float] = {}
        for n, m in zip(ctx.cluster.all_nodes, metrics_list, strict=True):
            if isinstance(m, BaseException):
                vram_per_node[n] = 0.0
            else:
                vram_per_node[n] = m.memory_total_mb / 1024
        return ClusterInfo(
            name=cfg.cluster.name,
            nodes=ctx.cluster.all_nodes,
            vram_per_node_gb=vram_per_node,
            total_vram_gb=sum(vram_per_node.values()),
        )

    @mcp.tool()
    @_instrument(metrics, "health_check")
    async def health_check() -> HealthStatus:
        """Lightweight end-to-end health check — repo + SSH reachability."""
        repo_ok = cfg.vllm_docker.repo_path.exists()
        ssh_results = await asyncio.gather(
            *[ctx.cluster.run(n, ["true"], timeout=3.0) for n in cfg.cluster.workers],
            return_exceptions=True,
        )
        ssh_ok = all(not isinstance(r, BaseException) and r.exit_code == 0 for r in ssh_results)
        return HealthStatus(
            ok=repo_ok and ssh_ok,
            details={"repo_path_exists": repo_ok, "ssh_ok": ssh_ok},
        )

    # ---- Resources ----

    @mcp.resource("spark://recipes")
    async def resource_recipes() -> list[RecipeSummary]:
        return await ctx.recipes.list_recipes()

    @mcp.resource("spark://recipes/{name}")
    async def resource_recipe(name: str) -> Recipe:
        validate_recipe_name(name)
        return await ctx.recipes.load_recipe(name)

    @mcp.resource("spark://cluster/status")
    async def resource_cluster_status() -> ClusterStatus:
        return await get_cluster_status()  # type: ignore[no-any-return]

    @mcp.resource("spark://cluster/gpu")
    async def resource_gpu() -> list[GpuMetrics]:
        return await get_gpu_status()  # type: ignore[no-any-return]

    @mcp.resource("spark://cache/models")
    async def resource_cache_models() -> list[CachedModel]:
        return await ctx.operations.list_cached_models()

    @mcp.resource("spark://logs/{node}")
    async def resource_logs(node: str) -> str:
        return await ctx.operations.container_logs(
            node, cfg.vllm_docker.container_name, (cfg.server.log_level and 200) or 200
        )

    return mcp


# --- HTTP transport + auth + metrics --------------------------------------


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Timing-safe bearer auth. Protects /mcp always; /metrics when protect_metrics."""

    def __init__(self, app: Any, token: str, protect_metrics: bool) -> None:
        super().__init__(app)
        self._expected = f"Bearer {token}".encode()
        self._protect_metrics = protect_metrics

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        needs_auth = path.startswith("/mcp") or (
            self._protect_metrics and path.startswith("/metrics")
        )
        if not needs_auth:
            return await call_next(request)
        header = request.headers.get("authorization", "").encode()
        if not _secrets.compare_digest(header, self._expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


_LOOPBACK_IPS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-IP token bucket; evicts oldest entries when over cap.

    Loopback clients (127.0.0.1, ::1, localhost) are exempt — they share the
    host's trust boundary with the server, so the rate limit exists to protect
    against *remote* attackers, not the operator's own TUI / scripts.
    """

    _MAX_ENTRIES = 10_000

    def __init__(self, app: Any, requests_per_minute: int) -> None:
        super().__init__(app)
        self._limit = requests_per_minute
        self._buckets: dict[str, tuple[float, int]] = {}

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if self._limit <= 0:
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        if ip in _LOOPBACK_IPS:
            return await call_next(request)
        now = _time.monotonic()
        window, count = self._buckets.get(ip, (now, 0))
        if now - window > 60:
            window, count = now, 0
        count += 1
        self._buckets[ip] = (window, count)
        if len(self._buckets) > self._MAX_ENTRIES:
            oldest = sorted(self._buckets.items(), key=lambda kv: kv[1][0])[
                : self._MAX_ENTRIES // 10
            ]
            for key, _ in oldest:
                self._buckets.pop(key, None)
        if count > self._limit:
            return JSONResponse(
                {"error": "rate_limited"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        return await call_next(request)


async def build_http_app(
    cfg: AppConfig,
) -> tuple[Starlette, ServerContext, dict[str, Any]]:
    """Async factory: wires ServerContext, FastMCP, middleware, lifespan, routes."""
    if cfg.server.metrics_auth == "none" and cfg.server.host not in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        raise RuntimeError(
            "Unauthenticated /metrics on non-loopback is unsafe. "
            "Either set metrics_auth='bearer' or host='127.0.0.1'."
        )

    registry, metrics = build_metrics()
    ctx = await ServerContext.create(cfg)
    mcp = build_mcp(ctx, metrics)
    mcp_app = mcp.streamable_http_app()

    async def metrics_endpoint(_: Request) -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    async def health_endpoint(_: Request) -> Response:
        return JSONResponse({"ok": True})

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):  # type: ignore[no-untyped-def]
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await ctx.aclose()

    middleware = [
        Middleware(RateLimitMiddleware, requests_per_minute=cfg.server.rate_limit_per_minute),
        Middleware(
            CORSMiddleware,
            allow_origins=cfg.server.cors_allow_origins,
            allow_methods=["POST", "GET"],
            allow_headers=["authorization", "content-type", "mcp-session-id"],
        ),
        Middleware(
            BearerAuthMiddleware,
            token=cfg.secrets.auth_token.get_secret_value(),
            protect_metrics=(cfg.server.metrics_auth == "bearer"),
        ),
    ]
    # Mount both "/mcp" and "/mcp/" at the same ASGI app. Without this,
    # Starlette issues a 307 redirect from /mcp -> /mcp/, and some MCP
    # clients (Claude Code) strip the Authorization header across redirects
    # for security, so the follow-up request arrives unauthenticated.
    routes: list[Any] = [
        Mount("/mcp/", app=mcp_app),
        Mount("/mcp", app=mcp_app),
        Route("/health", health_endpoint, methods=["GET"]),
    ]
    if cfg.server.metrics_enabled:
        routes.append(Route("/metrics", metrics_endpoint, methods=["GET"]))

    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
    return app, ctx, metrics


async def serve(cfg: AppConfig) -> None:
    """Entry point used by `spark-mcp serve`. Async end-to-end."""
    import uvicorn

    if cfg.server.transport == "stdio":
        ctx = await ServerContext.create(cfg)
        mcp = build_mcp(ctx, metrics=None)
        try:
            await asyncio.to_thread(mcp.run, "stdio")
        finally:
            await ctx.aclose()
        return

    if cfg.server.log_level == "DEBUG":
        log.warning("log_level=DEBUG may leak Authorization headers; prefer INFO in production.")
    app, _, _ = await build_http_app(cfg)
    uv_cfg = uvicorn.Config(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.server.log_level.lower(),
        lifespan="on",
    )
    await uvicorn.Server(uv_cfg).serve()


__all__ = [
    "BearerAuthMiddleware",
    "RateLimitMiddleware",
    "ServerContext",
    "build_http_app",
    "build_mcp",
    "build_metrics",
    "serve",
]


# Quiet unused-import warning for uuid (kept for ServerContext debugging helpers).
_ = uuid
