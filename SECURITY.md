# Security policy

## Threat model (short form)

The `spark-mcp` bearer token grants **root-equivalent access** to the
cluster it serves. Any client holding the token can launch arbitrary
shell commands (via recipe `command:` fields) on the head node, SSH to
every worker, and manipulate the full vLLM lifecycle.

Treat the token with the same care as a root SSH key. See
[`docs/security.md`](docs/security.md) for the full threat model,
deployment guidance, and mitigation recipes.

## Reporting a vulnerability

Please **do not** open public GitHub issues for exploitable findings.

Email the project security contact with the details:

- Reporter email: `security@<maintainer-domain>`
  (placeholder; edit this file when you fork)
- PGP key: none yet.

Expected response time: acknowledgement within 72 hours; initial triage
within 7 days. We follow a 90-day coordinated disclosure window.

## Scope

All three packages in this monorepo are in scope:

- `spark-mcp` (MCP server)
- `spark-skills` (Claude skills)
- `spark-tui` (Textual TUI)

Out of scope:

- Vulnerabilities in upstream `eugr/spark-vllm-docker` — please report to
  that project directly.
- Vulnerabilities in NVIDIA proprietary software, vLLM, or HuggingFace
  Hub — please report to those projects.
- Attacks requiring prior root access to the host running `spark-mcp`.

## Supported versions

v0.1.x receives security fixes. Older pre-release tags do not.
