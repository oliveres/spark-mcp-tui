# spark-tui

Textual TUI for the [`spark-mcp`](../spark-mcp) MCP server.

## Install (dev)

```bash
uv pip install -e ".[dev]"
```

## Run

```bash
spark-tui --profile homelab
```

TUI config lives at `~/.config/spark-tui/config.toml`; per-profile tokens
live in `~/.config/spark-tui/.env`.

## Security

Never expose the bearer token over plain HTTP across a network; the TUI
refuses to send tokens over non-localhost plain-HTTP URLs. Use Tailscale
or an HTTPS reverse proxy in front of spark-mcp for remote access.
