# muse-glimmer-vllm

Optimized vLLM container for **`meta-models/Muse-Glimmer-30B`** (Meta's 30B
open-weights agentic VLM, released 2026-08-10, Apache 2.0).

Built to take over **GPU 2 on the university L40S box** from
`gemma4-moe-gpu2`. See `muse-glimmer-gpu2.container` for the Quadlet unit.

## Model specs

| Property | Value |
|---|---|
| Architecture | Dense 29.6B total (27.8B LM + 1.8B ViT-G/14 perception encoder) |
| Layers / hidden | 52 / 6656 |
| Attention | GQA 32 Q : 2 KV heads, head_dim 128, **gated** |
| Attention pattern | `[Local, Local, Local, Global]` repeating → 39 SWA(2048) + 13 global |
| RoPE | θ = 500 000, **local layers only** |
| Vocab | 202 048 (+2 048 special) |
| Modalities | text + image in, text out (≤4096 visual tokens/image) |
| Native context | 131 072 |
| Knowledge cutoff | 2026-01-04 |
| License | Apache 2.0 |
| HF model ID | `meta-models/Muse-Glimmer-30B` |

## Weight variants and what fits a 45 GiB L40S

| Repo | Format | On disk | L40S-48GB? |
|---|---|---|---|
| `meta-models/Muse-Glimmer-30B` | BF16 | 55.5 GiB | ✗ needs 80GB-class |
| **`RedHatAI/Muse-Glimmer-30B-FP8-block`** | **FP8 W8A8, 128×128 blocks** | **32.1 GiB** | **✓ default** |
| `RedHatAI/Muse-Glimmer-30B-NVFP4` | NVFP4 W4A4 | 21.8 GiB | ✗ **Blackwell-only** |
| `cyankiwi/Muse-Glimmer-30B-AWQ-INT4` | AWQ INT4 | 22.4 GiB | ✓ (Marlin) quality risk |
| `abhishekchohan/Muse-Glimmer-30B-GPTQ-INT4` | GPTQ INT4 | 22.2 GiB | ✓ (Marlin) quality risk |
| `meta-models/Muse-Glimmer-30B-assistant` | DFlash draft head | 4.8 GiB | optional, see below |

> **FP8-block is the only 8-bit build that exists.** There is no per-tensor /
> per-channel FP8 repo (the shape the Gemma 4 pods use). Block-scaled FP8 is
> DeepSeek-style: on sm_90+ it hits a CUTLASS block-scaled GEMM, on **Ada
> (sm_89) it falls back to vLLM's Triton `w8a8_block_fp8_matmul`**. It runs,
> but the throughput penalty vs per-tensor FP8 is unmeasured on this hardware.
> **Benchmark before promising anything.**
>
> The vision tower, embeddings and output head stay BF16 in the FP8 repo —
> that is why 27.8B "8-bit" params still weigh 32.1 GiB.
>
> The same unquantized remainder is why the **4-bit variants only drop to
> ~22 GiB, not ~17 GiB**: going FP8 → INT4 buys about 10 GiB, not the 2×
> the bit-width suggests. Sizes above are measured from the HF blob API
> (2026-08-17), not from the headline parameter count.

## Memory math on L40S-48GB (45 GiB usable, FP8-block weights ≈ 32.1 GiB)

KV per token per layer = 2 (K+V) × 2 KV heads × 128 head_dim = 512 elements.
Only 13 of 52 layers hold full-context KV; the other 39 are capped at the
2048-token sliding window.

| Precision | Global (13 L) | Local (39 L) | **Per 128K session** |
|---|---|---|---|
| bf16 KV | 1.625 GiB | 0.076 GiB | **1.70 GiB** |
| fp8 KV  | 0.813 GiB | 0.038 GiB | **0.85 GiB** |

At `--gpu-memory-utilization 0.95` → 42.7 GiB cap − 32.1 GiB weights ≈ 10.6 GiB,
of which ~4 GiB goes to activations + CUDA graphs + CUDA context, leaving
**~6.5 GiB of KV pool ≈ 3–4 concurrent full-128K sessions at bf16 KV.**

That comfortably clears the 1–2 concurrent target **without** an FP8 KV cache —
which is the point, because fp8_e4m3 KV + interleaved SWA on Ada is exactly
what forced the Gemma 4 pods onto `TRITON_ATTN`. Here we stay on `FLASH_ATTN`.

> If the hybrid/SWA KV manager ever fails to kick in, all 52 layers would hold
> full-context KV → **6.5 GiB per session**, i.e. exactly one. Check the
> `# GPU KV cache size` line in the boot log against the table above.

## Quick start

```bash
docker build -t muse-glimmer-vllm:latest .

# L40S-48GB at 128K (the deployed configuration)
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MODEL=RedHatAI/Muse-Glimmer-30B-FP8-block \
    -e MAX_MODEL_LEN=131072 \
    -e GPU_MEMORY_UTILIZATION=0.95 \
    -e SKIP_VRAM_CHECK=true \
    muse-glimmer-vllm:latest

# 80GB-class card, full BF16
docker run --gpus all -p 8000:8000 --ipc=host \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MODEL=meta-models/Muse-Glimmer-30B \
    muse-glimmer-vllm:latest
```

## Key environment variables

