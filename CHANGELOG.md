# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.3] - 2026-04-17

### Fixed

- `spark-tui` crashed on startup with `RuntimeError: Attempted to exit
  cancel scope in a different task than it was entered in`. Cause: the
  previous `McpClient.connect`/`aclose` split used an `AsyncExitStack`
  spanning two asyncio tasks (Textual dispatches `on_mount` and
  `on_unmount` on separate tasks). Fix: the MCP client now opens a
  fresh `ClientSession` per call — the server is already `stateless_http=True`
  so there is no handshake overhead beyond the initial `initialize`.
  The client's `connect()` and `aclose()` are now no-ops kept for API
  compatibility.
- The TUI reconnect loop now probes connectivity with `health_check`
  instead of re-entering/re-closing a persistent session.

## [0.1.2] - 2026-04-17

### Changed

- **Zero-config TUI on the head node.** When `~/.config/spark-tui/config.toml`
  does not exist and spark-mcp is installed locally, `spark-tui` now
  auto-discovers the server by reading `~/.config/spark-mcp/config.toml`
  for the port and `~/.config/spark-mcp/.env` for the bearer token.
  Builds an implicit single-profile config pointing at
  `http://127.0.0.1:<port>/mcp`.
- A dedicated `~/.config/spark-tui/config.toml` is still honored and
  remains the way to configure multi-profile / remote TUI setups.
- README quickstart step 10 collapsed from ~14 lines of TUI config to
  just `spark-tui`.

## [0.1.1] - 2026-04-17

### Fixed

- `~`, `$HOME`, and `$USER` in `SPARK_MCP_SSH_KEY_PATH` are now expanded
  before the SSH-key permission check. Previously a literal
  `~/.ssh/id_ed25519` would fail with `RuntimeError: SSH key not found
  at ~/.ssh/id_ed25519`.
- Head-only deployments (`[cluster].workers = []`) no longer require an
  SSH key or `known_hosts` file — the server skips SSH setup entirely.
- Clusters with workers now produce an actionable error at startup
  ("Run `spark-mcp ssh-trust <worker>` for each worker: ...") instead
  of the opaque internal `ValueError: known_hosts_path is required`.

### Changed

- README quickstart now covers: `spark-mcp check`, the required
  `ssh-trust <worker>` step before `systemctl enable`, the
  `/health` + `/metrics` smoke-test, and the TUI config file setup.

## [0.1.0] - 2026-04-17

### Added

- `spark-mcp` MCP server with FastMCP tool surface for DGX Spark clusters:
  - Recipe CRUD (create/update/delete/validate/get/list) with path-traversal
    guard, 1 MiB YAML size cap, TOCTOU-safe exclusive-create, and idempotent
    delete.
  - Cluster lifecycle (`launch_recipe`, `stop_cluster` with per-node
    container discovery, `restart_cluster` from persisted `last_launch_args`,
    `wait_ready`).
  - Monitoring (`get_cluster_status`, `get_gpu_status`, `get_container_logs`,
    `tail_logs`).
  - Model management (`list_cached_models` local + per-worker SSH scan,
    `download_model` with interconnect copy, `get_download_progress`,
    `cancel_download`).
  - Discovery (`search_huggingface`, `get_cluster_info`, `health_check`).
  - MCP resources: `spark://recipes`, `spark://recipes/{name}`,
    `spark://cluster/status`, `spark://cluster/gpu`, `spark://cache/models`,
    `spark://logs/{node}`.
- HTTP transport with Starlette lifespan around `mcp.session_manager.run()`,
  timing-safe bearer auth (`secrets.compare_digest`), per-IP rate limiting,
  CORS default-deny, Prometheus `/metrics` (protected by default) and
  public `/health`; stdio fallback for local testing.
- `spark-mcp init` CLI that generates a 32+ char bearer token, writes
  `config.toml` (0o644), `.env` (0o600), and the systemd unit into
  `~/.config/spark-mcp/` (not `/tmp`, to avoid symlink attacks).
- `spark-mcp ssh-trust <worker>` for hostname-validated `ssh-keyscan`.
- Strict SSH key permission check (`_verify_key_permissions`) and mandatory
  `known_hosts` file (no more `known_hosts=None` silent MITM risk).
- `spark-skills` Claude skills:
  - `creating-vllm-recipes` — full recipe schema, decision tree, six
    templates, three worked examples (all YAML passes
    `RecipeStore.validate_text`).
  - `troubleshooting-dgx-spark-vllm` — diagnostic workflow + five known-
    issue briefs (SM 12.1 FP8, FlashInfer, NCCL, OOM, Ray init).
  - `choosing-models-for-dgx-spark` — memory-budget math, quantization
    guide, performance expectations.
  - Idempotent `install.sh` for Claude Code and Claude Desktop.
- `spark-tui` Textual TUI with MCP `ClientSession` integration, node-
  status cards, recipes table, log panel, theme cycling (textual-dark,
  textual-light, dracula, nord, solarized-light), keyboard bindings,
  exponential-backoff reconnect, refusal to send tokens over plain HTTP
  to non-localhost URLs.
- Documentation:
  - Root README with architecture ASCII + 8-step quickstart.
  - `docs/installation.md`, `docs/configuration.md`,
    `docs/claude-code-setup.md`, `docs/security.md` (threat model,
    Tailscale/Caddy recipes, SSH hygiene, Docker socket caveat, token
    rotation), `docs/architecture.md` (module map + sequence diagrams),
    `docs/error-codes.md` (every `ErrorInfo.code`).
  - `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`.
- Examples: `config.toml` (CI-verified identical to packaged template),
  `env.example`, three Claude MCP client configs, `docker-compose.yml`
  with single-key SSH mount.
- CI/CD pipeline: `ci.yml` (ruff + mypy + pytest with 80% coverage gate
  on matrix Python 3.11/3.12, plus skill-YAML validation and examples-
  drift detection), `release.yml` (PyPI publish for both packages),
  `docker.yml` (multi-arch GHCR image build).
- `Dockerfile` (python:3.12-slim + openssh-client only).

### Security

- Threat model documented: the bearer token is root-equivalent on the
  cluster. See `docs/security.md`.
- Remote SSH execution uses `shlex.join` to escape argv; no raw shell
  string concatenation.
- Empty or <32-char `SPARK_MCP_AUTH_TOKEN` rejected at config load.
- `spark-mcp init` does not print the generated token to stdout by
  default; `--print-token` refuses under `CI=true` without `--force`.
- `/metrics` requires the bearer token by default; unauthenticated
  `/metrics` is permitted only when bound to `127.0.0.1`.

[0.1.0]: https://github.com/oliveres/spark-mcp-tui/releases/tag/v0.1.0
