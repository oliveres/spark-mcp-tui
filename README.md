# spark-mcp

**MCP-powered lifecycle management for vLLM on NVIDIA DGX Spark clusters.**

![status: pre-release](https://img.shields.io/badge/status-pre--release-orange)
![license: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)
![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

spark-mcp turns `eugr/spark-vllm-docker` into a programmable surface for
AI assistants (Claude Code, Claude Desktop, Cursor) and a pleasant
terminal TUI. It solves the pain of stitching `launch-cluster.sh`,
`run-recipe.py`, and `hf-download.sh` across several SSH terminals, and
gives you a first-class "stop the whole cluster reliably" primitive.

## What's in this monorepo

- [`spark-mcp/`](spark-mcp) - FastMCP server wrapping `spark-vllm-docker`
- [`spark-skills/`](spark-skills) - three Claude skills (recipe authoring,
  troubleshooting, model selection)
- [`spark-tui/`](spark-tui) - Textual terminal UI

Full product requirements live in [`docs/SPARK_MCP_PRD.md`](docs/SPARK_MCP_PRD.md).

## Architecture

```
   Claude Code  Claude Desktop  Cursor  spark-tui
         \       |       |       /
          \      |       |      /
           v     v       v     v
    ------------------------------------
    |  spark-mcp (FastMCP + HTTP)      |
    |  Bearer auth / Prometheus metrics|
    |  SSH pool to workers             |
    ------------------------------------
           |            |           |
           v            v           v
    +--------+    +--------+  +--------+
    | head   |    | worker |  | worker |
    +--------+    +--------+  +--------+
```

## Quickstart

```bash
git clone https://github.com/oliveres/spark-mcp-tui ~/spark-mcp-tui
cd ~/spark-mcp-tui

# 1. Install uv if not present
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install both packages as uv-managed tools (isolated envs, CLIs on PATH)
uv tool install ./spark-mcp
uv tool install ./spark-tui
#
# Alternative (development / editable install):
#   uv venv --python 3.11
#   source .venv/bin/activate
#   uv pip install -e ./spark-mcp
#   uv pip install -e ./spark-tui
# With the editable setup you must `source .venv/bin/activate` in every
# new shell, or call the CLIs as `uv run --with-editable ./spark-mcp spark-mcp ...`.

# 3. First-time setup on the head node
spark-mcp init            # writes ~/.config/spark-mcp/{config.toml,.env}

# 4. Edit config.toml (workers, spark-vllm-docker path) and .env (SSH user/key)
nano ~/.config/spark-mcp/config.toml
nano ~/.config/spark-mcp/.env
spark-mcp check           # sanity-check the config before continuing

# 5. Trust each worker's SSH host key BEFORE starting the service.
#    spark-mcp refuses to silently accept unknown hosts (security amendment B3).
spark-mcp ssh-trust spark-2
# Repeat for each worker in config.toml [cluster].workers.

# 6. Run as a systemd service (recommended) or foreground for testing
spark-mcp                  # foreground
# or:
sudo cp ~/.config/spark-mcp/spark-mcp.service /etc/systemd/system/
sudo systemctl enable --now spark-mcp
sudo systemctl status spark-mcp --no-pager

# 7. Verify
TOKEN=$(grep SPARK_MCP_AUTH_TOKEN ~/.config/spark-mcp/.env | cut -d= -f2)
curl -s http://localhost:8765/health                               # {"ok": true}
curl -sH "Authorization: Bearer $TOKEN" http://localhost:8765/metrics | head -5

# 8. Install skills into Claude Code / Claude Desktop
./spark-skills/install.sh

# 9. Register the MCP server in Claude Code (from any workstation on the LAN)
claude mcp add spark-mcp --transport http \
    http://spark-head.local:8765/mcp \
    --header "Authorization: Bearer $TOKEN"

# 10. Configure and launch the TUI (optional)
mkdir -p ~/.config/spark-tui
cat > ~/.config/spark-tui/config.toml <<'TOML'
[connection]
default_profile = "homelab"

[profiles.homelab]
mcp_url = "http://localhost:8765/mcp"

[ui]
theme = "dracula"
refresh_interval_ms = 3000
log_tail_lines = 200
TOML
echo "SPARK_TUI_TOKEN_HOMELAB=$TOKEN" > ~/.config/spark-tui/.env
chmod 600 ~/.config/spark-tui/.env
spark-tui --profile homelab
```

## Security at a glance

The bearer token grants root-equivalent access to the cluster. Keep it in
the 0o600 `.env` file and put the server behind Tailscale or an HTTPS
reverse proxy before exposing it beyond a trusted LAN. See
[`docs/security.md`](docs/security.md) for the full threat model and
reverse-proxy recipes.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache 2.0 - see [`LICENSE`](LICENSE).
