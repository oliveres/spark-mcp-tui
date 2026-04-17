# Quantization guide

| Scheme  | VRAM × FP16 | Quality loss | Notes |
|---------|-------------|--------------|-------|
| FP16 / BF16 | 1.00 | 0% | Reference; rarely needed on Hopper/GB10 |
| FP8 (W8A8)  | 0.55 | <1% | Default for Hopper/GB10 |
| NVFP4       | 0.28 | ~2-3% | Hopper-native mixed-precision |
| AWQ (W4)    | 0.28 | ~2-3% | Community-quantized; broad coverage |
| INT4 auto-round | 0.28 | ~4-5% | Fallback when AWQ unavailable |

## When to pick what

- **FP8** — default when the model fits.
- **NVFP4** — when VRAM is tight and you are on a GB10 node; kernels are
  Hopper-native and fast.
- **AWQ** — when a native FP8 or NVFP4 variant of the model is not yet
  published. Expect comparable memory to NVFP4.
- **INT4** — last resort when no AWQ is available. Slightly worse quality
  than AWQ.
- **FP16 / BF16** — only for debugging or when the model has quality
  regressions at FP8.

## Finding quantized variants

Search the Hugging Face Hub for the model name plus the suffix:

- FP8: `<model>-FP8` or `-W8A8`
- NVFP4: `-NVFP4`
- AWQ: `-AWQ`
- INT4: `-INT4` or `-auto-round`

The `search_huggingface` MCP tool can do this programmatically.
