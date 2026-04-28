# Initial smoke test — gemma4-dense-vllm

First successful bring-up on A100-80GB after the FA2 head-size crash was
resolved by switching the Ampere/Ada default to `TRITON_ATTN`.

## Environment

| Field | Value |
|---|---|
| Date | 2026-04-21 |
| GPU | NVIDIA A100-80GB (Ampere, sm_80) |
| Host / endpoint | RunPod, `http://154.54.102.26:13161/v1` |
| Image | `gemma4-dense-vllm` on `vllm/vllm-openai:v0.19.1` |
| Model | `RedHatAI/gemma-4-31B-it-FP8-Dynamic` (~31 GB on GPU) |
| Attention backend | `TRITON_ATTN` (arch-conditional default) |
| Max context | 131072 |
| KV cache dtype | `auto` (bf16 on Ampere) |
| GPU memory util | 0.92 |
| Async scheduling | on |
| Prefix caching | on |
| Chunked prefill | on |
| Tool parser | `gemma4` |
| Reasoning parser | `gemma4` |

## Startup timing (cold HF cache)

| Phase | Duration |
|---|---|
| Weight download + load | ~596 s (~10 min) — dominant cost |
| `torch.compile` (dynamo + inductor) | ~49 s |
| Initial profiling / warmup | ~4 s |
| CUDA graph capture + KV alloc + uvicorn bind | ~remainder |

Total cold-boot before `:8000` accepts connections: roughly 12–15 minutes.
Subsequent boots reuse the HF cache and `torch_compile_cache` and should be
much faster.

## Tests

### 1. Plain chat completion — PASS

```bash
curl -sS http://154.54.102.26:13161/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "RedHatAI/gemma-4-31B-it-FP8-Dynamic",
    "messages": [{"role": "user", "content": "Write a single 5-7-5 haiku about Fedora Linux."}],
    "max_tokens": 100,
    "temperature": 0.7
  }'
```

Response content:

> Bleeding edge features,
> Stable base for daily work,
> Freedom in the code.

- HTTP 200, `finish_reason: "stop"`, `stop_reason: 106` (EOS)
- Tokens: 28 prompt / 19 completion / 47 total

### 2. Tool call — PASS (parallel tool calls)

```bash
curl -sS http://154.54.102.26:13161/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "RedHatAI/gemma-4-31B-it-FP8-Dynamic",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant with access to tools. Use them when appropriate."},
      {"role": "user", "content": "What is the weather in Berlin right now? Also, what about Tokyo?"}
    ],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_current_weather",
        "description": "Get the current weather for a given city.",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string", "description": "Name of the city, e.g. Berlin"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"], "description": "Temperature unit"}
          },
          "required": ["city"]
        }
      }
    }],
    "tool_choice": "auto",
    "max_tokens": 300,
    "temperature": 0.2
  }'
```

Response `tool_calls`:

```json
[
  {"function": {"name": "get_current_weather", "arguments": "{\"city\": \"Berlin\"}"}},
  {"function": {"name": "get_current_weather", "arguments": "{\"city\": \"Tokyo\"}"}}
]
```

- HTTP 200, `content: null`, `finish_reason: "tool_calls"`, `stop_reason: 50`
- Tokens: 150 prompt / 33 completion / 183 total
- Confirms the `gemma4` parser translates Gemma's custom (non-JSON)
  tool serialization into OpenAI-compatible `tool_calls`, and that
  parallel tool calls in a single turn work.

### 3. Reasoning / math — PASS, but parser did not fire

```bash
curl -sS http://154.54.102.26:13161/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "RedHatAI/gemma-4-31B-it-FP8-Dynamic",
    "messages": [{"role": "user", "content": "Think step by step. A train leaves Paris Gare de Lyon at 14:00, travels at 120 km/h toward Marseille (750 km). At what clock time does it arrive? Show your reasoning, then give the final answer."}],
    "max_tokens": 800,
    "temperature": 0.2
  }'
```

- Math: correct — 750 / 120 = 6.25 h = 6 h 15 min, 14:00 + 6:15 = **20:15**
- Well-formatted markdown with LaTeX math steps
- HTTP 200, `finish_reason: "stop"`, `stop_reason: 106` (EOS)
- Tokens: 68 prompt / 275 completion / 343 total
- **`reasoning: null`** — the model emitted reasoning inline in `content`
  rather than inside `<think>…</think>` tokens, so the `gemma4`
  reasoning parser had nothing to extract. Retry with
  `"chat_template_kwargs": {"enable_thinking": true}` (or equivalent
  Gemma 4 flag) to see if thinking-mode triggers tag emission.

## Open items / next probes

- Exercise the reasoning parser by enabling thinking mode (see above).
- Multimodal smoke test (image_url, video_url) — Gemma 4 supports both at
  this size but wasn't probed here.
- Long-context probe near 128K (prefix cache + chunked prefill behavior).
- Concurrency / throughput probe (`max_num_seqs=32`) under load.
- Dockerfile `HEALTHCHECK --start-period=300s` is shorter than the cold
  load time (~600 s). Bump to `900s` or pre-warm the HF cache volume to
  avoid false-unhealthy during boot on k8s / orchestrators that restart
  on unhealthy.
