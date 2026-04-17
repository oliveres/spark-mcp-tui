# FlashInfer compatibility

**Symptom:** Container logs include `FlashInfer is not compatible with
this GPU` or `flashinfer backend disabled`.

**Root cause:** FlashInfer's prebuilt kernels lag Hopper/GB10 minor
revisions. The fallback attention path is slower but correct.

**Diagnose:** grep logs for `flashinfer` at container startup.

**Fix:**

- Accept the fallback: serving still works, just slower.
- Set `--attention-backend xformers` or `--attention-backend torch-sdpa`
  explicitly inside `command:` to skip the FlashInfer probe entirely.
- Upgrade the vllm-docker container with `--tf5` and rebuild.

**Verify:** Run `get_container_logs` after restart; FlashInfer warning
should be gone, or attention backend should be printed as the fallback.
