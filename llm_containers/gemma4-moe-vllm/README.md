# gemma4-moe-vllm

Optimized vLLM container for **`google/gemma-4-26B-A4B`** (Gemma 4 MoE, 4B active).

## Model specs

| Property | Value |
|---|---|
| Architecture | 26B total / 4B active (MoE, 8 of 128 experts + 1 shared) |
| Attention | GQA with 5:1 interleaved local-SWA (window 1024) + global |
| Modalities | text + image + video (no audio) |
| Native context | 256K |
| License | Apache 2.0 |
| HF model ID | `google/gemma-4-26B-A4B-it` |

## Memory math on A100-80GB (BF16 weights ≈ 50 GB)

MoE KV cache is thin because routing touches only 4B active params per token.

| Context | KV cache | Total | Verdict |
|---|---|---|---|
| 64K  | ~1.3 GB | ~52 GB | Trivial fit |
| 128K | ~2.6 GB | ~54 GB | **Default** |
| 256K | ~5.2 GB | ~56 GB | Still comfortable |

## Quick start

```bash
docker build -t gemma4-moe-vllm:latest .

# A100-80GB at default 128K
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    gemma4-moe-vllm:latest

# Full 256K context
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MAX_MODEL_LEN=262144 \
    gemma4-moe-vllm:latest

# L40S-48GB — REQUIRES a quantized model. BF16 (~50 GB) does NOT fit.
# FP8 (~27 GB) fits comfortably at 128K context with native FP8 TC speed (Ada).
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MODEL=RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic \
    -e KV_CACHE_DTYPE=fp8_e4m3 \
    -e MAX_MODEL_LEN=131072 \
    gemma4-moe-vllm:latest

# L40S-48GB with AWQ INT4 (~14 GB) — full 256K context, high concurrency headroom
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MODEL=cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit \
    -e KV_CACHE_DTYPE=fp8_e4m3 \
    -e MAX_MODEL_LEN=262144 \
    gemma4-moe-vllm:latest
```

> **Google does NOT ship official FP8/INT4 variants of Gemma 4** (unlike Gemma 3
> where Google released QAT-INT4). All quants are community / RedHatAI re-packs.
>
> Verified L40S-friendly variants:
> - `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic` (~27 GB, vLLM-tested)
> - `protoLabsAI/gemma-4-26B-A4B-it-FP8` (~27 GB, supports FP8 KV cache)
> - `RedHatAI/gemma-4-26B-A4B-it-NVFP4` (~17 GB, best on Hopper/Blackwell — A100/L40S use Marlin fallback)
> - `cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit` (~14 GB, AWQ INT4)
>
> MoE quantization in vLLM is fragile (fused 3D expert tensors break some
> quant tools) — prefer RedHatAI / protoLabsAI over community GPTQ. Ada (L40S /
> 4090) supports `KV_CACHE_DTYPE=fp8_e4m3` with better dynamic range than the
> `e5m2` default tuned for Ampere.

## Key environment variables

| Variable | Default | Notes |
|---|---|---|
| `MODEL` | `google/gemma-4-26B-A4B-it` | MoE instruction-tuned |
| `MAX_MODEL_LEN` | `131072` | Comfortable; `262144` also fits on A100-80GB |
| `GPU_MEMORY_UTILIZATION` | `0.95` | Plenty of headroom |
| `KV_CACHE_DTYPE` | `fp8_e5m2` | Storage-only FP8, Ampere-safe |
| `MAX_NUM_SEQS` | `64` | 4B active = happy to batch more |
| `MAX_NUM_BATCHED_TOKENS` | `16384` | Higher throughput than dense |
| `TOOL_CALL_PARSER` | `gemma4` | Same parser as dense variant |
| `REASONING_PARSER` | `gemma4` | Extracts thinking into the `reasoning` field |
| `ENABLE_THINKING` | `true` | Default thinking on for all requests via `--default-chat-template-kwargs`. Necessary but not sufficient — see **Gotchas** for the client-side `skip_special_tokens:false` requirement |
| `MIN_VRAM_GB` | `56` | Warn at startup if GPU has less; override if loading a quant variant |
| `SKIP_VRAM_CHECK` | `false` | Set `true` to silence the L40S/<60 GB VRAM warning |

## Gotchas

- **Same SWA + prefix cache caveats** as the dense variant — hybrid KV manager
  handles it, but prefix-cache hit rate is reduced vs pure global attention.
- **FLASHINFER is unsupported** (vLLM #20865). Don't override.
- **Reasoning (`reasoning`) needs a client-side flag.** Enabling thinking
  server-side (`ENABLE_THINKING=true`, default) is not enough: vLLM strips the
  `<|channel>` delimiters before the `gemma4` reasoning parser runs unless the
  **client** also sends `"skip_special_tokens": false` (vLLM
  [#38855](https://github.com/vllm-project/vllm/issues/38855), open on v0.22.0).
  The `model-orchestrator` injects this for the Gemma 4 routes via
  `request_defaults`; direct callers must add it themselves. **Streaming
  caveat:** the parser's streaming path is still affected by #38855, so with
  `stream=true` the `<|channel>` markers may surface inline in `content` even
  with the flag set — reliable separation is non-streaming only for now.
- **MoE + prefix caching**: some A3B-class MoEs have shown cache-hit
  degradation on vLLM (issue #36493 for Qwen3.5-35B-A3B). Benchmark against
  your traffic pattern; disable with `ENABLE_PREFIX_CACHING=false` if you see
  contamination.
- **Lower capability ceiling** than the 31B dense. If your agent tasks need
  deep reasoning (long chains, hard math, hard planning), prefer the dense
  container. This is the fast/throughput/long-context pick.

## RunPod deployment

Container image: `ghcr.io/knaeckebrothero/gemma4-moe-vllm:latest`
(built by `.github/workflows/llm-containers.yml` on pushes to `main`).

- `HUGGING_FACE_HUB_TOKEN=hf_xxx`
- `MAX_MODEL_LEN=131072` or `262144`
- Volume: 100 GB
- Port 8000 as TCP
- `PUBLIC_KEY` auto-injected for SSH tunneling

## Choosing between MoE vs dense

Pick **this container (26B A4B MoE)** when: you want **throughput + long
context**, you're running multiple concurrent agent sessions, or your hardware
is <80GB. Tokens/sec will feel like a 4B dense model.

Pick **`gemma4-dense-vllm`** (31B dense) when: you want **maximum reasoning
depth** and can accept 2-3x lower throughput + tighter memory.
