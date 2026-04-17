# Configuration

spark-mcp has two config layers, strictly separated:

1. **TOML file** (`~/.config/spark-mcp/config.toml`, mode 0o644) — cluster
   topology and tool behavior. Safe to share for debugging.
2. **Env file** (`~/.config/spark-mcp/.env`, mode 0o600) — secrets only
   (auth token, SSH credentials). Never committed.

No overlap between layers. Every value lives in exactly one place.

## Precedence

1. `SPARK_MCP_CONFIG` env var (explicit path to a TOML file).
2. `--profile <name>` selects `~/.config/spark-mcp/profiles/<name>.{toml,env}`.
3. Default `~/.config/spark-mcp/config.toml`.

## TOML sections

### `[server]`

- `host` — bind address. `0.0.0.0` exposes on LAN; `127.0.0.1` = local only.
- `port` — default 8765.
- `transport` — `"http"` (primary) or `"stdio"`.
- `log_level` — `DEBUG | INFO | WARNING | ERROR`. Avoid DEBUG in production;
  it can log Authorization headers.
- `metrics_enabled` — toggles `/metrics` route.
- `metrics_auth` — `"bearer"` (default) or `"none"`. `"none"` is rejected
  when `host != "127.0.0.1"`.
- `rate_limit_per_minute` — per-IP token bucket; 0 disables.
- `cors_allow_origins` — default `[]` (no browser clients).

### `[cluster]`

- `name` — display name.
- `head_node` — usually `"localhost"` since spark-mcp runs on the head.
- `workers` — list of SSH targets (`"worker-1"`, `"user@worker-1"`).
- `interconnect_ip` — CX-7 link-local IP for HF model distribution.

### `[spark-vllm-docker]`

- `repo_path` — path to your clone (supports `~` and `$HOME`/`$USER`).
- `container_name` — matches the upstream default (`vllm_node`).

### `[paths]`

- `hf_cache` — HuggingFace cache directory.
- `state_file` — spark-mcp state cache (mode 0o600 after write).
- `cache_dir` — other cache output.

### `[ssh]`

- `max_connections_per_worker` — semaphore size per worker.
- `connection_timeout` — initial connect timeout in seconds.

### `[limits]`

- `max_concurrent_models` — default 1; keeps the cluster singleton.
- `launch_timeout_s` — `run-recipe.py -d` wall clock (accommodates cold
  starts with `--setup`).
- `stop_timeout_s` — `docker stop -t` before escalating to `docker kill`.
- `max_concurrent_downloads` — cap on parallel `hf-download.sh`.
- `recipe_command_policy` — `"permissive"` (default) or `"vllm-only"`.
  Set to `"vllm-only"` to reject recipes whose `command:` does not start
  with `vllm serve` — useful when sharing a cluster with multiple users.

## Env file schema

```bash
SPARK_MCP_AUTH_TOKEN=sk-spark-...          # required; min 32 chars
SPARK_MCP_SSH_USER=<your-user>             # required
SPARK_MCP_SSH_KEY_PATH=~/.ssh/id_ed25519   # required; must be 0o600
```

## Multi-profile

Run multiple clusters from one machine:

```
~/.config/spark-mcp/
|-- config.toml       # default profile
|-- .env
|-- profiles/
|   |-- homelab.toml
|   |-- homelab.env
|   |-- office.toml
|   +-- office.env
+-- known_hosts       # shared across profiles
```

Launch with `spark-mcp --profile homelab`. The TUI picks up the same
naming via `~/.config/spark-tui/config.toml` (separate profiles section).
