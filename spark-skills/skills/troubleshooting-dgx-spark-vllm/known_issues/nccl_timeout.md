# NCCL timeout / collective hangs

**Symptom:** Cluster starts, model loads, then hangs. Logs include
`NCCL watchdog caught collective operation timeout` or `collective
exceeded timeout`.

**Root cause:** Slow/lossy CX-7 interconnect; firewall blocking NCCL
ports; or mismatched NCCL_IB_HCA / NCCL_SOCKET_IFNAME across nodes.

**Diagnose:**

```bash
# On each node:
ib_write_bw <other-node>
# Also check: ifconfig (expected 400G-ish CX-7 link)
nvidia-smi topo -m
```

**Fix:**

- Verify `interconnect_ip` in `config.toml` matches every node's CX-7
  link-local address.
- Explicitly set in recipe `command:`:
  ```bash
  --env NCCL_SOCKET_IFNAME=ibs1f0  # or whatever the CX-7 iface is named
  --env NCCL_DEBUG=WARN
  ```
- Reduce `tensor_parallel` (less collective traffic) to isolate.

**Verify:** Run `get_container_logs` and confirm NCCL init prints the
expected bandwidth and that no `watchdog` timeouts appear during the
first inference batch.
