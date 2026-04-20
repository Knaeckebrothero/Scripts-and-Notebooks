# gemma4-dense-vllm on RunPod

Optimized vLLM image for **Gemma 4 31B dense** — multimodal (text/image/video)
reasoning generalist. Default: FP8 dynamic quant (~33 GB), fits A100-80GB and
L40S-48GB comfortably.

## Container image

`ghcr.io/knaeckebrothero/gemma4-dense-vllm:latest`

## GPU requirements

| GPU | Max context (FP8 default) | Notes |
|---|---|---|
| A100-80GB | 256K | Recommended. Set `MAX_MODEL_LEN=262144` |
| L40S-48GB | 128K | Set `KV_CACHE_DTYPE=fp8_e4m3` for native FP8 speed |
| RTX 4090-24GB | 32K | Override `MODEL=QuantTrio/gemma-4-31B-it-AWQ` |

Full BF16 (`MODEL=google/gemma-4-31B-it`) needs A100-80GB and caps at 64K;
also set `MIN_VRAM_GB=70`.

## Environment variables

**Required only if default repo 401s** (RedHatAI re-packs are usually
ungated — try without first):
```
HUGGING_FACE_HUB_TOKEN=hf_xxx
```

**Optional:**
| Variable | Default | Notes |
|---|---|---|
| `MAX_MODEL_LEN` | `131072` | Raise to `262144` on A100-80GB |
| `MODEL` | `RedHatAI/gemma-4-31B-it-FP8-Dynamic` | See alternatives below |
| `KV_CACHE_DTYPE` | `fp8_e5m2` | Ampere-safe; on Ada use `fp8_e4m3` |
| `GPU_MEMORY_UTILIZATION` | `0.92` | Leave as-is |
| `TOOL_CALL_PARSER` | `gemma4` | Gemma's custom non-JSON format |
| `REASONING_PARSER` | `gemma4` | Extracts thinking content |
| `API_KEY` | (none) | Set to require bearer auth on `/v1` |

Alternative models: `RedHatAI/gemma-4-31B-it-NVFP4` (~17 GB, Hopper+),
`QuantTrio/gemma-4-31B-it-AWQ` (~20 GB, all GPUs).

## Pod configuration

- **GPU:** A100 SXM/PCIe 80GB (default) or L40S 48GB
- **Container disk:** 20 GB
- **Volume:** 80 GB mounted at `/root/.cache/huggingface`
- **Ports:**
  - `8000` as **TCP** (required — bypasses Cloudflare 30s timeout)
  - `22` as **TCP** (optional, SSH tunneling)
- **PUBLIC_KEY:** auto-injected by RunPod from account SSH keys

## Accessing the API

**Direct TCP:**
```bash
curl http://<pod-id>.runpod.net:<tcp-port>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"RedHatAI/gemma-4-31B-it-FP8-Dynamic","messages":[{"role":"user","content":"Hello"}]}'
```

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
    messages=[{"role":"user","content":"Hello!"}])
```

## Gotchas

- **HF gating:** RedHatAI re-pack is usually ungated. If the pod fails with
  401, set `HUGGING_FACE_HUB_TOKEN` and accept the Gemma license at
  huggingface.co/google/gemma-4-31B-it under the same account.
- **Sliding-window prefix cache:** interleaved SWA reduces prefix-cache hit
  rate vs pure-global attention — still a net win, don't disable.
- **FLASHINFER not supported** (vLLM #20865). Entrypoint uses `FLASH_ATTN`;
  don't override.
- **FP8 on Ampere is emulated** via Marlin W8A16 — ~60-70% of native FP8 TC
  throughput. Fully functional, just slower than on Hopper/Blackwell.
- **First boot downloads ~33 GB** — persistent volume keeps it warm across
  pod restarts.
