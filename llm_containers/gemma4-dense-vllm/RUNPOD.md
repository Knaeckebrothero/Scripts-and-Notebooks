# gemma4-dense-vllm on RunPod

Optimized vLLM image for **Gemma 4 31B dense** on HBM-class GPUs
(A100 / H100 / H200 / B200). Default: FP8-Dynamic quant (~31 GB) at 128K
context.

For **L40S-48GB**, deploy [`gemma4-dense-l40s-vllm`](../gemma4-dense-l40s-vllm/RUNPOD.md)
instead.

## Container image

`ghcr.io/knaeckebrothero/gemma4-dense-vllm:latest`

## GPU requirements

| GPU | Max context (FP8 default) | Notes |
|---|---|---|
| A100-80GB | 128K (2 concurrent, measured) or 32K (more) | Recommended starter |
| H100-80GB | 128K + headroom | Native FP8 TC speed |
| H200-141GB | 256K | Set `MAX_MODEL_LEN=262144` |
| B200 | 128K + | Native FP8 TC; FP4 weights also possible |
| L40S-48GB | **Not this image** | Use `gemma4-dense-l40s-vllm` |
| RTX 4090-24GB | **Not supported** | Use the MoE container |

Full BF16 (`MODEL=google/gemma-4-31B-it`) needs A100-80GB and caps at 64K;
also set `MIN_VRAM_GB=110`.

## Environment variables

**Required only if the default repo 401s** (RedHatAI re-packs are usually
ungated — try without first):
```
HUGGING_FACE_HUB_TOKEN=hf_xxx
```

**Optional:**

| Variable | Default | Notes |
|---|---|---|
| `MODEL` | `RedHatAI/gemma-4-31B-it-FP8-Dynamic` | See alternatives in README |
| `MAX_MODEL_LEN` | `131072` | Raise to `262144` on H200 |
| `MAX_NUM_SEQS` | `2` | Measured 128K KV ceiling (2× ~124K ≈ 70% pool, 0 preemption); admits 2, queues the rest. Raise for shorter contexts |
| `MAX_NUM_BATCHED_TOKENS` | `8192` | Chunked-prefill upper bound |
| `KV_CACHE_DTYPE` | `auto` (BF16) | **Do not change** — Issue #40388 |
| `GPU_MEMORY_UTILIZATION` | `0.95` | Default; maximizes KV so one 128K seq fits. vLLM recipe sanctions 0.90–0.95 for FP8 |
| `LIMIT_MM_PER_PROMPT` | `image=5,audio=0` | Up to 5 images/request; audio unsupported on 31B (`audio_config: null`) |
| `ENABLE_THINKING` | `true` | Server-side `<\|think\|>` injection |
| `TOOL_CALL_PARSER` | `gemma4` | |
| `REASONING_PARSER` | `gemma4` | |
| `API_KEY` | (none) | Set to require bearer auth on `/v1` |

## Pod configuration

- **GPU:** A100 SXM/PCIe 80GB or H100 80GB (default targets)
- **Container disk:** 20 GB
- **Volume:** 80 GB mounted at `/mnt/cache`
  (covers HF weights ~31 GB + torch_compile_cache + headroom)
- **Ports:**
  - `8000` as **TCP** (required — bypasses Cloudflare 30s timeout)
  - `22` as **TCP** (optional, SSH tunneling)
- **PUBLIC_KEY:** auto-injected by RunPod from account SSH keys

The `/mnt/cache` volume drops cold-boot from ~12-15 min to ~2-3 min on
subsequent starts. Without it, every pod restart re-downloads ~31 GB.

## Accessing the API

**Direct TCP:**
```bash
curl http://<pod-id>.runpod.net:<tcp-port>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"RedHatAI/gemma-4-31B-it-FP8-Dynamic",
    "messages":[{"role":"user","content":"Hello"}],
    "skip_special_tokens": false
  }'
```

**Required**: send `"skip_special_tokens": false` in any reasoning-mode
probe so the `<|channel|>` thought delimiters reach the gemma4 parser
(Issue #38855).

**SSH tunnel:**
```bash
ssh -L 8000:localhost:8000 root@<pod-id>.runpod.net -p <ssh-port>
curl http://localhost:8000/v1/models
```

**Python:**
```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="dummy")
r = client.chat.completions.create(
    model="RedHatAI/gemma-4-31B-it-FP8-Dynamic",
    messages=[{"role":"user","content":"Hello!"}],
    extra_body={"skip_special_tokens": False})
```

## Gotchas

- **`Background writer channel closed` = DISK FULL, not a bug.** If the boot
  crash-loops during weight download with `RuntimeError: Internal Writer Error:
  Background writer channel closed` (from `hf_xet`), the `/mnt/cache` volume is
  too small — the default model is ~31 GB (one shard ~27 GB) and the Xet
  downloader needs staging headroom. Look just above the traceback for
  `Not enough free disk space`. **Fix:** attach a **fresh** 80 GB volume at
  `/mnt/cache` (a too-small or already-mounted volume that received a prior
  failed download leaves orphaned partial blobs — free space collapses across
  restarts, so don't reuse it; use a clean volume or clear
  `/mnt/cache/huggingface`). The entrypoint now prints an explicit warning when
  free space at `HF_HOME` is below `MIN_DISK_GB` (default 40); set
  `SKIP_DISK_CHECK=true` to silence on a known-warm volume.
- **HF gating:** RedHatAI re-pack is usually ungated. If the pod fails
  with 401, set `HUGGING_FACE_HUB_TOKEN` and accept the Gemma license at
  huggingface.co/google/gemma-4-31B-it under the same account.
- **HEALTHCHECK 1200s start period**: cold boot is dominated by the
  ~10 min HF download + ~50s torch.compile pass. Don't shorten this —
  premature failures induce restart loops.
- **Sliding-window prefix cache:** interleaved SWA reduces prefix-cache
  hit rate vs pure-global attention — still a net win, don't disable.
- **FLASHINFER not supported** (vLLM #20865). Entrypoint uses
  `FLASH_ATTN` on Hopper/Blackwell, `TRITON_ATTN` on Ampere; don't override.
- **FP8 on Ampere is emulated** via Marlin W8A16 — ~60-70% of native FP8
  TC throughput. Fully functional, just slower than on Hopper/Blackwell.
- **First boot downloads ~31 GB** — persistent volume keeps it warm
  across pod restarts.
