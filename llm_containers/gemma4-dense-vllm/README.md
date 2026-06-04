# gemma4-dense-vllm

Optimized vLLM container for **Gemma 4 31B dense** on HBM-class GPUs
(A100 / H100 / H200 / B200). Defaults to
**`RedHatAI/gemma-4-31B-it-FP8-Dynamic`** (~31 GB), a vLLM-tested FP8 dynamic
quant with near-lossless quality vs BF16.

For **L40S-48GB Ada GPUs**, use the sibling
[`gemma4-dense-l40s-vllm`](../gemma4-dense-l40s-vllm/) image — it ships
INT4 AWQ + 8K context tuned for 5 concurrent agent sessions on 48 GB.

## Model specs

| Property | Value |
|---|---|
| Base model | `google/gemma-4-31B-it` (30.7B dense, 60 layers, head_dim 256/512) |
| Default checkpoint | `RedHatAI/gemma-4-31B-it-FP8-Dynamic` (FP8 dynamic W8A8) |
| Attention | Standard GQA with 5:1 interleaved local-SWA (window 1024) + global |
| Modalities | text + image + video (no audio at this size) |
| Native context | 256K |
| License | Apache 2.0 (base + RedHatAI re-pack) |

> Google does **not** ship official FP8/INT4 variants of Gemma 4 (unlike
> Gemma 3). The `RedHatAI/*` repos are the battle-tested alternative,
> maintained by the team behind vLLM's llm-compressor.

## Memory math (default FP8 weights ~31 GB, BF16 KV)

Per-sequence KV is dominated by the 10 global layers (16 KV heads × 512
head_dim) — vLLM's PagedAttention forces uniform tensor shapes across the
5:1 SWA/global mix, so global layers are allocated as if they had 16 KV
heads even though the model only uses 4 (vllm-metal #276).

| Context | Per-session KV | A100-80GB | H100/H200-80GB |
|---|---|---|---|
| 32K  | ~10.5 GB | 4 concurrent | 4 concurrent + headroom |
| 64K  | ~21.4 GB | 2 concurrent | 2 concurrent + headroom |
| 128K | ~42.7 GB | **1 concurrent (default)** | 1 concurrent + headroom |
| 256K | ~85 GB   | Doesn't fit | Doesn't fit |

To run **full BF16** (~61 GB weights), set `MODEL=google/gemma-4-31B-it`
and cap `MAX_MODEL_LEN=65536` — requires A100-80GB or better, and bump
`MIN_VRAM_GB=110`.

## Quick start

```bash
docker build -t gemma4-dense-vllm:latest .

# A100-80GB at default 128K (1 concurrent agent)
docker run --gpus all -p 8000:8000 --ipc=host \
    -v /opt/cache:/mnt/cache \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    gemma4-dense-vllm:latest

# A100-80GB at 32K context (4 concurrent agents)
docker run --gpus all -p 8000:8000 --ipc=host \
    -v /opt/cache:/mnt/cache \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MAX_MODEL_LEN=32768 \
    gemma4-dense-vllm:latest

# H100/H200 at full 256K
docker run --gpus all -p 8000:8000 --ipc=host \
    -v /opt/cache:/mnt/cache \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MAX_MODEL_LEN=262144 \
    gemma4-dense-vllm:latest

# BF16 full precision on A100-80GB (cap context to 64K)
docker run --gpus all -p 8000:8000 --ipc=host \
    -v /opt/cache:/mnt/cache \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MODEL=google/gemma-4-31B-it \
    -e MAX_MODEL_LEN=65536 \
    -e MIN_VRAM_GB=110 \
    gemma4-dense-vllm:latest
```

The `-v /opt/cache:/mnt/cache` mount is **strongly recommended** — without
it, every restart re-downloads ~31 GB of weights and re-runs the ~50s
`torch.compile` pass. With the volume, subsequent boots drop from ~12-15
min cold to ~2-3 min warm.

## Key environment variables

| Variable | Default | Notes |
|---|---|---|
| `MODEL` | `RedHatAI/gemma-4-31B-it-FP8-Dynamic` | Swap for BF16, NVFP4, etc. (see below) |
| `MAX_MODEL_LEN` | `131072` | 128K. Lower for higher concurrency; raise to 262144 on H200 |
| `MAX_NUM_SEQS` | `16` | Capped by 128K KV pool footprint at 0.92 util |
| `MAX_NUM_BATCHED_TOKENS` | `8192` | Chunked-prefill upper bound |
| `GPU_MEMORY_UTILIZATION` | `0.92` | Hybrid KV manager needs headroom |
| `KV_CACHE_DTYPE` | `auto` (BF16) | **Do not change** — Issue #40388 makes FP8 KV unsafe with FP8 weights |
| `CUDAGRAPH_CAPTURE_SIZES` | `1,2,4,8,16` | Restricts graph capture to realistic agent batches |
| `LIMIT_MM_PER_PROMPT` | `image=2,audio=0` | Multimodal on; audio not supported at 31B |
| `TOOL_CALL_PARSER` | `gemma4` | Native Gemma 4 parser |
| `REASONING_PARSER` | `gemma4` | Extracts thinking content into `reasoning_content` |
| `ENABLE_THINKING` | `true` | Server-side `<\|think\|>` injection |
| `MIN_VRAM_GB` | `44` | Warns below; bump to 110 for BF16 override |
| `ALLOW_ADA` | `false` | Set `true` to bypass Ada refusal (use the L40S image instead) |

## Alternative model IDs (verified on HuggingFace)

| Repo | Size | Best on | Use case |
|---|---|---|---|
| `RedHatAI/gemma-4-31B-it-FP8-Dynamic` | ~31 GB | A100 / H100 / H200 / B200 | **Default** |
| `RedHatAI/gemma-4-31B-it-FP8-block` | ~31 GB | Same | Block-quant alternative, v1.0 stable |
| `RedHatAI/gemma-4-31B-it-NVFP4` | ~17 GB | Hopper / Blackwell | Max context + speed on new hardware |
| `nvidia/Gemma-4-31B-IT-NVFP4` | ~21 GB | Blackwell | `--quantization modelopt` path |
| `google/gemma-4-31B-it` | ~61 GB | A100-80GB+ | Full BF16 precision |

For INT4 AWQ on Ada-class hardware, see the
[`gemma4-dense-l40s-vllm`](../gemma4-dense-l40s-vllm/) image instead.

## Gotchas

- **FP8 KV is forbidden** on this image (default `KV_CACHE_DTYPE=auto`).
  Issue #40388: per-token FP8 KV requires 2:1 block-to-scale alignment
  broken by Gemma 4's heterogeneous head dimensions (256 SWA / 512
  global). Affects every architecture, including H100/H200.
