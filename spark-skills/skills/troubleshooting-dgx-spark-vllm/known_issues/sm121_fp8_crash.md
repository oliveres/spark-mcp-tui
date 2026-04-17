# SM 12.1 FP8 matmul crash

**Symptom:** Container exits with a segfault or `CUDA error: an illegal
memory access was encountered`. Logs mention `sm_121` and `FP8`.

**Root cause:** The Hopper GB10 reports compute capability `sm_121`, which
early vLLM wheels did not include in the FP8 kernel dispatch table. The
kernel falls through to an undefined path.

**Diagnose:**

```bash
get_container_logs(node="<head>", lines=300)
```

Look for `unsupported sm_121` or `cuda` illegal memory access near an
FP8 matmul call stack.

**Fix:**

- Use a recipe built with `--tf5` (TensorRT-LLM 5 container), which has
  the updated FP8 kernels.
- If `--tf5` is already in use, switch the recipe quantization from FP8 to
  NVFP4 or AWQ until upstream ships a fix.
- As a short-term workaround, launch with `--no-ray` (single-node only) —
  Ray forces multi-process execution that hits the buggy dispatch sooner.

**Verify:** After applying the fix, launch the recipe and watch the first
128 tokens stream. If the container survives the first batch, the fix is
effective.
