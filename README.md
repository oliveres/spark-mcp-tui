# spark-mcp

MCP-powered lifecycle management for vLLM on NVIDIA DGX Spark clusters.

This repository contains three components:

- [`spark-mcp/`](spark-mcp) — MCP server wrapping `eugr/spark-vllm-docker`
- [`spark-skills/`](spark-skills) — Claude skills for recipe authoring, troubleshooting, model selection
- [`spark-tui/`](spark-tui) — Textual-based terminal UI

Full product requirements live in [`docs/SPARK_MCP_PRD.md`](docs/SPARK_MCP_PRD.md).

**Status:** pre-release (v0.1.0 in progress). License: Apache 2.0.
