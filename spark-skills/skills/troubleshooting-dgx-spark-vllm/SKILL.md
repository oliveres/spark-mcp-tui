---
name: troubleshooting-dgx-spark-vllm
description: Use when the user reports that a vLLM model on a DGX Spark cluster has crashed, is stuck, produced an OOM, hit a NCCL timeout, or otherwise misbehaves. Walks through a diagnostic workflow built on spark-mcp tools (get_cluster_status, get_gpu_status, get_container_logs) and escalates to one of the known-issue briefs in known_issues/.
---

# troubleshooting-dgx-spark-vllm

## Triggers

- "model crash", "vllm crashed", "OOM", "out of memory"
- "cluster stuck", "cluster hung", "hanging"
- "NCCL error", "NCCL timeout"
- "Ray init failed", "Ray not joining"
- "flashinfer", "FlashInfer"

## Diagnostic workflow

1. `get_cluster_status()` — which nodes are reachable? Is Ray alive? Is an
   active model recorded in state?
2. `get_gpu_status()` — per-node GPU utilization, temperature, power.
   Nodes reporting 100% but no throughput → stuck kernels.
3. For each suspect node (the head first), `get_container_logs(node, lines=500)`
   and grep for known failure signatures (see `known_issues/`):
   - `sm_121` + `FP8` → [known_issues/sm121_fp8_crash.md](known_issues/sm121_fp8_crash.md).
   - `flashinfer` + `compatible` → [known_issues/flashinfer_compat.md](known_issues/flashinfer_compat.md).
   - `NCCL.*timeout` → [known_issues/nccl_timeout.md](known_issues/nccl_timeout.md).
   - `CUDA out of memory` → [known_issues/oom_patterns.md](known_issues/oom_patterns.md).
   - `Ray.*unable to join` / `raylet is unhealthy` →
     [known_issues/ray_init_failure.md](known_issues/ray_init_failure.md).

## Response template

When you find a known issue, explain to the user:

1. What the symptom means (root cause in one sentence).
2. The concrete fix (recipe change, `--no-ray` flag, reduced
   `gpu_memory_utilization`, etc.).
3. How to verify the fix worked (what to look for in the next logs).

If `stop_cluster()` is warranted, ask the user to confirm first.

## Unknown patterns

If logs do not match any `known_issues/` brief, summarize what you saw
(first and last 20 lines of relevant stack trace) and recommend the user
open a GitHub issue in the spark-mcp repo. Do not guess at fixes for
unfamiliar errors.
