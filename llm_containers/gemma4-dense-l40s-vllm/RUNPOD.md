# gemma4-dense-l40s-vllm on RunPod

L40S-tuned vLLM image for **Gemma 4 31B dense at INT4 AWQ**. Targets ~5
concurrent agent sessions at 8K context on a single L40S-48GB.

## Container image

`ghcr.io/knaeckebrothero/gemma4-dense-l40s-vllm:latest`

## GPU requirements

| GPU | Verdict |
|---|---|
| **L40S-48GB** | Primary target. Default config = 5 concurrent at 8K. |
| RTX 4090-24GB | Refuses to start (INT4 ~16.5 GB + KV won't fit). |
| A100-80GB | Refuses to start. Use `gemma4-dense-vllm` instead. |
| H100/H200 | Refuses to start. Use `gemma4-dense-vllm` instead. |

Set `ALLOW_NON_ADA=true` to bypass the refusal (not recommended; FP8 KV
quality degrades on Ampere, INT4 wastes HBM bandwidth on Hopper).

## Environment variables

**Required only if the AWQ repo is rate-limited:**
```
HUGGING_FACE_HUB_TOKEN=hf_xxx
```

**Optional:**

| Variable | Default | Notes |
|---|---|---|
| `MODEL` | `cyankiwi/gemma-4-31B-it-AWQ-4bit` | Any INT4 AWQ Gemma 4 31B checkpoint |
| `MAX_MODEL_LEN` | `8192` | Raise to `16384` for ~2-3 concurrent, `32768` for single-user |
| `MAX_NUM_SEQS` | `10` | KV-pool bound |
| `KV_CACHE_DTYPE` | `fp8_e4m3` | Mandatory; do not change |
| `LIMIT_MM_PER_PROMPT` | `image=0,audio=0` | Set `image=2,audio=0` to enable vision |
| `ENFORCE_EAGER` | `true` | Mandatory on Ada under TRITON_ATTN |
| `API_KEY` | (none) | Set to require bearer auth |

## Pod configuration

- **GPU:** L40S 48GB (or RTX A6000 Ada 48GB)
- **Container disk:** 20 GB
- **Volume:** 60 GB mounted at `/mnt/cache` (covers HF weights ~16.5 GB +
  Triton kernel cache + AWQ Marlin compile cache)
- **Ports:**
  - `8000` as **TCP** (required — bypasses Cloudflare 30s timeout)
  - `22` as **TCP** (optional, SSH tunneling)
- **PUBLIC_KEY:** auto-injected by RunPod from account SSH keys

The `/mnt/cache` volume is the difference between a 5-10 min cold boot and
a 1-2 min warm boot. Without it the AWQ checkpoint and Triton kernels are
re-downloaded and re-JIT-compiled on every restart.

## Accessing the API

**Direct TCP:**
```bash
curl http://<pod-id>.runpod.net:<tcp-port>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"cyankiwi/gemma-4-31B-it-AWQ-4bit",
    "messages":[{"role":"user","content":"Hello"}],
    "skip_special_tokens": false
  }'
```

**Required**: `"skip_special_tokens": false` for any reasoning-mode probe;
otherwise the `<|channel|>` thought delimiters are stripped before the
gemma4 reasoning parser can extract them (Issue #38855).

**SSH tunnel:**
```bash
ssh -L 8000:localhost:8000 root@<pod-id>.runpod.net -p <ssh-port>
curl http://localhost:8000/v1/models
```

## Gotchas

- **Concurrency ≠ context**: 8K context × 5 agents is the design point. Each
  step away (16K, 32K, multimodal-on) costs concurrency 1:1.
- **HEALTHCHECK 900s start period**: AWQ Marlin warmup is slow on Ada. Don't
  shorten this — premature failures cause restart loops.
- **No FlashAttention override**: `TRITON_ATTN` is forced and there is no
  L40S-supported alternative. FA2 hits the 256 head-size SRAM limit on the
  10 global layers; FlashInfer/FA4 corrupt outputs.
- **No CUDA graphs**: `--enforce-eager` is mandatory. Don't unset it.
- **AWQ tool-call drift**: at long contexts, AWQ marginally degrades JSON
  formatting adherence vs FP8/BF16. Mitigate via prefix-cached tool schemas
  (default ON).

## Choosing this image vs gemma4-dense-vllm

Pick **`gemma4-dense-l40s-vllm`** when:
- Hardware is an L40S-48GB, A6000 Ada, or similar 48 GB Ada-class GPU.
- Workload is **multiple concurrent agent sessions** at moderate (≤8K) context.

Pick **`gemma4-dense-vllm`** when:
- Hardware is A100-80GB / H100 / H200 / B200.
- Workload needs long context (32K-128K) or full multimodal (image + video).
- You want the FP8-Dynamic weight path with native FP8 tensor cores.
