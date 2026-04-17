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

# 1. Install (use uv if you have it; pip works too)
uv pip install -e ./spark-mcp
uv pip install -e ./spark-tui

# 2. First-time setup on the head node
spark-mcp init            # writes ~/.config/spark-mcp/{config.toml,.env}

# 3. Edit config.toml (workers, spark-vllm-docker path) and .env (SSH user/key)
nano ~/.config/spark-mcp/config.toml
nano ~/.config/spark-mcp/.env

# 4. Trust your workers' SSH host keys (first time only)
spark-mcp ssh-trust worker-1
spark-mcp ssh-trust worker-2

# 5. Run as a systemd service (recommended) or foreground for testing
spark-mcp                  # foreground
# or:
sudo cp ~/.config/spark-mcp/spark-mcp.service /etc/systemd/system/
sudo systemctl enable --now spark-mcp

# 6. Install skills into Claude Code / Claude Desktop
./spark-skills/install.sh

# 7. Register the MCP server in Claude Code (from any workstation on the LAN)
TOKEN=$(grep SPARK_MCP_AUTH_TOKEN ~/.config/spark-mcp/.env | cut -d= -f2)
claude mcp add spark-mcp --transport http \
    http://spark-head.local:8765/mcp \
    --header "Authorization: Bearer $TOKEN"

# 8. Launch the TUI
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
