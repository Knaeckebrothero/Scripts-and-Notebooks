# RunPod A100-80GB test — FP8 @ 128K, single stream

Goal: validate `gemma4-dense-vllm` on a rented **A100-80GB** at the production
target for autonomous agents — **FP8-Dynamic weights, 128K context, 1
concurrent stream, full quality** (no INT4, no fp8 KV).

This is the deliberate tradeoff: 1 guaranteed 128K stream at FP8 quality. More
than one concurrent 128K stream does **not** fit a single A100-80GB at BF16 KV
(2× 128K ≈ 85 GB KV vs ~45 GB pool) — scale that horizontally (more pods) or on
bigger hardware (H200-141GB), not on this GPU.

## Why this differs from the 2026-04 smoke test

`initial_test.md` passed, but only on tiny prompts and at `0.92` util. At `0.92`
the KV pool (~42.6 GB) is essentially equal to one full 128K sequence (~42.7 GB)
→ vLLM reports ~0.998x max concurrency, i.e. a real 128K-filling agent does
**not** fit. The image now ships **`0.95` as the default** (~45 GB pool → ~1.05x),
which the official vLLM Gemma 4 recipe sanctions ("use 0.90 to 0.95 to maximize KV
cache capacity"). This run also, crucially, **probes real long context**.

Real-world confirmation of the failure mode: vLLM issue #39133 is a user at
`max_model_len=131072` getting a KV pool of only ~25K tokens (~0.19x of one 128K
sequence) and asking why — exactly the "<1.0x" trap Step 0 guards against. (Their
hardware was 2×24 GB INT4, far smaller than this A100-80GB FP8, so your number
will be far healthier — but the check is identical.)

## Launch

- **Image:** `ghcr.io/knaeckebrothero/gemma4-dense-vllm:latest`
  (public, current — built from v0.22.0, CI `fab4043` 2026-06-04)
- **GPU:** 1× A100-80GB (SXM or PCIe)
- **Container disk:** 40 GB (the vLLM base image is large; 20 GB is tight)
- **Volume:** 80 GB at `/mnt/cache`, **fresh** (do not reuse a volume from the
  v0.19.x test — stale `torch.compile` graphs cause silent kernel errors)
- **Ports:** `8000` TCP (required), `22` TCP (optional, SSH)

### Environment

These match the shipped image **defaults** except the two marked `(override)`,
which are test-only choices — so a stock launch already does the right thing:

| Var | Value | Why |
|---|---|---|
| `MODEL` | `RedHatAI/gemma-4-31B-it-FP8-Dynamic` | default; FP8 quality |
| `MAX_MODEL_LEN` | `131072` | the 128K requirement |
| `KV_CACHE_DTYPE` | `auto` | forced BF16 — fp8 KV blocked on FP8 weights (#40388) |
| `GPU_MEMORY_UTILIZATION` | `0.95` | default; makes one 128K seq fit (~1.05x), recipe-sanctioned for FP8. Do **not** exceed 0.95 with live multimodal traffic — vision/video activations can spike past the profiled peak and OOM |
| `LIMIT_MM_PER_PROMPT` | `image=2,audio=0` | agents **are** multimodal — keep vision. Set `image=N` to your real per-request max (it sizes the encoder reservation, which shrinks the KV pool — so confirm Step 0 is still ≥ 1.0x). ⚠️ `audio=0`: the official recipe shows `audio:1` for the 31B — verify the 31B accepts audio before relying on this |
| `MAX_NUM_SEQS` | `4` `(override; default 16)` | admission cap for the test; keeps preemption tame if short requests pile up |
| `ENABLE_THINKING` | `false` `(override; default true)` | clean content for the baseline; flip to `true` for the Step 3 reasoning probe |

First cold boot is ~12–15 min (~31 GB download + compile). The 1200s
HEALTHCHECK start-period covers it — don't kill it early.

## Step 0 — the go/no-go (read the log, send no traffic yet)

In the startup log, find:
```
Maximum concurrency for 131072 tokens per request: <N>x
```
- **≥ 1.0x** → 128K genuinely fits. Proceed.
- **< 1.0x** → bump `GPU_MEMORY_UTILIZATION=0.96`, or drop `MAX_MODEL_LEN` to
  `126976`, and reboot. Do not proceed until this is ≥ 1.0x.

Also note the logged `GPU KV cache size: <N> tokens` — divide by 131072 for the
same number.

## Step 1 — basics (reuse initial_test.md)

Plain chat + parallel tool-call from `initial_test.md` should still PASS. Quick
sanity:
```bash
curl -sS http://<pod>:<port>/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"RedHatAI/gemma-4-31B-it-FP8-Dynamic",
       "messages":[{"role":"user","content":"Reply with exactly: OK"}],
       "max_tokens":8}'
```

## Step 2 — the real test: a genuine long-context request

This is what was never probed. Build a ~100K-token prompt and confirm it
prefills and generates without preemption or OOM.

```python
from openai import OpenAI
client = OpenAI(base_url="http://<pod>:<port>/v1", api_key="dummy")

# ~100K tokens of filler + a needle near the top to verify recall over distance
needle = "The access code for vault 7 is CERULEAN-42."
filler = ("In the distributed system, each node maintains a local log. " * 12000)
prompt = f"{needle}\n\n{filler}\n\nQuestion: What is the access code for vault 7?"

r = client.chat.completions.create(
    model="RedHatAI/gemma-4-31B-it-FP8-Dynamic",
    messages=[{"role": "user", "content": prompt}],
    max_tokens=64, temperature=0)
print(r.usage.prompt_tokens, "prompt tokens")   # expect ~100K
print(r.choices[0].message.content)              # expect "CERULEAN-42"
```

PASS = prompt_tokens near 100K, correct needle recall, `finish_reason: stop`,
no OOM in logs. Then push the filler toward ~125K tokens to exercise the top of
the window.

## Step 3 — reasoning (thinking on, NON-streaming)

Set `ENABLE_THINKING=true` (or pass per-request) and **send
`skip_special_tokens: false`** so the `<|channel|>` delimiters reach the gemma4
parser (#38855). Expect a populated `reasoning` field.

```bash
curl -sS http://<pod>:<port>/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"RedHatAI/gemma-4-31B-it-FP8-Dynamic",
       "messages":[{"role":"user","content":"A train leaves at 14:00 going 120 km/h for 750 km. Arrival time?"}],
       "skip_special_tokens": false, "max_tokens": 600, "temperature": 0.2}'
```

> Streaming + thinking is a known-degraded upstream mode (#38855): the gemma4
> parser's streaming path matches decoded text, not token ids, so reasoning may
> leak into `content`. Keep agent reasoning probes non-streaming.

## What to watch

- `vllm:num_preemptions_total` — should stay **0** for a single long stream. Any
  growth means the pool is over-subscribed (lower `MAX_NUM_SEQS`, raise util, or
  the concurrency metric was <1.0x).
- GPU mem via `nvidia-smi` — should sit near 95% without OOM.

## If you later need >1 concurrent at 128K

Not possible on one A100-80GB at FP8/BF16-KV. Options, cheapest first:
1. Horizontal: N pods behind your router, 1 stream each (keeps FP8 + 128K).
2. 2× A100 (TP=2): 2–3 concurrent @ 128K, FP8 quality.
3. H200-141GB: real concurrency at 128K, or single-stream 256K.

## References

- Official vLLM Gemma 4 recipe (util 0.90–0.95, multimodal flags):
  <https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html>
- vLLM #39133 — real-world 128K KV-pool-too-small report (the Step 0 trap):
  <https://github.com/vllm-project/vllm/issues/39133>
- vLLM #41403 — TurboQuant + Gemma 4 multimodal tracking. The title flags a
  "5-gate blocker stack" — the multimodal TurboQuant path is **not** ready, so
  don't plan the 256K goal around it yet.