- **Sliding-window prefix caching**: vLLM's Hybrid KV Cache Manager
  handles Gemma 4's interleaved SWA but with reduced prefix-cache
  efficiency vs a pure-global-attention model. Hits still happen — just
  fewer than on dense Qwen / Llama. Track issues #3355, #14881, #20865.
- **Attention backend**: entrypoint picks `TRITON_ATTN` on Ampere — FA2
  rejects Gemma 4's `head_dim=256` + interleaved SWA — and `FLASH_ATTN`
  (FA3) on Hopper/Blackwell. FLASHINFER stays unsupported (issue #20865).
- **FP8 on Ampere is software-emulated**: A100 runs FP8 weights through
  the Marlin W8A16 path — functional and stable, but ~60-70% of native
  FP8 TC throughput. On Hopper/Blackwell you get native FP8 speed.
- **Multimodal inputs** (image/video) work through the standard vLLM chat
  API with `{"type": "image_url", "image_url": {"url": "..."}}`. The
  `gemma4` parser doesn't affect multimodal paths — test separately.
- **Reasoning extraction requires `"skip_special_tokens": false`** in the
  client request — otherwise `<|channel|>` delimiters are stripped before
  the gemma4 reasoning parser sees them (vLLM Issue #38855). The
  `model-orchestrator` injects this for the Gemma 4 routes; direct callers
  must send it. **Streaming caveat:** #38855 still affects the streaming
  parser path, so with `stream=true` reasoning may surface inline in
  `content` — reliable separation is non-streaming only.

## RunPod deployment

See [`RUNPOD.md`](./RUNPOD.md). Container image:
`ghcr.io/knaeckebrothero/gemma4-dense-vllm:latest` (built by
`.github/workflows/llm-containers.yml` on pushes to `main`).

## Choosing between dense vs MoE vs L40S

Pick **this container (31B dense)** when: you're on A100/H100/H200/B200 and
want maximum reasoning depth, multimodal generalist capability, and the best
τ-bench / tool-use scores.

Pick **[`gemma4-dense-l40s-vllm`](../gemma4-dense-l40s-vllm/)** when: your
hardware is L40S-48GB or A6000 Ada — the INT4 AWQ + 8K + fp8_e4m3 KV recipe
unlocks 5 concurrent agent sessions where this image cannot fit even one.

Pick **[`gemma4-moe-vllm`](../gemma4-moe-vllm/)** when: you want higher
throughput (4B active params) and don't need the dense model's multi-step
tool-use reliability.