| Variable | Default | Notes |
|---|---|---|
| `MODEL` | `RedHatAI/Muse-Glimmer-30B-FP8-block` | Only 8-bit repo available |
| `MAX_MODEL_LEN` | `131072` | Native max; no RoPE-scaling headroom above it |
| `GPU_MEMORY_UTILIZATION` | `0.95` | Deliberately high — weights eat 32.1 GiB |
| `KV_CACHE_DTYPE` | `auto` (bf16) on Ada/Ampere, `fp8_e5m2` on Hopper+ | See memory math |
| `MAX_NUM_SEQS` | `4` | Sized for 1–2 concurrent sessions + headroom |
| `MAX_NUM_BATCHED_TOKENS` | `8192` | Lower than the MoE pods — activation peaks |
| `VLLM_ATTENTION_BACKEND_OVERRIDE` | `FLASH_ATTN` | **Never FLASHINFER** — kernels fail at startup |
| `TOOL_CALL_PARSER` | `muse_glimmer` | Mandatory — ATEM XML, not JSON |
| `REASONING_PARSER` | `muse_glimmer` | Mandatory — channel-scoped, not `<think>` |
| `GENERATION_CONFIG` | `auto` | Pulls Meta's temp 1.0 / top_p 0.95 / top_k 64 |
| `ENFORCE_EAGER` | `false` | Emergency lever: frees ~1–2 GiB, costs decode speed |
| `LIMIT_MM_PER_PROMPT` | *(unset)* | e.g. `{"image":2}` to bound vision activations |
| `ENABLE_DFLASH` | `false` | Speculative decoding, see below |
| `MIN_VRAM_GB` | `40` | Startup warning threshold |
| `SKIP_VRAM_CHECK` | `false` | `true` on the 45 GiB L40S |

## Gotchas

- **The base image is not a tagged vLLM release.** Muse Glimmer support merged
  upstream in [vllm#51655](https://github.com/vllm-project/vllm/pull/51655) on
  **2026-08-14**, three days *after* v0.27.1 (2026-08-11), the newest stable
  release — so it is in **no released version**. This image is built on
  `vllm/vllm-openai:muse-glimmer`, the launch-day branch build (commit
  `99a10304`, pushed 2026-08-11, CUDA 13.0.2, `TORCH_CUDA_ARCH_LIST` includes
  8.9 = Ada). Move the `FROM` to `v0.28.0` when it ships.
- **Do not run it greedy.** It is a reasoning model; Meta's own recipe is
  temperature 1.0 / top_p 0.95 / top_k 64. Identical requests at temperature 0
  were reported to return varying token counts. `--generation-config auto`
  handles this — don't let a client override temperature to 0.
- **Reasoning strength lives in the system prompt** (`low` | `medium` | `high` |
  `xhigh`), *not* in `chat_template_kwargs`. This differs from Gemma 4's
  `enable_thinking` and means the router needs **no `request_defaults`** for
  this route: the `muse_glimmer` reasoning parser sets
  `skip_special_tokens=False` itself.
- **Both parsers are required together.** Without them the ATEM tool-call XML
  and the reasoning channel both collapse into `content`.
- **FLASHINFER is unsupported** — kernels fail at startup (PR #51655 review).
- **Known parser rough edges from the PR discussion**, all relevant to agent
  workloads: JSON-decoded string parameters can raise schema errors; the model
  occasionally swallows an opening parameter tag and emits malformed calls;
  reasoning-parser accuracy degrades at long context; and when reasoning never
  terminates, requested JSON-schema grammar can be skipped entirely. Treat
  strict structured output as **unproven** on this model until tested.
- **DFlash speculative decoding** (`ENABLE_DFLASH=true`) adds a 4.8 GiB draft
  head — roughly half our KV pool, dropping us to ~1–2 concurrent sessions.
  `num_speculative_tokens` is fixed at 15 by the draft head's block size.
  Reported 3.1× on an RTX 5090 but flagged "still unusable" on SM120 Blackwell
  during review, and **unverified on Ada**. Off by default; it is the single
  most promising latency win to test *after* the base deployment is stable.

## Disk budget (university server, `llmprod`)

The root filesystem was at **97 % (18 GB free)** when this was written, and the
pull needs roughly **45 GB** (34 GB weights + ~10 GB image). Reclaimable, but
it needs a decision — these are model caches, not garbage:

| Item | Size | Notes |
|---|---|---|
| `gpt-oss-cache` podman volume | 26 GB | orphaned, 0 containers attached |
| `vision-cache` podman volume | 24 GB | orphaned, 0 containers attached |
| `gemma4-moe-vllm:sha-9931789` | 22.7 GB | superseded by `sha-fab4043` (running) |
| dangling images | 5.8 GB | safe |

Freeing the two orphaned volumes alone covers it. Deleting them means
re-downloading those weights if the retired gpt-oss / Pixtral pods ever return.

## Deployment

Container image: `ghcr.io/knaeckebrothero/muse-glimmer-vllm:latest`
(built by `.github/workflows/llm-containers.yml` on pushes to `main`).

Quadlet: `muse-glimmer-gpu2.container` → `~/.config/containers/systemd/`
(user `llmprod`), serving on **port 8092**.

Router: add the `Muse-Glimmer-30B` route and drop `127.0.0.1:8089` from the
Gemma pools — see `model-orchestrator/config.example.yaml`.

## Choosing between Muse Glimmer and the Gemma 4 MoE pods

Pick **Muse Glimmer 30B** when: you want a stronger single-session agent —
deeper reasoning, long-horizon planning, failure recovery, better tool-call
discipline — and 1–2 concurrent sessions is enough.

Pick **`gemma4-moe-vllm`** (26B-A4B) when: you want throughput. 4B active
params batch far better; the remaining pod on GPU 1 still serves that traffic.
