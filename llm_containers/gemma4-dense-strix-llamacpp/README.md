# gemma4-dense-strix-llamacpp

llama.cpp Vulkan (RADV) container for **`google/gemma-4-31B-it`** on
**AMD Strix Halo** (Ryzen AI Max+ 395, Radeon 8060S iGPU = RDNA 3.5 / gfx1151).

Sibling of `../gemma4-moe-strix-llamacpp`. Same backend, same caveats — pick
this image when reasoning depth matters more than throughput, the MoE for ~2×
decode and double the per-session concurrency.

## Model specs (31B dense)

| Property | Value |
|---|---|
| Total params | 30.7 B |
| Active per token | 30.7 B (no sparsity) |
| Layers | 60 |
| Native context | 256 K (capped to 128 K here) |
| Modalities | text + image + video (mmproj optional) |
| Quant default | Q5_K_M (~22 GB resident) |
| License | Apache 2.0 |

## Memory math (Strix Halo, 80 GB Gemma budget)

| Quant | Weights | KV @ 128 K (q8_0) | KV @ 64 K | 128 K-slot ceiling |
|---|---|---|---|---|
| Q4_K_M | ~19 GB | ~8 GB / slot | ~4 GB / slot | ~3 sessions |
| **Q5_K_M (default)** | **~22 GB** | **~8 GB / slot** | **~4 GB / slot** | **~2 sessions** |
| Q6_K | ~25 GB | ~8 GB / slot | ~4 GB / slot | ~2 sessions |
| Q8_0 | ~33 GB | ~8 GB / slot | ~4 GB / slot | ~2 sessions |

Per-token KV is roughly **2× the MoE** because num_layers doubles (60 vs 30).
At Q5_K_M / 128 K / q8_0 KV the realistic ceiling is **2 sustained / 3 burst**;
swap to Q4_K_M to fit a third sustained slot at the cost of measurable
quality on hard reasoning.

## Throughput baseline (extrapolated)

The PDF reports the 26B-A4B MoE at 52.3 / 35.1 tok/s (depth 0 / 64 K) and
notes "the 31B dense would be roughly half that decode rate at the same
depth" — call it ~26 / ~17 tok/s. **This is an extrapolation, not a measurement
on Strix Halo.** Run `llama-bench` on your unit to get the real number before
sizing capacity.

## Host setup

Identical to the MoE sibling — see
[`../gemma4-moe-strix-llamacpp/README.md`](../gemma4-moe-strix-llamacpp/README.md#host-setup-one-time)
for the GRUB / BIOS / firmware checklist. Key items:

- `ttm.pages_limit=25165824` (~96 GB GTT) and `amd_iommu=off` in GRUB
- BIOS UMA Frame Buffer Size = **512 MB** (NOT 96 GB on Linux)
- `dnf versionlock add linux-firmware` to avoid the 20251125 regression
- Uninstall AMDVLK if present
- Kernel ≥ 6.18.4

## Build

```bash
podman build -t gemma4-dense-strix-llamacpp:latest \
    /home/ghost/Repositories/Scripts-and-Notebooks/llm_containers/gemma4-dense-strix-llamacpp
```

## Run

```bash
podman run --rm \
    --device /dev/dri \
    --device /dev/kfd \
    -p 8080:8080 \
    -v $HOME/models:/models:Z \
    -v gemma4-dense-radv-cache:/root/.cache/mesa_shader_cache \
    --name gemma4-dense \
    gemma4-dense-strix-llamacpp:latest
```

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `MODEL_FILE` | `/models/gemma-4-31B-it-Q5_K_M.gguf` | Local GGUF path |
| `HF_REPO` | (unset) | Alt: `unsloth/gemma-4-31B-it-GGUF` (verify naming on HF) |
| `HF_FILE` | (unset) | HF filename |
| `MMPROJ_FILE` | (unset) | Set for image/video input |
| `N_PARALLEL` | `2` | 2 slots × 131 K = full 128 K per session |
| `CTX_TOTAL` | `262144` | Total ctx; per-slot = total / N_PARALLEL |
| `BATCH_SIZE` | `2048` | Prefill batch |
| `UBATCH_SIZE` | `512` | gfx1151 sweet spot |
| `CACHE_TYPE_K` | `q8_0` | Halves K cache vs f16 |
| `CACHE_TYPE_V` | `q8_0` | Halves V cache vs f16 |
| `FLASH_ATTN` | `on` | Mandatory on Strix Halo for q8_0 KV |
| `ENABLE_THINKING` | `true` | Fires `<\|channel\|>thought` |
| `USE_JINJA` | `true` | Required for Gemma 4 chat template |
| `CACHE_RAM_MB` | `8192` | Host-RAM slot hot-swap region |
| `TEMPERATURE` | `1.0` | Google's official Gemma 4 default |
| `TOP_P` | `0.95` | Google's official Gemma 4 default |
| `TOP_K` | `64` | Google's official Gemma 4 default |
| `METRICS` | `true` | Enable `/metrics` |
| `API_KEY` | (unset) | Bearer auth |

## 3-session 128 K config (override defaults)

```bash
podman run --rm \
    --device /dev/dri \
    -p 8080:8080 \
    -v $HOME/models:/models:Z \
    -v gemma4-dense-radv-cache:/root/.cache/mesa_shader_cache \
    -e MODEL_FILE=/models/gemma-4-31B-it-Q4_K_M.gguf \
    -e N_PARALLEL=3 \
    -e CTX_TOTAL=393216 \
    gemma4-dense-strix-llamacpp:latest
# 3 slots × 131072 ctx, Q4_K_M weights — measurable quality drop vs Q5_K_M
```

## Verify in production

The same five-curl checklist as the MoE container — see
[`../gemma4-moe-strix-llamacpp/README.md#verify-in-production-curl`](../gemma4-moe-strix-llamacpp/README.md#verify-in-production-curl).
Substitute model id `gemma-4-31B-it`. Adjust the concurrency probe to 3
sessions (not 5) to match this image's realistic ceiling.

## Watch out for

All gotchas from the MoE sibling apply (SWA prefix-cache regression, AMDVLK,
linux-firmware-20251125, kernel ≥ 6.18.4, RADV pipeline cold-cache compile).
**Plus**:

- **Decode is twice as bandwidth-bound** — first agent will feel slower;
  three concurrent agents will feel slow. The decision criterion is whether
  the quality delta vs the MoE is worth it for your specific workload.
- **The `~17 tok/s @ 64 K` figure is extrapolated**, not measured. Validate
  with `llama-bench --model /models/gemma-4-31B-it-Q5_K_M.gguf -p 64000 -n 128`
  on the actual unit before committing to capacity numbers.
- **PLE / KV-shared layers don't apply here** — the dense SKU has no
  Per-Layer Embeddings (those are E2B/E4B-only) and no shared-KV layers, so
  the KV math is the straight `num_layers × 2 × num_kv_heads × head_dim ×
  dtype_bytes` formula. Pull `num_key_value_heads` and `head_dim` from
  `config.json` for the SKU you actually deploy.

## Choosing between MoE vs dense on Strix Halo

Pick **this container (31B dense)** when: agents are doing complex multi-step
reasoning, hard math, or precise tool-arg construction where the published
~5-15 % MoE quality penalty would hurt. Expect ~17 tok/s decode at 64 K and
2-3 sustained concurrent agents.

Pick **`gemma4-moe-strix-llamacpp`** (26B-A4B MoE) when: throughput +
concurrency + long context dominate, agents are mostly routine tool calls,
or the workload tolerates retries on edge cases. Expect ~35 tok/s and 3-5
concurrent agents.
