# PRD: spark-mcp + spark-skills + spark-tui

Open source toolkit for managing vLLM models across a cluster of DGX Spark nodes (2+).

## Project Vision

`spark-mcp` is an open source MCP (Model Context Protocol) server that provides a programmable interface on top of the community `eugr/spark-vllm-docker` stack. It allows controlling a vLLM cluster across multiple DGX Spark nodes via AI assistants (Claude Code, Claude Desktop, Cursor) and a pleasant terminal TUI.

**Target audience:** owners of 2-6 DGX Spark nodes in home lab or small office environments.

**Value proposition:**
- Replaces manual orchestration of `launch-cluster.sh` / `run-recipe.py` across multiple SSH terminals
- Solves the pain point "stop on head node doesn't stop worker"
- Enables AI assistants to generate, debug, and launch vLLM recipes with awareness of the current cluster state
- Provides a fast TUI for operator-level work

**License:** Apache 2.0 (compatible with eugr/spark-vllm-docker, standard for NVIDIA/AI ecosystem).

## Context

### Integration with spark-vllm-docker (eugr)

`eugr/spark-vllm-docker` is a community wrapper for vLLM + Ray cluster on DGX Spark. It provides:
- Prebuilt vLLM wheels for sm_121 (GB10 architecture)
- Python/bash orchestration (`run-recipe.py`, `launch-cluster.sh`, `hf-download.sh`)
- YAML recipes for popular models
- Mods/patches for model-specific vLLM fixes
- Cluster node autodiscovery (`--discover`)

**spark-mcp does not replace spark-vllm-docker**, it wraps and extends it. Assumption: the user has spark-vllm-docker cloned on the head node.

The project could equally be used with a community fork of spark-vllm-docker, as long as it preserves the same CLI surface (`run-recipe.py`, `launch-cluster.sh`, `hf-download.sh`).

### What we duplicate from spark-vllm-docker and why

spark-mcp intentionally **wraps** vllm-docker scripts rather than reimplementing them. We only parse recipes ourselves (in `recipes.py`) because we need strongly-typed Pydantic models for the MCP API surface. The recipe YAML format is stable; if upstream changes it, we update our schema. All actual model operations (build, download, launch) are delegated to the upstream scripts via subprocess calls.

