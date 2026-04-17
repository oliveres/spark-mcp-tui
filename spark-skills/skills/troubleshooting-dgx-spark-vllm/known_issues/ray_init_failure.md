# Ray initialization failure

**Symptom:** Cluster launch succeeds on the head but workers never join.
Logs include `raylet is unhealthy` or `Unable to join cluster at ...`.

**Root cause:** Ray head port is not reachable from workers (firewall /
different subnet), or the Ray versions diverge across nodes, or the
worker can't resolve the head hostname.

**Diagnose:**

```bash
# From each worker:
ray status --address <head-ip>:6379
telnet <head-ip> 6379
```

Also compare Ray versions: `pip show ray` on head vs workers; they must
match exactly.

**Fix:**

- Pin `ray` to the same version across all nodes via
  `spark-vllm-docker/hf-download.sh` or via the container image tag.
- Verify cluster nodes share a VLAN / subnet that allows Ray ports
  (default 6379, 10001-10009, 10000+).
- If transient, restart with `launch-cluster.sh` after a clean
  `stop_cluster()`.
- As a fallback, launch `--solo` on the head node while debugging the
  cluster path.

**Verify:** `get_cluster_status()` should return `ray_status.alive=True`
with every worker listed in `ray_status.nodes`.
