# RunPod A100-80GB test — FP8 @ 128K, up to 2 streams

Goal: validate `gemma4-dense-vllm` on a rented **A100-80GB** at the production
target for autonomous agents — **FP8-Dynamic weights, 128K context, 1–2
concurrent streams, full quality** (no INT4, no fp8 KV).

> **Results (2026-06-28) — passed, and capacity beat the estimate.** The KV
> pool actually holds **~355K tokens**, so **two** full ~124K streams run
> concurrently at **~70% KV usage, 0 preemptions**, with correct needle recall.
> `MAX_NUM_SEQS` now defaults to **2**. The old "2× 128K ≈ 85 GB > ~45 GB pool"
> reasoning assumed vLLM's legacy 16-KV-head global-layer over-allocation, which
> v0.22's hybrid manager dropped (~2.7× less per-seq KV). **Caveat:** fitting 2
> is not 2× throughput — a single 124K prefill already saturates compute
> (~628 tok/s ⇒ ~3.3 min/prefill on the Ampere FP8-emulated path), so a 2nd
> concurrent stream halves each request's speed. It is burst headroom, not
> parallelism. For >2 concurrent 128K streams, scale horizontally (more pods) or
> onto bigger hardware (2× A100 TP=2, H200-141GB).

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
| `LIMIT_MM_PER_PROMPT` | `image=5,audio=0` | default; up to 5 images/request — this sizes the vision-encoder reservation, which shrinks the KV pool, so **Step 0's metric must still read ≥ 1.0x** (if not, lower the image cap or trim context). `audio=0` is mandatory: the 31B has no audio tower (`config.json` `audio_config: null`) |
| `MAX_NUM_SEQS` | `2` | default; the measured 128K KV ceiling — admits 2, queues the rest (no preemption thrash) |
| `ENABLE_THINKING` | `false` `(override; default true)` | clean content for the baseline; flip to `true` for the Step 3 reasoning probe |

> **Fixed in this build:** the entrypoint previously crash-looped on two args
> that vLLM parses as JSON — `--cudagraph-capture-sizes` (now emitted as
> `--compilation-config '{"cudagraph_capture_sizes":[…]}'`) and
> `--limit-mm-per-prompt` (the friendly `image=5,audio=0` form is now converted
> to `{"image":5,"audio":0}`, space-tolerant). If an older image loops on either,
> re-pull `:latest` (or pin the latest `sha-`).

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
