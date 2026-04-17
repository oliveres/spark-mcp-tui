# Performance expectations

All numbers are rough ballparks for batch size 1, `max_model_len=32768`,
measured on single-node DGX Spark (128 GB) unless noted. Real throughput
varies with prompt/output length and request concurrency.

## Tokens per second (generation)

| Model size | FP8 solo | NVFP4/AWQ solo | Cluster TP=2 |
|-----------|----------|----------------|--------------|
| 7-8 B     | 140-170  | 160-200        | ~2x solo     |
| 13-15 B   | 80-110   | 110-140        | ~1.9x solo   |
| 34-40 B   | 32-45    | 55-75          | ~1.8x solo   |
| 70 B      | 18-25    | 30-45          | ~1.7x solo   |
| 120-140 B | out of VRAM | 14-20       | ~1.6x solo   |

## Time to first token (TTFT)

TTFT scales with prompt length. Approximate:

- 1K-token prompt: ~0.3 s (7 B) to ~1.2 s (70 B)
- 8K-token prompt: ~0.8 s (7 B) to ~3.5 s (70 B)
- 32K-token prompt: ~2.5 s (7 B) to ~12 s (70 B)

TP=2 on a 2-node cluster roughly halves TTFT for the larger models but
adds ~100 ms NCCL overhead on every request.

## Rule of thumb for recommendations

- Interactive chat → pick the model whose TP=1 ttft is < 1 s at a typical
  prompt length.
- Batch inference → pick the model whose TP=N throughput × N nodes
  saturates the task time budget.
- Coding agent → prefer reasoning-parser-supported models; the parser
  overhead is negligible.
