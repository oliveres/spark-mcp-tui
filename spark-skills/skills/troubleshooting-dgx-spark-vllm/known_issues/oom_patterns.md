# CUDA OOM patterns

**Symptom:** `torch.cuda.OutOfMemoryError` or `CUDA out of memory`. The
container dies during model load (likely weight fit) or mid-request
(likely KV-cache fit).

## Pattern 1: Weights don't fit

**Logs look like:** OOM within the first 60 s, at the `Loading model`
line.

**Fix:**

- Lower precision: FP16 → FP8 → NVFP4/AWQ.
- Raise `tensor_parallel` to split weights across more GPUs (requires
  `cluster_only` recipe).
- If model size exceeds the cluster's total VRAM × 0.85, this model
  cannot run here — suggest a smaller variant.

## Pattern 2: KV cache doesn't fit

**Logs look like:** Model loads fine; OOM triggers during inference,
usually around the longest-context requests.

**Fix:**

- Lower `max_model_len` (e.g., 262144 → 65536).
- Lower `gpu_memory_utilization` from 0.85 to 0.7 to leave more for KV.
- Add `--max-num-seqs <N>` (smaller batch) to `command:`.

**Verify:** Re-launch and watch for sustained token throughput without
OOM. `get_gpu_status()` should show `memory_used` plateau below the
`gpu_memory_utilization` limit.
