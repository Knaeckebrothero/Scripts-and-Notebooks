# gemma4-dense-l40s-vllm

L40S-specific vLLM container for **Gemma 4 31B dense at INT4 AWQ**. Tuned to
serve **5 concurrent agent sessions at 8K context** on a single L40S-48GB.

For A100 / H100 / H200 use the sibling [`gemma4-dense-vllm`](../gemma4-dense-vllm/)
image instead — it ships FP8-Dynamic weights at full 128K context, which is
the right fit on HBM-class GPUs.

## Why a separate L40S image

The L40S is fundamentally a different deployment surface than A100/H100:

| Constraint | A100-80GB / H100 | L40S-48GB |
|---|---|---|
| VRAM | 80 GB | 48 GB |
| Memory bandwidth | 2 TB/s (HBM2e/HBM3) | 864 GB/s (GDDR6) |
| Best weight quant | FP8-Dynamic (~31 GB) | INT4 AWQ (~16.5 GB) |
| Best KV dtype | BF16 (`auto`) — Issue #40388 forbids FP8 KV with FP8 weights | `fp8_e4m3` (mandatory) |
| Useful context | 128K (1 concurrent) | 8K (5 concurrent) |
| CUDA graphs | enabled (capture sizes 1-16) | **disabled** (`--enforce-eager`) |
| Multimodal | enabled | disabled |

The decisive constraint is the 26.7 GB KV pool that remains after INT4 weights
load. Halving KV via `fp8_e4m3` and clamping context to 8K is what unlocks
the 5–10 concurrent agent target on this hardware.

## Model specs

| Property | Value |
|---|---|
| Default checkpoint | `cyankiwi/gemma-4-31B-it-AWQ-4bit` (INT4 AWQ) |
| Resident size | ~16.5 GB |
| Base model | `google/gemma-4-31B-it` (30.7B dense, 60 layers, head_dim 256/512) |
| Attention | Standard GQA with 5:1 interleaved local-SWA + global |
| Native context | 256K (clamped to 8K for agent serving) |
| License | Apache 2.0 (base) + community quant |

## KV-cache math (fp8_e4m3, 0.547 MB per token)

| Context | Per-session KV | Concurrent on L40S | Verdict |
|---|---|---|---|
| 8,192 | 4.48 GB | **~5 (safe)** | Default. Agent-serving sweet spot. |
| 16,384 | 8.96 GB | ~2-3 | Severe concurrency restriction. |
| 32,768 | 17.92 GB | 1 | Single-user. |
| 65,536 | 35.84 GB | 0 | OOM. |
| 131,072 | 71.68 GB | 0 | Physically impossible. |

26.7 GB KV pool = (48 GB - 16.5 GB AWQ weights - 2 GB PyTorch overhead - 2.8 GB
graph/Marlin scratch) at `--gpu-memory-utilization 0.92`.

## Quick start

```bash
docker build -t gemma4-dense-l40s-vllm:latest .

# Default — 5 concurrent agents at 8K
docker run --gpus all -p 8000:8000 --ipc=host \
    -v /opt/cache:/mnt/cache \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    gemma4-dense-l40s-vllm:latest

# Single-user 32K context (e.g. document RAG)
docker run --gpus all -p 8000:8000 --ipc=host \
    -v /opt/cache:/mnt/cache \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e MAX_MODEL_LEN=32768 \
    -e MAX_NUM_SEQS=1 \
    gemma4-dense-l40s-vllm:latest

# Re-enable vision (cuts ~1 concurrent session worth of KV)
docker run --gpus all -p 8000:8000 --ipc=host \
    -v /opt/cache:/mnt/cache \
    -e HUGGING_FACE_HUB_TOKEN=hf_xxx \
    -e LIMIT_MM_PER_PROMPT="image=2,audio=0" \
    gemma4-dense-l40s-vllm:latest
```

The `-v /opt/cache:/mnt/cache` mount is **strongly recommended** — without it,
every container restart re-downloads ~16.5 GB of weights and re-JITs all
kernels (~5-10 min cold). With the volume, subsequent boots are ~1-2 min.

## Key environment variables

