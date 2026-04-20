# qwen3-next-vllm

Optimized vLLM container for **`cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit`**
(Qwen3-Next 80B MoE, 3B active) — frontier-adjacent generalist with hybrid
Gated-DeltaNet + Gated-Attention architecture.

## Model specs

| Property | Value |
|---|---|
| Architecture | 80B total / 3B active (MoE), hybrid Gated-DeltaNet + Gated-Attention |
| Attention | Hybrid attention (no sinks, no interleaved SWA) |
| Native context | 262K (up to 1M with YaRN) |
| License | Apache 2.0 (weights); community quant |
| Default HF ID | `cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit` (~46 GB) |

## Memory math on A100-80GB (AWQ INT4 weights ≈ 46 GB)

| Context | KV cache (bf16) | Total | Verdict |
|---|---|---|---|
| 32K  | ~4 GB  | ~50 GB | Comfortable |
| 64K  | ~8 GB  | ~54 GB | **Default, safe** |
| 128K | ~16 GB | ~62 GB | Tight on A100-80GB |
| 256K | ~32 GB | ~78 GB | **Requires H100/H200 or 2× A100** |

FP8 KV cache quantization is **not supported** for Qwen3-Next hybrid attention
(vLLM #26646) — you cannot reclaim KV VRAM the way you can on Gemma / Llama.

## Quick start

```bash
docker build -t qwen3-next-vllm:latest .

# A100-80GB at default 64K
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    qwen3-next-vllm:latest

# Thinking-mode enabled (separate reasoning field in API response)
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e REASONING_PARSER=qwen3 \
    qwen3-next-vllm:latest

# Swap to Qwen3-Coder-30B-A3B (FP8, ~31GB weights, coding-specialized)
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 \
    -e QUANTIZATION=auto \
    -e MAX_MODEL_LEN=131072 \
    qwen3-next-vllm:latest

# Swap to Qwen3-32B dense (AWQ, ~20 GB weights, huge KV budget)
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MODEL=Qwen/Qwen3-32B-AWQ \
    -e MAX_MODEL_LEN=131072 \
    qwen3-next-vllm:latest
```

## Key environment variables

| Variable | Default | Notes |
|---|---|---|
| `MODEL` | `cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit` | Also works for any Qwen3 variant |
| `QUANTIZATION` | `awq_marlin` | `auto` for FP8, `awq_marlin` for AWQ, `gptq_marlin` for GPTQ |
| `MAX_MODEL_LEN` | `65536` | Raise only on H100+ — KV grows fast without FP8 support |
| `GPU_MEMORY_UTILIZATION` | `0.93` | AWQ + bf16 KV is memory-efficient; 0.95 on Hopper |
| `KV_CACHE_DTYPE` | `bfloat16` | **Do not change** — FP8 KV is broken for Qwen3-Next (#26646) |
| `TOOL_CALL_PARSER` | `qwen3_xml` | **Do NOT use `qwen3_coder`** — infinite special-char bug on long inputs |
| `REASONING_PARSER` | (empty) | Set to `qwen3` to extract thinking into a separate field |
| `ASYNC_SCHEDULING` | `true` | Safe on vLLM 0.14+ for Qwen3 |
| `MIN_VRAM_GB` | `56` | Warn at startup if GPU has less; override if loading a smaller Qwen variant |
| `SKIP_VRAM_CHECK` | `false` | Set `true` to silence the sub-56 GB VRAM warning |

## Gotchas

- **Pin vLLM 0.19.x** on A100. Main/nightly regressed FP8 Marlin on Ampere
  (vLLM issue #39610) — upgrading breaks FP8 Qwen variants.
- **FP8 KV cache is broken** for Qwen3-Next (hybrid Gated-DeltaNet) —
  vLLM #26646 open. Must use `bfloat16`. This constrains your context budget
  more than it would on Gemma 4 / Llama 3.3.
- **`qwen3_coder` tool parser is unstable** — emits infinite streams of
  special characters on long tool-call inputs. Use `qwen3_xml` (default).
  If you swap `MODEL` to a vanilla Qwen3-Instruct (non-Coder, non-Next),
  switch to `TOOL_CALL_PARSER=hermes` or `qwen25`.
- **Thinking-mode bleed**: with reasoning enabled, `</think>` may not close
  when the assistant emits a tool call without text content, corrupting the
  next turn. Use the patched Jinja templates (Qwen3 issue #1831). Default
  config here has `REASONING_PARSER` empty to avoid this.
- **Prefix-cache hit rate** can be <1% on some A3B-class MoEs (vLLM #36493).
  No cost to leaving it on — just don't expect dramatic speedups.
- **AWQ perf cliff**: older Qwen AWQ quants dropped from 52 t/s → 3 t/s at
  concurrency 25 on A100 (vLLM #20469). Benchmark your target concurrency
  and consider capping `MAX_NUM_SEQS` if throughput collapses.
- **Hermes streaming fixes**: PR #38168 in vLLM late-Q1 2026 resolved
  long-standing tool-call streaming regressions. This image is pinned to
  0.19.0 which includes the fix.
- **`VLLM_ALLOW_LONG_MAX_MODEL_LEN=1`** is set in the Dockerfile. Required
  for 256K+ context with Qwen3-Next.

## RunPod deployment

Container image: `ghcr.io/knaeckebrothero/qwen3-next-vllm:latest`
(built by `.github/workflows/llm-containers.yml` on pushes to `main`).

- `HUGGING_FACE_HUB_TOKEN=hf_xxx`
- `MAX_MODEL_LEN=65536` (A100) or `131072` (H100)
- Volume: 100 GB (AWQ weights ~46 GB + HF cache)
- Port 8000 as TCP
- `PUBLIC_KEY` auto-injected for SSH tunneling

## Choosing Qwen3-Next vs the Gemma containers

Pick **this container** when:
- You want frontier-adjacent agentic quality on a single A100-80GB
- Text-only is fine (no multimodal required)
- You can tolerate bleeding-edge backend caveats (hybrid-attention bugs, AWQ
  perf cliffs, KV quant unavailable)

Pick **`gemma4-moe-vllm`** when:
- You want multimodal (image/video) inputs
- You want the cleanest backend path on A100 (no hybrid-attention caveats)
- Throughput matters more than peak capability

Pick **`gemma4-dense-vllm`** when:
- You want multimodal + maximum reasoning depth and can accept tight memory
