# Architecture

## Layers

```
Clients (Claude Code, Claude Desktop, Cursor, spark-tui, Dockebase)
                         |
                         | Streamable HTTP + Bearer token
                         v
+---------------------------------------------------------------+
|  spark-mcp daemon                                             |
|  - FastMCP (mcp[cli] >= 1.2) on :8765                         |
|  - BearerAuthMiddleware (compare_digest, protect_metrics)     |
|  - RateLimitMiddleware (per-IP token bucket, cap 10k IPs)     |
|  - CORSMiddleware (default-deny origins)                      |
|  - Prometheus /metrics                                        |
|  - ServerContext: Cluster + Operations + VllmDocker +         |
|    RecipeStore + StateStore + shared httpx.AsyncClient        |
+------------+---------------------------+----------------------+
             |                           |
   asyncssh  |                           |  subprocess
   pool      v                           v
       +----------+                +--------------+
       | workers  |                | head-node    |
       | (docker, |                | run-recipe,  |
       |  GPU)    |                | launch-cluster,
       +----------+                | hf-download  |
                                   +--------------+
```

## Modules (src/spark_mcp/)

- `__init__.py` - version string.
- `__main__.py` - `python -m spark_mcp` shim.
- `cli.py` - argparse front-end: `init`, `serve`, `check`, `version`, `ssh-trust`.
- `config.py` - TOML + env loader via pydantic-settings; profile routing.
- `models.py` - every Pydantic model used on the wire (Recipe,
  ClusterStatus, GpuMetrics, LaunchArgs, etc.).
- `cluster.py` - asyncssh pool with `known_hosts` enforcement, shlex-
  escaped remote exec, StateStore with atomic writes.
- `operations.py` - docker / nvidia-smi / HF-cache helpers on top of
  `Cluster.run`.
- `recipes.py` - YAML parsing + path-traversal-safe CRUD.
- `vllm_docker.py` - argv builders + `VllmDocker` wrapper (launch,
  stop_all with per-node container discovery, wait_ready, start_download).
- `server.py` - FastMCP tool registration, Starlette app, bearer auth
  middleware, rate limit middleware, CORS, Prometheus metrics,
  `serve(cfg)` entry point.

Nine content modules. The `templates/` directory ships the packaged
`config.toml`, `.env` template, and systemd unit; it is data, not a
module.

## Sequence diagrams

### launch_recipe

```
client      server.launch_recipe      StateStore      VllmDocker      Cluster
   |                |                       |              |             |
   |  JSON-RPC -->  |                       |              |             |
   |                |-- load() ------------>|              |             |
   |                |<-- active_model ------|              |             |
   |                |-- enforce max_concurrent_models -----|             |
   |                |--                                    |             |
   |                |-- build_run_recipe_argv ------------>|             |
   |                |                                      |-- run(head, argv) -->|
   |                |                                      |   (local subprocess)  |
   |                |                                      |<----------------------|
   |                |-- save(active_model, last_launch) -->|              |
   |   <- result  --|                       |              |             |
```

### stop_cluster (per-node discovery, A7)

```
server.stop_cluster   VllmDocker.stop_all   Operations.list_containers   Cluster
    |                        |                         |                     |
    |--                      |                         |                     |
    |-- stop_all() --------->|                         |                     |
    |                        | for each node in parallel:                    |
    |                        |--- list_containers --->|                      |
    |                        |                        |-- docker ps ---------->|
    |                        |                        |<----------------------|
    |                        |<- running -------------|                      |
    |                        |-- if container in running:                    |
    |                        |   docker stop -t 30 ---------------------->|
    |                        |   if fail: docker kill --------------------->|
    |                        | aggregate StopResult                           |
    |<-- StopResult ---------|                                                |
```

### download_model

```
client   server.download_model   VllmDocker.start_download   hf-download.sh
  |              |                       |                           |
  |--- hf_id --->|                       |                           |
  |              |-- max_concurrent_downloads gate                   |
  |              |-- start_download ---->|                           |
  |              |                       |-- build argv + spawn ---->|
  |              |                       |<-- Process handle         |
  |              |-- record DownloadRecord into state                 |
  |<-- id -------|                       |                           |
```

## State schema

`~/.cache/spark-mcp/state.json` (mode 0o600):

```json
{
  "version": 1,
  "active_model": {
    "recipe": "gemma4-26b-a4b",
    "started_at": "2026-04-17T12:34:56+00:00",
    "overrides": {"tensor_parallel": 2},
    "container_id": null,
    "launch_pid": null
  },
  "last_launch_args": {
    "recipe_name": "gemma4-26b-a4b",
    "overrides": {"tensor_parallel": 2},
    "setup": false,
    "solo": false
  },
  "downloads": {}
}
```

State is a cache, not source of truth - `docker ps` on every node wins.

## Testing layers

- **Unit tests** (`tests/unit/`) — pure or FakeShellRunner-based. Covers
  config, models, recipes, cluster abstractions, operations, argv
  builders, auth middleware. ≥80% line+branch coverage gate.
- **Integration tests** (`tests/integration/`) — exercise the full
  server via `ClientSession` against a live cluster. Gated on env vars;
  skipped by default.