| Variable | Default | Notes |
|---|---|---|
| `MODEL` | `cyankiwi/gemma-4-31B-it-AWQ-4bit` | Any INT4 AWQ Gemma 4 31B checkpoint |
| `QUANTIZATION` | `awq` | Set to `autoround` for Intel's AutoRound INT4 |
| `MAX_MODEL_LEN` | `8192` | Load-bearing flag; raising hurts concurrency |
| `MAX_NUM_SEQS` | `10` | Capped by 26.7 GB KV pool ÷ per-session size |
| `MAX_NUM_BATCHED_TOKENS` | `4096` | DoS safety bound (Issue #29) |
| `KV_CACHE_DTYPE` | `fp8_e4m3` | Mandatory; do not change |
| `GPU_MEMORY_UTILIZATION` | `0.92` | Higher risks preemption loops |
| `ENFORCE_EAGER` | `true` | Disables CUDA graphs (mandatory on Ada) |
| `LIMIT_MM_PER_PROMPT` | `image=0,audio=0` | Text-only by default |
| `TOOL_CALL_PARSER` | `gemma4` | |
| `REASONING_PARSER` | `gemma4` | |
| `ENABLE_THINKING` | `true` | Server-side `<\|think\|>` injection |
| `ALLOW_NON_ADA` | `false` | Set `true` to bypass the Ada-only refusal (debugging) |

## Production verification probes

Before promoting, run the three checks from the L40S report:

### 1. Reasoning-parser isolation

Confirms `<|channel|>thought\n` is routed to `reasoning_content`, not leaked
into the user-facing `content` field.

```bash
curl -k -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cyankiwi/gemma-4-31B-it-AWQ-4bit",
    "messages": [{"role":"user","content":"Compute 23 * 47, show your reasoning."}],
    "temperature": 0.1,
    "max_tokens": 1024,
    "skip_special_tokens": false,
    "extra_body": {"chat_template_kwargs": {"enable_thinking": true}}
  }'
```

Validation: response payload contains a non-empty `reasoning_content` key
*and* the final numerical answer in `content`. **Required: client must send
`"skip_special_tokens": false`** — otherwise `<|channel|>` delimiters are
stripped before the parser sees them (vLLM Issue #38855). In production the
`model-orchestrator` injects this for the Gemma 4 routes. **Streaming caveat:**
#38855 still affects the streaming parser path, so this probe is reliable for
non-streaming (`stream=false`) only.

### 2. Parallel tool calling

Confirms the `gemma4` parser emits a clean OpenAI-compatible `tool_calls`
array (no trailing garbage, no unescaped quotes) under concurrent load.

```bash
curl -k -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cyankiwi/gemma-4-31B-it-AWQ-4bit",
    "messages": [{"role":"user","content":"Weather in London and Paris?"}],
    "tools": [{"type":"function","function":{
      "name":"get_weather",
      "description":"Get current weather.",
      "parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}
    }}],
    "tool_choice": "auto"
  }'
```

Validation: `tool_calls` array has two entries (London + Paris), each with
parseable JSON in `arguments`.

### 3. Concurrent KV-cache stress

Launch 10 background curls with 4K-token prompts. Watch
`vllm:num_requests_waiting_by_reason{reason="capacity"}` and
`vllm:num_preemptions_total`.

```bash
for i in $(seq 1 10); do
  curl -sS http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"cyankiwi/gemma-4-31B-it-AWQ-4bit\",\"messages\":[{\"role\":\"user\",\"content\":\"$(yes 'word' | head -n 4000 | tr '\n' ' ')\"}],\"max_tokens\":50}" &
done
wait
```

If preemptions fire, lower `MAX_NUM_SEQS` or `MAX_MODEL_LEN`.

## Operational landmines

1. **Thermal throttling**: L40S is 350W TDP with passive/blower cooling. Under
   sustained decode at high batch, core temps can hit 80°C and downclock
   tensor cores. Monitor `vllm:e2e_request_latency_seconds` for sudden spikes.
2. **Don't override the attention backend**: `FA2` rejects `head_dim=512` on
   the 10 global layers (256 SRAM cap). `FlashInfer` corrupts outputs at the
   final layer due to heterogeneous head dims. `TRITON_ATTN` is the only
   stable choice.
3. **Don't disable `--enforce-eager`**: CUDA graph capture under TRITON_ATTN
   on Ada double-counts KV blocks during warmup, causing OOM crashes before
   the engine reaches steady state.
4. **HEALTHCHECK start period**: 900s. AWQ Marlin JIT compile + initial
   warmup is slow on Ada. Premature health checks induce restart loops.
5. **AWQ + tool-call long-context drift**: AWQ INT4 marginally degrades JSON
   tool-call adherence as the conversation grows past ~4K tokens. The
   prefix cache (storing tool schemas in high precision on first compute)
   mitigates this — confirm via `vllm:prefix_cache_hits` that schemas are
   being served from cache.

## RunPod deployment

See [`RUNPOD.md`](./RUNPOD.md). Container image:
`ghcr.io/knaeckebrothero/gemma4-dense-l40s-vllm:latest` (built by
`.github/workflows/llm-containers.yml` on pushes to `main`).
