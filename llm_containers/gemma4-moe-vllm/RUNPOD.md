# gemma4-moe-vllm on RunPod

Optimized vLLM image for **Gemma 4 26B MoE (4B active)** — multimodal
(text/image/video) throughput + long-context generalist. Tokens/sec feels
like a 4B dense model.

## Container image

`ghcr.io/knaeckebrothero/gemma4-moe-vllm:latest`

## GPU requirements

| GPU | Model | Max context |
|---|---|---|
| A100-80GB | `google/gemma-4-26B-A4B-it` (BF16, ~50 GB) | 256K |
| L40S-48GB | `RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic` (~27 GB) | 128K |
| L40S-48GB | `cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit` (~14 GB) | 256K |

BF16 does NOT fit L40S — override `MODEL` to a quant variant.

## Environment variables

**Required** (default `google/*` repo is gated — accept the Gemma license
at huggingface.co/google/gemma-4-26B-A4B-it under the same HF account first):
```
HUGGING_FACE_HUB_TOKEN=hf_xxx
```

**Optional:**
| Variable | Default | Notes |
|---|---|---|
| `MAX_MODEL_LEN` | `131072` | `262144` also fits A100-80GB |
| `MODEL` | `google/gemma-4-26B-A4B-it` | See alternatives above |
| `KV_CACHE_DTYPE` | `fp8_e5m2` | On Ada (L40S/4090) use `fp8_e4m3` |
| `GPU_MEMORY_UTILIZATION` | `0.95` | MoE has headroom |
| `MAX_NUM_SEQS` | `64` | Batch more — only 4B active per token |
| `TOOL_CALL_PARSER` | `gemma4` | Same parser as dense |
| `REASONING_PARSER` | `gemma4` | Extracts thinking into `reasoning` |
| `ENABLE_THINKING` | `true` | Default thinking on; needs client `skip_special_tokens:false` (see Gotchas) |
| `API_KEY` | (none) | Set to require bearer auth |

## Pod configuration

- **GPU:** A100-80GB (BF16 default) or L40S-48GB (needs quant variant)
- **Container disk:** 20 GB
- **Volume:** 100 GB mounted at `/mnt/cache` (entrypoint sets `HF_HOME=/mnt/cache/huggingface` + `VLLM_CONFIG_ROOT=/mnt/cache/vllm`; was `/root/.cache/huggingface` before v0.22.0 — update the mount path)
- **Ports:**
  - `8000` as **TCP** (required — bypasses Cloudflare 30s timeout)
  - `22` as **TCP** (optional, SSH)
- **PUBLIC_KEY:** auto-injected from RunPod account

## Accessing the API

**Direct TCP:**
```bash
curl http://<pod-id>.runpod.net:<tcp-port>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"google/gemma-4-26B-A4B-it","messages":[{"role":"user","content":"Hello"}]}'
```

**SSH tunnel:**
```bash
ssh -L 8000:localhost:8000 root@<pod-id>.runpod.net -p <ssh-port>
curl http://localhost:8000/v1/models
```

**Multimodal (image input):**
```json
{
  "model": "google/gemma-4-26B-A4B-it",
  "messages": [{"role": "user", "content": [
    {"type": "text", "text": "Describe this image"},
    {"type": "image_url", "image_url": {"url": "https://..."}}
  ]}]
}
```

## Gotchas

- **HF gating:** `google/gemma-4-*` repos require license acceptance in your
  HF account before any download works — the token alone isn't enough.
- **MoE quantization is fragile** — prefer RedHatAI / protoLabsAI re-packs
  over community GPTQ quants (fused 3D expert tensors break some quant tools).
- **SWA prefix caching:** interleaved sliding-window attention reduces
  prefix-cache hit rate (vLLM #3355, #14881). Net win; leave on.
- **FLASHINFER not supported** (vLLM #20865). Entrypoint uses `FLASH_ATTN`;
  don't override.
- **Clean `reasoning` needs a client flag.** `ENABLE_THINKING=true` (default)
  turns thinking on, but vLLM only splits `reasoning` from `content` when the
  request also carries `"skip_special_tokens": false` (vLLM #38855). The
  `model-orchestrator` injects this for the Gemma 4 routes; direct callers must
  send it. Streaming is still affected by #38855 — reliable for non-streaming.
- **Lower reasoning depth** than the 31B dense. Pick dense for deep chains
  and hard math; pick MoE for throughput + long context + concurrent sessions.
- **First boot downloads ~27-50 GB** depending on MODEL — use a persistent
  volume.
