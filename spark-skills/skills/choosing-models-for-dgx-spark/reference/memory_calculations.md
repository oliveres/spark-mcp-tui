# Memory calculations

`vram_needed = param_count_billions * precision_bytes + activations + kv_cache`

| Precision | Bytes per param |
|-----------|-----------------|
| FP16      | 2.0             |
| BF16      | 2.0             |
| FP8       | 1.1 (with overhead) |
| NVFP4     | 0.55            |
| AWQ       | 0.55            |
| INT4      | 0.55            |

## Activations

~10-20% of weight VRAM for typical batch sizes and `max_model_len`
≤32768. Scales with batch × sequence length.

## KV cache

`kv_cache_gb = 2 * num_layers * hidden_size * max_model_len * bytes_per_element / 1e9`

For a 70B model (80 layers, 8192 hidden) at FP16 and 32K context:
`2 * 80 * 8192 * 32768 * 2 / 1e9 ≈ 86 GB`

This is why `max_model_len` is one of the first knobs to lower when
hitting OOM.

## Cluster capacity reference

Assume 128 GB per Spark; budget 85% usable after OS/kernel overhead.

| Cluster size | Usable VRAM | FP8 model ceiling | NVFP4/AWQ ceiling |
|--------------|-------------|-------------------|-------------------|
| 1 × Spark    | 108 GB      | ~110 B params     | ~170 B params     |
| 2 × Spark    | 216 GB      | ~220 B params     | ~340 B params     |
| 4 × Spark    | 432 GB      | ~440 B params     | ~680 B params     |
| 6 × Spark    | 648 GB      | ~660 B params     | ~1000 B params    |

These are upper bounds for weights-only; subtract ~15% for KV + activations.
