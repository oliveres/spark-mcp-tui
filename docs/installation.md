# Installation

## Prerequisites

- NVIDIA DGX Spark head node (1+ node; 2+ for cluster recipes)
- `spark-vllm-docker` already cloned and tested on the head node
- Python 3.11 or newer
- SSH keys distributed to worker nodes (key-based login must already work)
- A shared HuggingFace cache accessible to vllm-docker (default: `~/.cache/huggingface`)

## Three deployment paths

### 1. Native Python on the head node (recommended)

```bash
git clone https://github.com/oliveres/spark-mcp-tui ~/spark-mcp-tui
cd ~/spark-mcp-tui
uv pip install -e ./spark-mcp        # or: pip install -e ./spark-mcp
spark-mcp init
```

`spark-mcp init` populates `~/.config/spark-mcp/{config.toml,.env}` with
a freshly generated auth token (file mode 0o600) and drops a ready-to-
install systemd unit into the same directory.

Edit `~/.config/spark-mcp/config.toml` to set `[cluster].workers`, the
path to your `spark-vllm-docker` clone, and the head-node interconnect
IP. Edit `~/.config/spark-mcp/.env` to point `SPARK_MCP_SSH_USER` and
`SPARK_MCP_SSH_KEY_PATH` at the cluster-wide SSH key.

Run `spark-mcp ssh-trust <worker>` for each worker to seed the
`known_hosts` file (required — `spark-mcp` refuses to silently accept
new host keys).

### 2. systemd service

```bash
sudo cp ~/.config/spark-mcp/spark-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spark-mcp
sudo journalctl -u spark-mcp -f
```

The unit runs `spark-mcp serve` with your user and loads the env file.
Restarts on failure with 5 s backoff.

### 3. Docker Compose

```bash
cp examples/docker-compose.yml .
cp ~/.config/spark-mcp/config.toml .
cp ~/.config/spark-mcp/.env .
docker compose up -d
```

The example compose file mounts:

- a single SSH key (not the whole `~/.ssh` — see `docs/security.md`)
- the `spark-vllm-docker` checkout read-only
- `/var/run/docker.sock` for local Docker commands (this is the main
  blast radius; prefer native systemd for production)

## Installing the TUI

```bash
uv pip install -e ./spark-tui
mkdir -p ~/.config/spark-tui
cat > ~/.config/spark-tui/config.toml <<EOF
[connection]
default_profile = "homelab"

[profiles.homelab]
mcp_url = "http://spark-head.local:8765/mcp"

[ui]
theme = "dracula"
refresh_interval_ms = 3000
log_tail_lines = 200
EOF
echo "SPARK_TUI_TOKEN_HOMELAB=$(grep SPARK_MCP_AUTH_TOKEN ~/.config/spark-mcp/.env | cut -d= -f2)" \
    > ~/.config/spark-tui/.env
chmod 600 ~/.config/spark-tui/.env
spark-tui --profile homelab
```

## Troubleshooting

- `SSH known_hosts not found` — run `spark-mcp ssh-trust <worker>` first.
- `SSH key has insecure permissions` — `chmod 600 ~/.ssh/<key>`.
- `Refusing to send bearer token over plain HTTP` (TUI) — the TUI
  declines to transmit the token to any non-localhost plain-HTTP URL.
  Use Tailscale or HTTPS.
- `401 unauthorized` — check the token matches in `~/.config/spark-mcp/.env`
  and in your client registration.
