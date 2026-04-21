# gemma4-dense-vllm

Optimized vLLM container for **Gemma 4 31B dense** — a multimodal generalist
agent model. Defaults to **`RedHatAI/gemma-4-31B-it-FP8-Dynamic`** (~33 GB),
a vLLM-tested FP8 dynamic quant with near-lossless quality vs BF16.

## Model specs

| Property | Value |
|---|---|
| Base model | `google/gemma-4-31B-it` (30.7B dense, 60 layers, head_dim 256) |
| Default checkpoint | `RedHatAI/gemma-4-31B-it-FP8-Dynamic` (FP8 dynamic W8A8) |
| Attention | Standard GQA with 5:1 interleaved local-SWA (window 1024) + global |
| Modalities | text + image + video (no audio at this size) |
| Native context | 256K |
| License | Apache 2.0 (base + RedHatAI re-pack) |

> Google does **not** ship official FP8/INT4 variants of Gemma 4 (unlike
> Gemma 3). The `RedHatAI/*` repos are the battle-tested alternative,
> maintained by the team behind vLLM's llm-compressor.

## Memory math (default FP8 weights ≈ 33 GB, `fp8_e5m2` KV)

| Context | FP8 KV | Total (weights + KV + overhead) | A100-80GB | L40S-48GB | RTX 4090-24GB |
|---|---|---|---|---|---|
| 32K  | ~1.5 GB | ~38 GB | Trivial | Comfortable | Tight |
| 64K  | ~3 GB   | ~39 GB | Comfortable | Comfortable | Doesn't fit |
| 128K | ~6 GB   | ~42 GB | **Default, safe** | **Default, fits** | Doesn't fit |
| 256K | ~10 GB  | ~46 GB | Comfortable | Overflows | Doesn't fit |

To run **full BF16** (~61 GB weights), set `MODEL=google/gemma-4-31B-it` and
cap `MAX_MODEL_LEN=65536` — requires A100-80GB or better.

## Quick start

```bash
docker build -t gemma4-dense-vllm:latest .

# A100-80GB at default 128K — simplest path, near-max context
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    gemma4-dense-vllm:latest

# A100-80GB at full 256K
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MAX_MODEL_LEN=262144 \
    gemma4-dense-vllm:latest

# L40S-48GB at default 128K (native FP8 TC speed on Ada)
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e KV_CACHE_DTYPE=fp8_e4m3 \
    gemma4-dense-vllm:latest

# BF16 full precision on A100-80GB (cap context to 64K)
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MODEL=google/gemma-4-31B-it \
    -e MAX_MODEL_LEN=65536 \
    -e MIN_VRAM_GB=70 \
    gemma4-dense-vllm:latest

# RTX 4090-24GB via AWQ INT4
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MODEL=QuantTrio/gemma-4-31B-it-AWQ \
    -e MAX_MODEL_LEN=32768 \
    gemma4-dense-vllm:latest
```

## Key environment variables

| Variable | Default | Notes |
|---|---|---|
| `MODEL` | `RedHatAI/gemma-4-31B-it-FP8-Dynamic` | FP8 by default. Swap for BF16, AWQ, NVFP4 (see below) |
| `MAX_MODEL_LEN` | `131072` | Fits both A100-80GB and L40S-48GB at FP8; raise to `262144` on A100+ |
| `GPU_MEMORY_UTILIZATION` | `0.92` | Hybrid KV manager needs headroom |
| `KV_CACHE_DTYPE` | `fp8_e5m2` | Storage-only FP8; Ampere-safe. On Ada (L40S/4090) prefer `fp8_e4m3` |
| `TOOL_CALL_PARSER` | `gemma4` | Native parser; handles Gemma's custom non-JSON serialization |
| `REASONING_PARSER` | `gemma4` | Extracts thinking content into a separate field |
| `ENABLE_PREFIX_CACHING` | `true` | Works but SWA reduces hit rate (see Gotchas) |
| `ASYNC_SCHEDULING` | `true` | Safe on vLLM 0.14+ |
| `MIN_VRAM_GB` | `44` | Warn below this; override to `70` when `MODEL=google/gemma-4-31B-it` (BF16) |
| `SKIP_VRAM_CHECK` | `false` | Set `true` to silence the sub-44 GB warning |

