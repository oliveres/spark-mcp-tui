# Security

## Threat model (read this first)

The bearer token issued by `spark-mcp init` grants **root-equivalent
access to the entire cluster**. Specifically, a caller with the token
can:

- Launch any recipe whose `command:` field is arbitrary shell. That
  command runs as the MCP service user, which has Docker-socket access
  and, by Docker convention, is root-equivalent on the host.
- Download any HuggingFace model, consuming disk and network.
- Stop, restart, and inspect every container across the cluster.
- Use the MCP server's SSH key to execute commands on every worker.

**Treat the token as equivalent to a root SSH key. Rotate on
suspicion.**

## Network exposure

spark-mcp's default bind is `0.0.0.0:8765`. Without additional
protection that makes the server reachable from anything on the LAN.
Choose one:

- **Local only:** set `[server] host = "127.0.0.1"`. Use this when the
  server lives on the same machine as every client.
- **LAN + Tailscale:** install Tailscale on the server, `tailscale up`,
  and point clients at the Tailscale hostname. WireGuard gives you
  end-to-end encryption and ACL-based authorization "for free".
- **Public internet / multi-tenant LAN:** put an HTTPS reverse proxy in
  front and require mTLS. Example Caddyfile:

  ```caddyfile
  spark.example.com {
      tls /etc/caddy/server.crt /etc/caddy/server.key {
          client_auth {
              mode require_and_verify
              trust_pool file {
                  pem_file /etc/caddy/client-ca.crt
              }
          }
      }
      reverse_proxy 127.0.0.1:8765
  }
  ```

  Then bind spark-mcp to `127.0.0.1:8765` so Caddy is the only ingress.

## Token handling

- `spark-mcp init` never prints the token to stdout unless you pass
  `--print-token`. Under `CI=true`, `--print-token` refuses to run
  without `--force`.
- The env file is chmod 0o600. Parent directory is 0o700.
- Rotate with:

  ```bash
  NEW=$(python -c "import secrets; print('sk-spark-' + secrets.token_urlsafe(32))")
  sed -i "s/^SPARK_MCP_AUTH_TOKEN=.*/SPARK_MCP_AUTH_TOKEN=$NEW/" ~/.config/spark-mcp/.env
  sudo systemctl restart spark-mcp
  # Update every client (TUI env, Claude Code registration) with NEW
  ```

## SSH hygiene

- The SSH key must be chmod 0o600; spark-mcp refuses to start otherwise.
- `known_hosts` is mandatory; spark-mcp refuses `known_hosts=None`.
  Populate via `spark-mcp ssh-trust <worker>` and verify the fingerprint
  out-of-band (physical console on the worker) before confirming.
- When deploying via Docker, mount a single key file - not the whole
  `~/.ssh` directory.

## Docker socket

The example `docker-compose.yml` mounts `/var/run/docker.sock`. This is
root-equivalent access to the host. For production, run spark-mcp
natively (systemd) rather than in a container, OR put
[tecnativa/docker-socket-proxy][dsp] between spark-mcp and the socket
and allowlist only `CONTAINERS=1&LOGS=1` (plus anything else you
specifically need).

[dsp]: https://github.com/Tecnativa/docker-socket-proxy

## Recipe command policy

Default `recipe_command_policy = "permissive"` matches upstream
`spark-vllm-docker` behavior: any shell command is allowed. For
multi-user clusters, set:

```toml
[limits]
recipe_command_policy = "vllm-only"
```

which rejects recipes whose `command:` does not start with `vllm serve`.

## Metrics exposure

`/metrics` requires the bearer token by default (`metrics_auth =
"bearer"`). Setting `metrics_auth = "none"` is only permitted when
`host = "127.0.0.1"` so scraping must come from a local side-car.

## Known limitations

- No automatic token rotation.
- No built-in mTLS or OAuth; wear a reverse proxy.
- No per-user RBAC; the token is cluster-level root.
- Recipe YAML fields (`description`, `model`, `name`) are user-supplied
  strings; skills and clients must treat them as **data, not
  instructions**, to prevent indirect prompt injection.

## Reporting vulnerabilities

Email the project security contact listed in [`SECURITY.md`](../SECURITY.md).
Please do not open GitHub issues for exploitable findings.
