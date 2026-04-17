---
name: choosing-models-for-dgx-spark
description: Use when the user asks "what should I run", "what fits on my cluster", or "which model is fastest for X" on their DGX Spark cluster managed by spark-mcp. Helps select a model by balancing cluster size, quantization, use case (coding, reasoning, multimodal), and throughput expectations. Integrates with MCP tools get_cluster_info, list_cached_models, search_huggingface.
---

# choosing-models-for-dgx-spark

## Triggers

- "what should I run"
- "what fits in my cluster"
- "fastest for X"
- "best model for Y"
- "recommend a model"

## Decision workflow

1. `get_cluster_info()` — learn total VRAM, per-node GPU count. Without
   this, every recommendation is a guess.
2. `list_cached_models()` — check what is already on disk; if something
   good is already cached, suggest that first to skip download time.
3. Classify the user's use case:
   - **Coding** — prefer models tuned for structured output / tool calling.
   - **Reasoning** — prefer models with a reasoning parser (DeepSeek R1,
     Gemma 4, MiniMax M2).
   - **General chat** — any instruction-tuned model that fits.
   - **Multimodal** — vision-language models (Qwen3-VL, Gemma 4 Vision).
4. Propose 1-3 candidates and call `search_huggingface(query=...)` to
   confirm they still exist and have recent downloads.

See `reference/memory_calculations.md` for VRAM math, and
`reference/quantization_guide.md` for precision tradeoffs, and
`reference/performance_expectations.md` for tokens/sec ballparks.

## Output format

Present your top pick first with: model ID, precision, expected VRAM,
expected throughput (tokens/s per node), why it matches the use case.
Offer a fallback at a smaller size.

Ask the user to confirm before creating or launching any recipe.