## Alternative model IDs (all verified on HuggingFace)

| Repo | Size | Best on | Use case |
|---|---|---|---|
| `RedHatAI/gemma-4-31B-it-FP8-Dynamic` | ~33 GB | Ada / Hopper / Blackwell (native FP8) | **Default** |
| `RedHatAI/gemma-4-31B-it-FP8-block` | ~33 GB | Same as Dynamic | Block-quant alternative, v1.0 stable |
| `RedHatAI/gemma-4-31B-it-NVFP4` | ~17 GB | Hopper / Blackwell | Max context + speed on new hardware |
| `QuantTrio/gemma-4-31B-it-AWQ` | ~20 GB | All GPUs | Most downloads; reliable INT4 |
| `Intel/gemma-4-31B-it-int4-AutoRound` | ~17 GB | All GPUs (via `--quantization autoround`) | AutoRound INT4 |
| `nvidia/Gemma-4-31B-IT-NVFP4` | ~21 GB | Blackwell | `--quantization modelopt` path |
| `google/gemma-4-31B-it` | ~61 GB | A100-80GB+ | Full BF16 precision |

## Gotchas

- **Sliding-window prefix caching**: vLLM's Hybrid KV Cache Manager handles
  Gemma 4's interleaved SWA but with reduced prefix-cache efficiency vs a pure
  global-attention model. Hits still happen — just fewer than on dense Qwen /
  Llama. Track vLLM issues #3355, #14881, #20865.
- **Attention backend by GPU**: entrypoint picks `TRITON_ATTN` on Ampere/Ada
  — FA2 rejects Gemma 4's `head_dim=256` + interleaved SWA with
  `head_size not supported` — and `FLASH_ATTN` (FA3) on Hopper/Blackwell.
  FLASHINFER stays unsupported for Gemma's interleaved SWA (issue #20865) —
  don't override to it. Override via `VLLM_ATTENTION_BACKEND_OVERRIDE` if
  you know your stack supports a different backend.
- **Tool parser is young**: the `gemma4` parser shipped in early April 2026.
  Watch for bugs like PR #38847 (`tools` kwarg) and issue #39468 (format).
  Pin to vLLM ≥ 0.19.0 which includes these fixes.
- **FP8 on Ampere is software-emulated**: A100 runs FP8 weights through the
  Marlin W8A16 path — functional and stable, but ~60-70% of native FP8 TC
  throughput. On Hopper/Blackwell you get native FP8 speed.
- **Multimodal inputs** (image/video) work through the standard vLLM chat API
  with `{"type": "image_url", "image_url": {"url": "..."}}`. The `gemma4`
  parser doesn't affect multimodal paths — test separately.
- **BF16 override**: if you set `MODEL=google/gemma-4-31B-it` for full
  precision, also set `MIN_VRAM_GB=70` and cap `MAX_MODEL_LEN=65536`. The
  default VRAM check assumes FP8.

## RunPod deployment

Container image: `ghcr.io/knaeckebrothero/gemma4-dense-vllm:latest`
(built by `.github/workflows/llm-containers.yml` on pushes to `main`).

- `HUGGING_FACE_HUB_TOKEN=hf_xxx`
- `MAX_MODEL_LEN=131072` (default) or `262144` (A100-80GB+)
- Volume: 80 GB (FP8 weights ~33 GB + HF cache + headroom)
- Expose port 8000 as TCP (bypasses Cloudflare 30s timeout)
- `PUBLIC_KEY` auto-injected for SSH tunneling

## Choosing between dense vs MoE

Pick **this container (31B dense)** when: you want maximum reasoning depth,
multimodal generalist capability, and the best τ-bench / tool-use scores. The
FP8 default runs comfortably on both A100-80GB and L40S-48GB.

Pick **`gemma4-moe-vllm`** (26B A4B) when: you want higher throughput (4B
active params), or you're running on hardware smaller than L40S.