### Reference implementations
- [vllm-studio](https://github.com/0xSero/vllm-studio) — single-node lifecycle manager for vLLM/SGLang with REST API. Inspiration for recipe schema and UI patterns, but not multi-node.
- [llmfit](https://github.com/AlexsJones/llmfit), [llmserve](https://github.com/AlexsJones/llmserve) — Rust/ratatui TUI, inspiration for UX.
- [Claude Code MCP docs](https://docs.claude.com/en/docs/claude-code/mcp) — reference for MCP integration.

## Architecture

```
                  ┌──────────────────────────────────────┐
                  │          Clients                      │
                  │                                       │
                  │  Claude Code (LAN/VPN)                │
                  │  Claude Desktop                       │
                  │  Cursor, Cline, other MCP clients     │
                  │  spark-tui (localhost or LAN)         │
                  │  Dockebase (future, web UI module)    │
                  └──────────────┬───────────────────────┘
                                 │
                                 │  Streamable HTTP
                                 │  (+ Bearer token auth)
                                 │  stdio fallback for local testing
                                 ▼
┌────────────────────────────────────────────────────────────────┐
│  HEAD NODE (any DGX Spark designated as head)                  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │  spark-mcp daemon (systemd service)                       │ │
│  │  - FastMCP server on :8765 (configurable)                 │ │
│  │  - Bearer token auth                                      │ │
│  │  - SSH connection pool to worker nodes                    │ │
│  │  - Persistent state and cache                             │ │
│  │  - /metrics endpoint for Prometheus                       │ │
│  └──────────┬─────────────────────────────┬──────────────────┘ │
│             │                             │                    │
│             ▼                             ▼                    │
│  ┌──────────────────────┐    ┌───────────────────────────┐    │
│  │ vllm-docker scripts  │    │ docker, nvidia-smi        │    │
│  │ run-recipe.py        │    │ (local + SSH workers)     │    │
│  │ launch-cluster.sh    │    └───────────────────────────┘    │
│  │ hf-download.sh       │                                      │
│  │ recipes/*.yaml       │                                      │
│  └──────────────────────┘                                      │
└─────────────────────────────┬──────────────────────────────────┘
                              │ SSH (key from env)
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
   ┌──────────┐         ┌──────────┐         ┌──────────┐
   │ worker-1 │         │ worker-2 │  ...    │ worker-N │
   │ (Spark)  │         │ (Spark)  │         │ (Spark)  │
   └──────────┘         └──────────┘         └──────────┘
```

### Transport layer

**Streamable HTTP** is the primary transport. Reasons:
- MCP server as a long-running systemd service (persistent state, SSH pool)
- Multiple clients simultaneously (TUI locally, Claude Code remotely, Dockebase)
- More practical for LAN scenarios (user has a Mac on a different floor, Spark in a server room)

**stdio** transport is a fallback for:
- Local development and testing (`mcp inspector`)
- Paranoid mode (don't want to open a port)
- Cases where the client doesn't support HTTP MCP

### Authentication

**Bearer token**. The token is generated during initial installation and stored in a `.env` file with restrictive permissions (0600).

The client sends `Authorization: Bearer <token>` with every HTTP request.

For production scenarios (public internet, multiple users), we recommend:
- Tailscale VPN in front of the MCP server
- Reverse proxy with mTLS (Caddy, Nginx)
- OAuth (future enhancement)

## Configuration

### Two-layer approach

Configuration is split by purpose, with a simple, predictable model:

1. **TOML config file** (`~/.config/spark-mcp/config.toml`)
   - Cluster structure (which nodes, their roles)
   - Tool behavior (ports, paths, limits, log level)
   - Non-sensitive operational parameters
   - Permissions: 0644, safe to share for debugging

2. **Env file** (`~/.config/spark-mcp/.env`)
   - Secrets only (auth token, SSH credentials)
   - Permissions: 0600, never committed to git
   - Loaded by systemd via `EnvironmentFile=`

**No overrides between layers.** Each value lives in exactly one place. To change something, edit the appropriate file and restart the service. This is predictable and avoids the "where is this value coming from" problem.

### TOML config schema

```toml
# ~/.config/spark-mcp/config.toml
#
# Cluster topology and tool behavior.
# Secrets (auth token, SSH credentials) belong in the .env file.

[server]
# Bind address: 0.0.0.0 for LAN access, 127.0.0.1 for local only
host = "0.0.0.0"
port = 8765

# Transport: "http" (primary, network-accessible) or "stdio" (local only)
transport = "http"

# Log verbosity: DEBUG, INFO, WARNING, ERROR
log_level = "INFO"

# Prometheus metrics endpoint at /metrics
metrics_enabled = true

[cluster]
# Human-readable cluster name, shown in clients
name = "my-spark-cluster"

# Head node identifier. Typically "localhost" since spark-mcp
# runs on the head node itself.
head_node = "localhost"

# Worker node SSH targets. Must be reachable via SSH key authentication.
# Format: "hostname", "hostname:port", or "user@hostname"
workers = ["worker-1", "worker-2"]

# Interconnect IP for model distribution (hf-download.sh --copy-to).
# Typically the CX-7 link-local address on the head node.
# Leave empty for auto-detection from routing table.
interconnect_ip = ""

[spark-vllm-docker]
# Path to the cloned eugr/spark-vllm-docker repository (or compatible fork).
# Supports ~ expansion and env vars.
repo_path = "~/spark-vllm-docker"

# Docker container name used by launch-cluster.sh.
# Defaults to the upstream value. Override only if you customized it.
container_name = "vllm_node"

[paths]
# HuggingFace cache directory (shared with vllm-docker via volume mount)
hf_cache = "~/.cache/huggingface"

# State and cache for spark-mcp itself
state_file = "~/.cache/spark-mcp/state.json"
cache_dir = "~/.cache/spark-mcp/"

[ssh]
# Connection pool sizing (per worker)
max_connections_per_worker = 4
connection_timeout = 10  # seconds

[limits]
# Maximum models running concurrently.
# Default: 1 (singleton model contract).
# May be increased when models fit together in VRAM.
max_concurrent_models = 1
```

### Env file schema

```bash
# ~/.config/spark-mcp/.env
#
# Secrets for spark-mcp. Never commit this file to git.
# Permissions: chmod 600

# MCP server authentication token (required).
# Generated during `spark-mcp init`.
SPARK_MCP_AUTH_TOKEN=sk-spark-abc123...

# SSH credentials for worker node access (required).
SPARK_MCP_SSH_USER=your-username
SPARK_MCP_SSH_KEY_PATH=~/.ssh/id_ed25519_shared
```

### Multi-profile support

For users with multiple clusters:

```bash
# Multiple config files side by side
~/.config/spark-mcp/config.toml          # default profile
~/.config/spark-mcp/profiles/homelab.toml
~/.config/spark-mcp/profiles/office.toml

# Corresponding env files
~/.config/spark-mcp/.env                 # default
~/.config/spark-mcp/profiles/homelab.env
~/.config/spark-mcp/profiles/office.env

# Launch with a profile
spark-mcp --profile homelab
spark-tui --profile office
```

When `--profile <n>` is used, both files are loaded from `profiles/<n>.{toml,env}`.

## Component 1: spark-mcp

### Technology
- **Python:** 3.11+ (for `tomllib` in stdlib)
- **Framework:** FastMCP (part of official `mcp[cli]` package)
- **Dependencies:**
  - `mcp[cli]` — FastMCP framework
  - `asyncssh` — async-native SSH
  - `pyyaml` — recipe parsing (vllm-docker recipes are YAML)
  - `pydantic` + `pydantic-settings` — models and config loading
  - `httpx` — HuggingFace API calls
  - `prometheus-client` — metrics endpoint
  - `rich` — nicer CLI output

### Project structure

Nine source files in `src/spark_mcp/`, each with a single clear responsibility:

```
spark-mcp/
├── pyproject.toml
├── README.md
├── LICENSE                       # Apache 2.0
├── CONTRIBUTING.md
├── CHANGELOG.md
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                # lint, tests, type check
│   │   └── release.yml           # auto-release to GitHub
│   └── ISSUE_TEMPLATE/
├── docs/
│   ├── installation.md
│   ├── configuration.md
│   ├── claude-code-setup.md
│   ├── security.md
│   └── architecture.md
├── src/
│   └── spark_mcp/
│       ├── __init__.py
│       ├── __main__.py           # python -m spark_mcp entry
│       ├── cli.py                # `spark-mcp` CLI, argparse, init command
│       ├── server.py             # FastMCP instance + HTTP app + auth + metrics + tool registration
│       ├── config.py             # TOML + env loading via pydantic-settings
│       ├── models.py             # All Pydantic data models (Recipe, NodeStatus, etc.)
│       ├── cluster.py            # Node abstraction, SSH pool, state management
│       ├── operations.py         # Docker + GPU + HuggingFace cache operations
│       ├── recipes.py            # Recipe parsing and management
│       ├── vllm_docker.py        # Subprocess wrapper for run-recipe.py / launch-cluster.sh / hf-download.sh
│       └── templates/
│           ├── config.toml       # default config template
│           ├── env.template      # default env template
│           └── systemd.service   # systemd unit template
├── tests/
│   ├── unit/
│   │   ├── test_config.py        # TOML + env parsing, validation
│   │   ├── test_models.py        # Pydantic model validation
│   │   ├── test_recipes.py       # YAML recipe parsing
│   │   └── test_vllm_docker.py   # subprocess argument construction
│   ├── integration/
│   │   └── test_cluster.py       # real cluster required, marked with @pytest.mark.integration
│   └── fixtures/
│       ├── sample_recipes/       # sample YAML recipes for tests
│       └── mock_ssh_responses.yaml
└── examples/
    ├── config.toml                    # reference config
    ├── env.example                    # reference env
    ├── claude_code_mcp_http.json      # Claude Code HTTP registration
    ├── claude_code_mcp_stdio.json     # Claude Code stdio registration
    ├── claude_desktop_config.json     # Claude Desktop config
    └── docker-compose.yml             # optional container deployment
```

### File responsibilities

- **`cli.py`** — CLI entry point. `spark-mcp` command, `spark-mcp init` subcommand for first-time setup.
- **`server.py`** — FastMCP instance, HTTP application setup, bearer token middleware, Prometheus metrics endpoint, registration of all MCP tools and resources.
- **`config.py`** — loads TOML config file and `.env` into validated Pydantic settings objects. Handles profile selection.
- **`models.py`** — all Pydantic data models: `Recipe`, `RecipeSummary`, `NodeStatus`, `ClusterStatus`, `GpuMetrics`, `LaunchResult`, `OperationResult`, `ErrorInfo`, etc.
- **`cluster.py`** — node abstraction (head + workers), asyncssh connection pool, state file management (what model is currently active, download tracking).
- **`operations.py`** — low-level operations: `docker ps` / `docker stop` / `docker logs` over local or SSH, `nvidia-smi` parsing, HuggingFace cache introspection.
- **`recipes.py`** — YAML recipe parsing into `Recipe` models, validation, CRUD operations on recipe files.
- **`vllm_docker.py`** — subprocess wrappers around upstream eugr scripts (`run-recipe.py`, `launch-cluster.sh`, `hf-download.sh`). This is the bridge between our structured API and their CLI.

### MCP Tools

#### Recipe management

**`list_recipes() -> list[RecipeSummary]`**
Returns all recipes from `{spark-vllm-docker.repo_path}/recipes/`.

```python
class RecipeSummary:
    name: str
    description: str
    model: str                           # HF model ID
    supports_cluster: bool
    supports_solo: bool
    is_model_cached: dict[str, bool]     # per node: {"head": True, "worker-1": False}
    is_active: bool
    path: str
```

**`get_recipe(name: str) -> Recipe`**
Returns the full recipe content as a structured object.

**`create_recipe(name: str, content: str) -> OperationResult`**
Creates a new recipe with YAML schema validation.

**`update_recipe(name: str, content: str) -> OperationResult`**
Updates an existing recipe.

**`delete_recipe(name: str) -> OperationResult`**
Deletes a recipe file. Requires confirmation.

**`validate_recipe(content: str) -> ValidationResult`**
Validates YAML without saving. Useful for AI assistants before committing changes.

#### Cluster lifecycle

**`get_cluster_status() -> ClusterStatus`**
```python
class ClusterStatus:
    cluster_name: str
    head_node: NodeStatus
    workers: list[NodeStatus]
    active_model: ActiveModel | None    # recipe name, uptime, parameters
    ray_status: RayStatus | None        # when cluster is running
    total_vram_gb: float
    used_vram_gb: float

class NodeStatus:
    name: str
    reachable: bool
    hostname: str
    docker_running_containers: list[str]
    gpu: GpuMetrics
    uptime_seconds: int
```

**`launch_recipe(recipe_name: str, overrides: dict = None, setup: bool = False, solo: bool = False) -> LaunchResult`**

Launches a model via `run-recipe.py <n> -d` (daemon mode) with overrides for:
- `port`, `host`, `tensor_parallel`, `gpu_memory_utilization`, `max_model_len`
- `setup=True` — build + download + run if missing
- `solo=True` — single-node mode

Contract: max `limits.max_concurrent_models` models (default 1). Returns error if something is already running.

**`stop_cluster() -> StopResult`**

**Critical function** — solves the main pain point. Procedure:
1. Discover running containers on head and all workers
2. In parallel on all nodes: `docker stop <container>`
3. Wait up to N seconds for termination (configurable)
4. If any node doesn't respond, escalate to `docker kill`
5. Return per-node result

**`restart_cluster() -> RestartResult`**
Stop active + restart the last recipe with the same parameters.

**`wait_ready(recipe_name: str, timeout_s: int = 120) -> ReadyResult`**
Polls the vLLM `/health` endpoint until 200 OK or timeout. For clients that need to know when the model is actually usable.

#### Monitoring

**`get_gpu_status() -> list[GpuMetrics]`**
```python
class GpuMetrics:
    node: str
    name: str                    # "NVIDIA GB10"
    memory_used_mb: int
    memory_total_mb: int
    utilization_pct: int
    temperature_c: int
    power_watts: int
```

**`get_container_logs(node: str, container: str = "vllm_node", lines: int = 100) -> str`**
Last N lines from `docker logs`.

**`tail_logs(node: str, container: str = "vllm_node") -> AsyncIterator[str]`**
Streaming logs (MCP resource, not tool). For the TUI log panel.

#### Model management

**`list_cached_models(node: str = "all") -> list[CachedModel]`**
```python
class CachedModel:
    hf_id: str                   # "Qwen/Qwen3.5-122B-FP8"
    nodes: list[str]             # ["head", "worker-1"] - where the model is cached
    size_gb: float
    last_modified: datetime
```

**`download_model(hf_id: str, distribute_to_workers: bool = True) -> DownloadResult`**
Runs `./hf-download.sh <hf_id> --copy-to <interconnect_ip> --copy-parallel` in an async subprocess. Returns `download_id` for tracking.

**`get_download_progress(download_id: str) -> DownloadProgress`**
Status of an ongoing download job.

**`cancel_download(download_id: str) -> OperationResult`**
Cancels an in-progress download.

#### Discovery and utilities

**`search_huggingface(query: str, limit: int = 10, filter: dict = None) -> list[HfSearchResult]`**
Calls HF API to search for models. Useful for AI assistants.

**`get_cluster_info() -> ClusterInfo`**
Static cluster info (node names, VRAM per node, total capacity, vllm-docker version).

**`health_check() -> HealthStatus`**
Quick check that the MCP server is healthy, SSH pool is OK, vllm-docker repo is accessible.

### MCP Resources

Read-only data the client can fetch anytime:

- `spark://recipes` — recipe list
- `spark://recipes/{name}` — specific recipe content
- `spark://cluster/status` — current state
- `spark://cluster/gpu` — GPU metrics
- `spark://cache/models` — cached models
- `spark://logs/{node}` — live log stream

### State management

Principles:
- **Source of truth is `docker ps`** on all nodes
- **State file is a cache** for faster responses between restarts
- **Always validate against real state** before destructive actions
- State file in `~/.cache/spark-mcp/state.json`:
  ```json
  {
    "version": 1,
    "active_model": {
      "recipe": "qwen3.5-122b-fp8",
      "started_at": "2026-04-16T21:45:00Z",
      "overrides": {"port": 8000},
      "container_id": "abc123",
      "launch_pid": 12345
    },
    "downloads": {
      "dl-uuid-1": {"hf_id": "...", "status": "in_progress", "bytes": 1234}
    }
  }
  ```

### Error handling

All tools return structured responses:

```python
class OperationResult:
    success: bool
    data: Any | None = None
    error: ErrorInfo | None = None

class ErrorInfo:
    code: str              # "RECIPE_NOT_FOUND", "CLUSTER_BUSY", "SSH_FAILED"
    message: str           # human-readable
    details: dict          # actionable context (exit codes, stderr snippet)
    hint: str | None       # for AI assistants: what to try next
```

Error codes are standardized and documented (`docs/error-codes.md`).

### Metrics endpoint

Prometheus-compatible `/metrics` endpoint with:
- `spark_mcp_tool_calls_total{tool="..."}`
- `spark_mcp_tool_duration_seconds{tool="..."}`
- `spark_mcp_cluster_nodes_reachable{node="..."}`
- `spark_mcp_active_model_info{recipe="...",started_at="..."}`
- `spark_mcp_gpu_memory_used_bytes{node="..."}`
- `spark_mcp_ssh_pool_size{node="..."}`

Integrates with existing Prometheus + Grafana stacks.

### Acceptance criteria

- [ ] Server starts via `spark-mcp` CLI and `python -m spark_mcp`
- [ ] Supports HTTP and stdio transport via config
- [ ] Bearer token auth works, returns 401 without token
- [ ] Auto-generates token on first install via `spark-mcp init`
- [ ] Works with `mcp inspector` (official debugger)
- [ ] `list_recipes()` returns all recipes from vllm-docker repo
- [ ] `get_cluster_status()` correctly detects running and idle cluster
- [ ] `launch_recipe("gemma4-26b-a4b")` launches and returns success
- [ ] **`stop_cluster()` reliably stops containers on all nodes** (head + all workers)
- [ ] Claude Code via HTTP MCP works from another machine on the LAN
- [ ] All destructive actions are idempotent
- [ ] `/metrics` endpoint is scrapable by Prometheus
- [ ] Multi-profile config works (2+ clusters from one client)
- [ ] Unit tests cover config, models, recipes, vllm-docker with ≥80% coverage
- [ ] Integration tests pass against a real 2-node Spark cluster

### Deployment

Primarily as a **systemd service** on the head node:

```ini
# /etc/systemd/system/spark-mcp.service
[Unit]
Description=spark-mcp MCP server
After=network.target docker.service

[Service]
Type=simple
User=%i
Group=%i
WorkingDirectory=/home/%i
ExecStart=/home/%i/.local/bin/spark-mcp
EnvironmentFile=/home/%i/.config/spark-mcp/.env
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Install script generates a user-specific unit with correct paths.

Alternatively, a Docker container (`docker-compose.yml` in examples/).

## Testing strategy

### What tests are for

- **For developers (maintainers and contributors):** CI/CD regression detection, executable documentation of expected behavior, confidence when refactoring.
- **For end users: invisible.** Tests are not installed with the package (`pip install spark-mcp` installs only `src/`). Users never run them.

### Unit tests (`tests/unit/`)

Fast, isolated, no real cluster required. Run in CI on every push/PR.

- **`test_config.py`** — TOML parsing, env var loading, profile selection, validation edge cases
- **`test_models.py`** — Pydantic model validation, serialization, edge cases
- **`test_recipes.py`** — YAML recipe parsing with real vllm-docker recipe fixtures, schema validation
- **`test_vllm_docker.py`** — subprocess argument construction (what CLI args we pass to run-recipe.py given a Recipe object)

Mocked SSH responses live in `tests/fixtures/mock_ssh_responses.yaml`. No real SSH connections in unit tests.

### Integration tests (`tests/integration/`)

Require a real cluster. Marked with `@pytest.mark.integration`, skipped by default in CI. Run manually before releases or on self-hosted runners.

- **`test_cluster.py`** — full end-to-end: launch a small model, verify cluster status, stop cluster, verify clean shutdown on all nodes

### Coverage

Target: ≥80% line coverage on the unit test set. Tracked via `coverage.py` and reported in CI (codecov or equivalent).

## Component 2: spark-skills

### Structure
```
spark-skills/
├── README.md
├── install.sh
└── skills/
    ├── creating-vllm-recipes/
    │   ├── SKILL.md
    │   ├── templates/
    │   │   ├── solo_single_gpu.yaml
    │   │   ├── cluster_tp2.yaml
    │   │   ├── cluster_tp4.yaml
    │   │   ├── moe_awq.yaml
    │   │   ├── fp8_native.yaml
    │   │   └── nvfp4.yaml
    │   └── examples/
    │       ├── glm_with_tool_calling.yaml
    │       ├── qwen_vision_language.yaml
    │       └── gemma_with_reasoning.yaml
    │
    ├── troubleshooting-dgx-spark-vllm/
    │   ├── SKILL.md
    │   └── known_issues/
    │       ├── sm121_fp8_crash.md
    │       ├── flashinfer_compat.md
    │       ├── nccl_timeout.md
    │       ├── oom_patterns.md
    │       └── ray_init_failure.md
    │
    └── choosing-models-for-dgx-spark/
        ├── SKILL.md
        └── reference/
            ├── memory_calculations.md
            ├── quantization_guide.md
            └── performance_expectations.md
```

### Skill 1: creating-vllm-recipes

**Trigger:** User wants to create or modify a vLLM recipe for spark-vllm-docker.

**SKILL.md contents:**
- Complete vllm-docker recipe YAML schema with all fields
- Decision tree: when to use solo vs cluster, quantization choice
- Tool calling parsers (glm47, minimax_m2, gemma4, ...)
- Reasoning parsers (deepseek_r1, gemma4, minimax_m2_append_think)
- Build args (--pre-tf, --tf5, --mxfp4, ...)
- Mods (when and which)
- Memory calculations per node size
- Pre-commit checklist

**Integration with spark-mcp:** The skill instructs Claude to call:
- `list_recipes()` for baseline and consistency
- `get_recipe("<similar>")` for inspiration
- `validate_recipe(content)` before `create_recipe()`
- `list_cached_models()` to check if downloading is needed

### Skill 2: troubleshooting-dgx-spark-vllm

**Trigger:** Model crashes, vLLM returns errors, cluster is stuck.

**Contents:**
- Known bugs (SM12.1 FP8 crash, FlashInfer issues, NCCL timeouts)
- How to diagnose from `docker logs`
- When to use `--no-ray` fallback
- OOM patterns and solutions
- Cheatsheet for Ray status interpretation

**Integration:** Calls `get_container_logs()`, recognizes patterns, recommends specific actions.

### Skill 3: choosing-models-for-dgx-spark

**Trigger:** User asks "what should I run", "what fits", "what's fastest for X".

**Contents:**
- Memory limits per cluster size
- FP8/NVFP4/AWQ/INT4 comparison
- Performance expectations (tokens/s per model size)
- Use-case recommendations (coding, reasoning, multimodal)

**Integration:** Calls `get_cluster_info()`, `list_cached_models()`, `search_huggingface()`.

### Acceptance criteria
- [ ] Each skill has a functional `SKILL.md` with clear triggers
- [ ] Templates are valid vllm-docker recipes
- [ ] `install.sh` installs skills to `~/.claude/skills/` and `~/.claude-code/skills/`
- [ ] Tested: "create a recipe for Qwen3-VL-235B" produces valid YAML
- [ ] Tested: "model crashing with OOM" activates troubleshooting skill

## Component 3: spark-tui

### Technology
- **Textual** (https://textual.textualize.io/) — modern TUI framework
- **Python 3.11+**
- **MCP client** for communication with spark-mcp (HTTP on localhost or LAN)

### Project structure

```
spark-tui/
├── pyproject.toml
├── README.md
└── src/
    └── spark_tui/
        ├── __init__.py
        ├── __main__.py
        ├── app.py              # Main Textual App class with all screens and widgets
        ├── mcp_client.py       # MCP client wrapper (HTTP, profile selection)
        └── config.py           # TOML + env loading for TUI profiles
```

Four files. Screens, widgets, and modals are all within `app.py` because Textual encourages co-location of view components.

### Relationship with spark-mcp

spark-tui is a **full MCP client** (not just an import of the spark-mcp module). Reasons:
- Consistency with Claude Code and Dockebase
- Multi-profile support (switch TUI to another cluster)
- Remote TUI (connect from Mac to Spark without Remote-SSH)
- Tests the MCP server via a real client

### Layout

```
╭────────────────────────────────────────────────────────────────╮
│ spark-tui            Cluster: my-homelab       3 nodes        │ <- header
├────────────────────────────────────────────────────────────────┤
│ ┌─ HEAD ────────────────┐ ┌─ worker-1 ──┐ ┌─ worker-2 ──────┐│
│ │ GPU:  87%  94/128 GB  │ │ 85%  92/128 │ │ 81%  88/128     ││
│ │ Temp: 71°C   Pwr: 215W│ │ 69°C  208W  │ │ 67°C  200W      ││
│ │ Cont: vllm_node UP    │ │ vllm_node UP│ │ vllm_node UP    ││
│ └───────────────────────┘ └─────────────┘ └─────────────────┘│
├────────────────────────────────────────────────────────────────┤
│ Recipes (14)                                 [Filter: ___]    │
│ ● qwen3.5-397b-int4-autoround  Intel/Qwen3.5-397B    [RUN]   │
│ ○ glm-4.7-flash-awq            cyankiwi/GLM-4.7-Fl...         │
│ ○ gemma4-26b-a4b               google/gemma-4-26B...         │
│ ○ minimax-m2-awq               cyankiwi/MiniMax-M2... [DL]   │
│ ○ nemotron-3-nano-nvfp4        nvidia/Nemotron-3-Nano...      │
│ ...                                                            │
├────────────────────────────────────────────────────────────────┤
│ Logs: qwen3.5-397b @ head              [auto-scroll ON]       │
│ INFO 04-16 21:45:12 Avg generation throughput: 42.3 tokens/s │
│ INFO 04-16 21:45:13 Running 0 reqs, Waiting: 0 reqs, ...     │
│ ...                                                            │
├────────────────────────────────────────────────────────────────┤
│ [Enter] Start  [S] Stop  [D] Download  [N] New  [E] Edit      │
│ [L] Logs  [F] Filter  [P] Profile  [?] Help  [Q] Quit         │
╰────────────────────────────────────────────────────────────────╯
```

### Panels

1. **Header** — cluster name, node count, health badge
2. **Cluster Status** — horizontal split for each node (head + workers), responsive
3. **Recipes list** — scrollable table with filter, badges (RUN = active, DL = missing model)
4. **Logs** — live tail of active model
5. **Footer** — keybindings + status

### Screens

- **Main** — layout described above
- **Recipe Edit** — modal for YAML editing (Monaco-like editor in Textual)
- **Recipe New** — wizard: select template, customize, validate, save
- **Download Progress** — modal with active downloads and progress bars
- **Profile Selector** — switch between cluster profiles
- **Help** — keybinding cheatsheet

### Keybindings

| Key | Action |
|-----|--------|
| `Enter` | Start selected recipe |
| `S` | Stop active model |
| `R` | Restart active |
| `D` | Download model for selected recipe |
| `N` | New recipe (wizard) |
| `E` | Edit recipe |
| `X` | Delete recipe (confirmation) |
| `L` | Toggle log panel |
| `F` | Filter recipes (typing) |
| `P` | Profile selector (multi-cluster) |
| `/` | Search (vim-style) |
| `?` | Help |
| `Q` | Quit |
| `Tab` | Cycle panels |
| `j/k` or `↑/↓` | Navigation |

### Refresh strategy

- Cluster status: every 3 s (async poll)
- Recipes list: on startup + after actions + manual F5
- Logs: live stream via MCP resource

### Configuration

`~/.config/spark-tui/config.toml`:

```toml
[connection]
# Default profile (can be overridden with --profile)
default_profile = "homelab"

[profiles.homelab]
mcp_url = "http://spark-head.local:8765/mcp"
# Token is read from the TUI env file for security

[profiles.office]
mcp_url = "http://10.0.1.50:8765/mcp"

[ui]
theme = "dracula"        # dark | light | dracula | nord | solarized
refresh_interval_ms = 3000
log_tail_lines = 200
```

```bash
# ~/.config/spark-tui/.env
SPARK_TUI_TOKEN_HOMELAB=sk-spark-abc...
SPARK_TUI_TOKEN_OFFICE=sk-spark-def...
```

### Acceptance criteria
- [ ] Application starts via `spark-tui` command
- [ ] Connects to MCP server (HTTP + token)
- [ ] Shows real-time cluster status
- [ ] Start/stop of models works
- [ ] Logs stream live
- [ ] Multi-profile switching
- [ ] Keyboard-only operation
- [ ] Responsive on wide and narrow terminals
- [ ] Dark/light themes
- [ ] No crashes on MCP server outage (graceful reconnect)

## Installation and setup

### Quickstart

```bash
# 1. Clone project
git clone https://github.com/<you>/spark-mcp ~/spark-mcp
cd ~/spark-mcp

# 2. Install (uv preferred, pip fallback)
curl -LsSf https://astral.sh/uv/install.sh | sh  # if uv not present
uv pip install -e ./spark-mcp
uv pip install -e ./spark-tui

# 3. First setup — generates config and token
spark-mcp init

# Output:
# ✓ Created ~/.config/spark-mcp/config.toml
# ✓ Created ~/.config/spark-mcp/.env (chmod 600)
# ✓ Generated auth token in .env
# ✓ systemd unit written to /tmp/spark-mcp.service
# ✓ To enable: sudo cp /tmp/spark-mcp.service /etc/systemd/system/ && sudo systemctl enable --now spark-mcp
# ✓ Ready. Edit config.toml with your cluster details.

# 4. Edit config (set workers, vllm-docker path)
nano ~/.config/spark-mcp/config.toml

# 5. Edit env (set SSH user and key path)
nano ~/.config/spark-mcp/.env

# 6. Start (foreground for testing)
spark-mcp

# Or as a service:
sudo cp /tmp/spark-mcp.service /etc/systemd/system/
sudo systemctl enable --now spark-mcp

# 7. Install skills into Claude Code
./spark-skills/install.sh

# 8. Register in Claude Code (on your dev machine, not on the Spark)
# Read the token from the server:
TOKEN=$(grep SPARK_MCP_AUTH_TOKEN ~/.config/spark-mcp/.env | cut -d= -f2)
claude mcp add spark-mcp --transport http \
    http://spark-head.local:8765/mcp \
    --header "Authorization: Bearer $TOKEN"

# 9. Start TUI
spark-tui
```

### Docker deployment (alternative)

```yaml
# docker-compose.yml (in examples/)
services:
  spark-mcp:
    image: ghcr.io/<you>/spark-mcp:latest
    restart: unless-stopped
    network_mode: host
    volumes:
      - ~/.ssh:/root/.ssh:ro
      - ~/spark-vllm-docker:/vllm-docker:ro
      - ~/.cache/huggingface:/hf-cache
      - /var/run/docker.sock:/var/run/docker.sock
      - ./config.toml:/config/config.toml:ro
    env_file:
      - ./.env
    environment:
      SPARK_MCP_CONFIG: /config/config.toml
```

## Implementation order

The following sequence respects dependencies between components. **All steps must be completed** — this is not an MVP; the project is released as a coherent whole. The order exists because you cannot test a TUI client without a server, or register skills without tools for them to call.

### spark-mcp core

1. Project scaffolding (pyproject.toml, 9-file structure, CI skeleton, LICENSE, README stub)
2. Config loading (`config.py` + `models.py` — TOML via `tomllib`, env via `pydantic-settings`)
3. Unit tests for config and models
4. Cluster abstraction (`cluster.py` — asyncssh pool, state management)
5. Recipe parsing (`recipes.py` + recipe Pydantic models) + unit tests
6. Low-level operations (`operations.py` — Docker, GPU, HuggingFace cache)
7. vllm-docker subprocess wrappers (`vllm_docker.py`) + unit tests
8. MCP tool registration (`server.py` — all tools listed in "MCP Tools" section)
9. HTTP server + bearer auth + Prometheus metrics endpoint
10. `spark-mcp init` CLI command for first-time setup
11. systemd service template and install logic in `init`
12. Integration tests against a real 2-node Spark cluster

### spark-skills

13. `creating-vllm-recipes` — SKILL.md + templates + examples
14. Test recipe generation via Claude Code using the skill
15. `troubleshooting-dgx-spark-vllm` — SKILL.md + known_issues/
16. `choosing-models-for-dgx-spark` — SKILL.md + reference/
17. `install.sh` script (installs to `~/.claude/skills/` and `~/.claude-code/skills/`)

### spark-tui

18. Textual App skeleton with MCP client
19. Theme system (dark/light/dracula/nord/solarized)
20. Main screen layout (header, cluster status, recipes list, logs)
21. Modals (edit recipe, new recipe wizard, download progress, profile selector, help)
22. Keyboard bindings
23. Connection error handling and graceful reconnect

### Documentation and release

24. README.md — quickstart, screenshots, asciinema
25. docs/ — installation, configuration, claude-code-setup, security, architecture, error-codes
26. CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md
27. Usage examples and asciinema recordings
28. GitHub Actions CI/CD full pipeline (lint, test, coverage, release, docker-image, PyPI)
29. First release v0.1.0

## Known risks and limitations

### SSH latency
Every SSH-calling tool has ~50-200 ms of latency. For bulk operations, use `asyncio.gather()` and a connection pool. `asyncssh` (async-native) is preferred over paramiko + `asyncio.to_thread`.

### State drift
If someone runs `launch-cluster.sh` manually outside spark-mcp, the state file becomes inconsistent. Mitigation: always validate against `docker ps` before critical operations.

### HuggingFace cache separation
Each Spark has its own cache. `download_model` must always use `--copy-to --copy-parallel` over the interconnect IP. Auto-detect interconnect IP from the routing table with a config fallback.

### Multi-cluster state
One spark-mcp = one cluster. For multiple clusters, the user runs multiple instances on different ports, or uses TUI multi-profile.

### Ray cluster init
The first 30-60 s after launch, Ray is initializing. `wait_ready()` polls for real health.

### Token rotation
No automatic token rotation. Document workaround: edit `.env`, restart service.

### Upstream vllm-docker compatibility
spark-mcp depends on the CLI surface of `run-recipe.py`, `launch-cluster.sh`, and `hf-download.sh`. Upstream breaking changes will require spark-mcp updates. Pin known-compatible versions in docs/changelog.

### Recipe schema drift
We parse recipe YAML into our own Pydantic models. If upstream adds fields or changes semantics, our schema must be updated. `recipes.py` includes a docstring pointing to the source of truth (upstream `recipes/*.yaml`).

### Network security
MCP server on `0.0.0.0:8765` is exposed. Documentation must guide users to:
- `127.0.0.1` binding if no remote access is needed
- Tailscale VPN for remote access
- mTLS / reverse proxy for production

## Open source strategy

### Repo structure
**Monorepo** for all three components (spark-mcp, spark-skills, spark-tui):
- Shared release cycle
- Shared data models (recipe schema)
- Single documentation
- Atomic commits across components

### Branding
- **Name:** `spark-mcp` is primary, submodules are `spark-skills` and `spark-tui`
- **Tagline:** "MCP-powered lifecycle management for vLLM on NVIDIA DGX Spark clusters"

### Community
- **GitHub Discussions** for Q&A and feature requests
- **Issues** for bugs and concrete tasks only
- **Label scheme:** `good first issue`, `help wanted`, `needs-discussion`, `upstream-vllm-docker`

### CI/CD
- **GitHub Actions:** lint (ruff), format (black), test (pytest), type check (mypy), coverage (codecov)
- **Release:** semantic-release + auto-changelog + GitHub Releases
- **Docker images:** auto-build on `ghcr.io/<user>/spark-mcp:latest`
- **PyPI:** `spark-mcp` and `spark-tui` as separate packages

### Documentation
- `README.md` — quickstart, screenshots
- `docs/` — comprehensive (markdown or MkDocs)
- `SECURITY.md` — security policy
- `CONTRIBUTING.md` — how to contribute, coding style
- `CODE_OF_CONDUCT.md` — standard
- Asciinema recordings for TUI
- Blog post at release (DGX Spark community, Reddit r/LocalLLaMA)

## Guidelines for implementation

### Workflow
1. **Read the entire PRD** before planning
2. **Propose a complete task breakdown** covering all implementation order steps in one plan — this is a complete project, not an MVP
3. **Iterate per step** — finish one step, test, commit, then continue
4. **Real cluster testing** for integration tests, run against a real 2-node Spark cluster
5. **Conventional commits** — `feat(spark-mcp): add list_recipes`, `fix(tui): reconnect on mcp error`
6. **Git workflow** — feature branch per larger step, merge after testing

### Key rules
1. **No hardcoded values.** Everything via config or args. Not even in tests (use fixtures).
2. **All code comments, docstrings, error messages, and commit messages in English.** User-facing docs can be localized later, code is always English.
3. **Async first.** FastMCP tools can be async; SSH via `asyncssh`.
4. **Type hints everywhere.** Strict Pydantic models.
5. **i18n-ready UI texts in spark-tui** (default English).
6. **Graceful shutdown.** MCP server must close SSH connections, flush state.
7. **Parallelize SSH.** `asyncio.gather()` for bulk operations across nodes.
8. **Pydantic schema validation** for recipes. Schema should come from existing vllm-docker recipes.
9. **Don't break vllm-docker.** spark-mcp is a wrapper; does not modify vllm-docker scripts, only calls them.
10. **Testability.** Abstract SSH operations behind an interface for mocking.
11. **Test everything.** Unit tests for every module in `src/spark_mcp/`, integration tests for cluster operations.
12. **Nine files in `src/spark_mcp/`**, no more. If a new file feels necessary, evaluate whether it fits into an existing one first.

## Reference

### Example vllm-docker recipe (gemma4-26b-a4b.yaml)
```yaml
recipe_version: "1"
name: Gemma4-26B-A4B
description: vLLM serving Gemma4-26B-A4B
model: google/gemma-4-26B-A4B-it
cluster_only: false
solo_only: false
container: vllm-node-tf5
build_args: ["--tf5"]
mods: ["mods/fix-gemma4-tool-parser"]
defaults:
  port: 8000
  host: 0.0.0.0
  tensor_parallel: 2
  gpu_memory_utilization: 0.7
  max_model_len: 262144
command: |
  vllm serve google/gemma-4-26B-A4B-it \
    --max-model-len {max_model_len} \
    ...
```

### vllm-docker CLI examples
```bash
# List recipes
./run-recipe.py --list

# Run as daemon
./run-recipe.py glm-4.7-flash-awq -d

# Solo mode on head only
./run-recipe.py glm-4.7-flash-awq --solo

# Full setup (build + download + run)
./run-recipe.py glm-4.7-flash-awq --setup -d

# Download and distribute model
./hf-download.sh cyankiwi/MiniMax-M2-AWQ --copy-to <interconnect_ip> --copy-parallel
```

### FastMCP HTTP example
```python
from fastmcp import FastMCP

mcp = FastMCP("spark-mcp")

@mcp.tool()
async def list_recipes() -> list[dict]:
    """List all available vLLM recipes from spark-vllm-docker repo."""
    # implementation
    return [...]

if __name__ == "__main__":
    # HTTP mode
    mcp.run(transport="http", host="0.0.0.0", port=8765)
    # Or stdio:
    # mcp.run(transport="stdio")
```

### Claude Code HTTP MCP registration
```bash
claude mcp add spark-mcp \
    --transport http \
    http://spark-head.local:8765/mcp \
    --header "Authorization: Bearer $SPARK_MCP_TOKEN"
```

### References
- spark-vllm-docker: https://github.com/eugr/spark-vllm-docker
- FastMCP docs: https://gofastmcp.com/
- MCP specification: https://spec.modelcontextprotocol.io/
- Textual: https://textual.textualize.io/
- vllm-studio (inspiration): https://github.com/0xSero/vllm-studio

---

**License:** Apache 2.0
**Status:** PRD v0.5 (pre-implementation)
