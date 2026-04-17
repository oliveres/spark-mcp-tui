# spark-mcp

MCP server component of the spark-mcp-tui toolkit. See the [root README](../README.md) and [PRD](../docs/SPARK_MCP_PRD.md).

## Install (dev)

```bash
uv pip install -e ".[dev]"
```

## Run tests

```bash
pytest               # unit tests only
pytest -m integration  # requires a real cluster
```
