# Claude Code setup

Three ways to register spark-mcp with Claude Code.

## 1. HTTP over LAN (recommended)

```bash
TOKEN=$(grep SPARK_MCP_AUTH_TOKEN ~/.config/spark-mcp/.env | cut -d= -f2)
claude mcp add spark-mcp \
    --transport http \
    http://spark-head.local:8765/mcp \
    --header "Authorization: Bearer $TOKEN"
```

The `examples/claude_code_mcp_http.json` file has the raw JSON form if
you prefer to edit `~/.claude/mcp.json` directly.

## 2. HTTP over Tailscale (recommended for cross-site)

Same registration, but point at the Tailscale hostname:

```bash
claude mcp add spark-mcp \
    --transport http \
    http://spark-head.your-tailnet.ts.net:8765/mcp \
    --header "Authorization: Bearer $TOKEN"
```

Tailscale gives you WireGuard-over-QUIC end-to-end encryption without
extra certificates; recommended over raw plain-HTTP on untrusted networks.

## 3. stdio (local testing only)

```json
{
  "mcpServers": {
    "spark-mcp": {
      "transport": "stdio",
      "command": "spark-mcp",
      "args": ["serve"],
      "env": {
        "SPARK_MCP_CONFIG": "${HOME}/.config/spark-mcp/config.toml"
      }
    }
  }
}
```

## Installing the skills

```bash
./spark-skills/install.sh
```

This copies `creating-vllm-recipes`, `troubleshooting-dgx-spark-vllm`,
and `choosing-models-for-dgx-spark` into `~/.claude/skills/` and
`~/.claude-code/skills/`. Restart Claude Code to pick them up.

## Verifying

Ask Claude: "Use the spark-mcp server to list my recipes." Claude should
call `list_recipes` and show the output. If authentication fails, the
server returns 401; check the token and header syntax.
