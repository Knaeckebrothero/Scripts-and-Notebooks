# qwen3-next-vllm on RunPod

Optimized vLLM image for **Qwen3-Next 80B MoE (3B active)** —
frontier-adjacent agentic generalist on a single A100-80GB. Text-only.
Default: AWQ INT4 community quant (~46 GB weights).

## Container image

`ghcr.io/knaeckebrothero/qwen3-next-vllm:latest`

## GPU requirements

A100-80GB minimum. FP8 KV cache is **not supported** for Qwen3-Next hybrid
attention (vLLM #26646), so KV VRAM cannot be reclaimed.

| Context | KV cache (bf16) | Total | A100-80GB |
|---|---|---|---|
| 32K  | ~4 GB  | ~50 GB | Comfortable |
| 64K  | ~8 GB  | ~54 GB | **Default, safe** |
| 128K | ~16 GB | ~62 GB | Tight |
| 256K | ~32 GB | ~78 GB | Needs H100/H200 or 2×A100 |

## Environment variables

**Not required** — default `cyankiwi/*` community quant is ungated. Only
set `HUGGING_FACE_HUB_TOKEN` if you override `MODEL` to a gated repo.

**Optional:**
| Variable | Default | Notes |
|---|---|---|
| `MAX_MODEL_LEN` | `65536` | Raise only on H100+ |
| `MODEL` | `cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit` | See below |
| `QUANTIZATION` | `awq_marlin` | `auto` for FP8 variants |
| `KV_CACHE_DTYPE` | `bfloat16` | **Don't change** — FP8 KV broken here |
| `GPU_MEMORY_UTILIZATION` | `0.93` | `0.95` on Hopper |
| `TOOL_CALL_PARSER` | `qwen3_xml` | **Don't use `qwen3_coder`** (see gotchas) |
| `REASONING_PARSER` | (empty) | Set to `qwen3` to extract thinking |
| `API_KEY` | (none) | Set to require bearer auth |

Swap to `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8` (coding) or
`Qwen/Qwen3-32B-AWQ` (dense) for different capabilities; add
`QUANTIZATION=auto` for the FP8 variant.

## Pod configuration

- **GPU:** A100-80GB (required at default MODEL)
- **Container disk:** 20 GB
- **Volume:** 100 GB mounted at `/root/.cache/huggingface`
- **Ports:**
  - `8000` as **TCP** (required — bypasses Cloudflare 30s timeout)
  - `22` as **TCP** (optional, SSH)
- **PUBLIC_KEY:** auto-injected from RunPod account

## Accessing the API

**Direct TCP:**
```bash
curl http://<pod-id>.runpod.net:<tcp-port>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit","messages":[{"role":"user","content":"Hello"}]}'
```

**SSH tunnel:**
```bash
ssh -L 8000:localhost:8000 root@<pod-id>.runpod.net -p <ssh-port>
curl http://localhost:8000/v1/models
```

## Gotchas

- **FP8 KV cache broken** for Qwen3-Next hybrid Gated-DeltaNet (vLLM #26646).
  Must use bfloat16 — context budget is tighter than on Gemma / Llama.
- **`qwen3_coder` tool parser is unstable** — emits infinite special-char
  streams on long tool-call inputs. Default `qwen3_xml` is safe. For vanilla
  Qwen3-Instruct variants switch to `hermes` or `qwen25`.
- **Thinking-mode bleed** with reasoning on: `</think>` may not close when
  the assistant emits a tool call without text content, corrupting the next
  turn (Qwen3 #1831). Default config leaves `REASONING_PARSER` empty.
- **AWQ perf cliff**: older Qwen AWQ quants dropped 52 → 3 t/s at
  concurrency 25 on A100 (vLLM #20469). Benchmark your target concurrency
  and cap `MAX_NUM_SEQS` if throughput collapses.
- **First boot downloads ~46 GB** — use a persistent volume.
