# gemma4-moe-strix-llamacpp

llama.cpp Vulkan (RADV) container for **`unsloth/gemma-4-26B-A4B-it-GGUF`** on
**AMD Strix Halo** (Ryzen AI Max+ 395, Radeon 8060S iGPU = RDNA 3.5 / gfx1151).

Built around the verdict in `../Hosting Gemma 4 on Strix Halo_ llama.cpp Vulkan
over vLLM-ROCm.pdf`. vLLM-ROCm is rejected on this hardware: SWA disabled in V1
on RDNA (#19367), HIP graph capture forces `--enforce-eager` (#32180), four
open Gemma 4 parser bugs (#38847 / #38855 / #38910 / #39392).

## Model specs (26B-A4B MoE)

| Property | Value |
|---|---|
| Total / active params | 25.2 B / ~3.8 B |
| Experts | 128 fine-grained, top-8 + 1 shared |
| Layers | 30 |
| Native context | 256 K (capped to 128 K here) |
| Modalities | text + image + video (mmproj optional) |
| Quant default | Unsloth UD-Q5_K_XL (~19 GB resident) |
| License | Apache 2.0 |

## Memory math (Strix Halo, 128 GB unified, ~96 GB iGPU pool)

| Item | Size |
|---|---|
| Co-resident services (embedding + Whisper + TTS + framework) | ~10–15 GB |
| Gemma 4 26B-A4B UD-Q5_K_XL weights | ~19 GB |
| KV cache @ 128 K × 3 slots, q8_0 (~4 GB/slot) | ~12 GB |
| Activations + RADV pipeline cache + headroom | ~6 GB |
| **Total Gemma footprint** | **~37 GB** of the 80 GB budget |

Realistic concurrent ceiling: **3 sessions sustained / 5 burst @ 128 K per
session**, or **5 sustained @ 64 K per session**. Gated by Gemma 4's SWA
prefix-cache regression (llama.cpp #21468 / #21831), not by the KV math.

## Throughput baseline

ollama #15601 (Apr 15 2026, llama.cpp b8765, Vulkan, 26B-A4B Q4_K_XL):

| Depth | Decode |
|---|---|
| 0 | 52.3 tok/s |
| 64 K | 35.1 tok/s |

Q5_K_XL is ~10–15 % slower than Q4_K_XL in exchange for materially better
quality on tool-use / reasoning. Tune to taste.

## Host setup (one-time)

```bash
# /etc/default/grub: GRUB_CMDLINE_LINUX_DEFAULT additions
ttm.pages_limit=25165824    # exposes ~96 GB to GTT
amdgpu.gttsize=98304         # legacy form (deprecated; ttm.pages_limit preferred)
amd_iommu=off                # +6 % MBW on Strix Halo (~234 vs 221 GB/s)

# BIOS:
#   UMA Frame Buffer Size = 512 MB     (do NOT set to 96 GB on Linux — GTT
#                                       is dynamic; large carve-out cripples OS)
#   cTDP / STAPM / fast-limit = 85 W   (community sweet spot for 24/7 inference)

# Versionlock to prevent the broken firmware update:
sudo dnf install python3-dnf-plugin-versionlock
sudo dnf versionlock add linux-firmware
# Specifically avoid linux-firmware-20251125 — breaks ROCm on Strix Halo
# (kyuz0 toolbox README, Mar 2026). RADV-only deployments are less affected
# but the hold is cheap insurance.

# Verify after reboot:
rocminfo | grep -E "Name:|Memory Pool"   # should show gfx1151 + GTT ~110 GiB
```

Uninstall AMDVLK if present (`sudo dnf remove amdvlk`) — it silently hijacks
Vulkan dispatch and halves prefill (hogeheer499 guide, Mar 2026).

## Build

```bash
podman build -t gemma4-moe-strix-llamacpp:latest \
    /home/ghost/Repositories/Scripts-and-Notebooks/llm_containers/gemma4-moe-strix-llamacpp
```

## Run

```bash
podman run --rm \
    --device /dev/dri \
    --device /dev/kfd \
    -p 8080:8080 \
    -v $HOME/models:/models:Z \
    -v gemma4-moe-radv-cache:/root/.cache/mesa_shader_cache \
    --name gemma4-moe \
    gemma4-moe-strix-llamacpp:latest
```

> `/dev/kfd` is only required if you also want ROCm tooling (rocm-smi /
> rocminfo) inside the container. The Vulkan inference path needs only
> `/dev/dri`.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `MODEL_FILE` | `/models/gemma-4-26B-A4B-it-UD-Q5_K_XL.gguf` | Local GGUF path |
| `HF_REPO` | (unset) | Alt: pull from HF (`unsloth/gemma-4-26B-A4B-it-GGUF`) |
| `HF_FILE` | (unset) | HF filename when using `HF_REPO` |
| `MMPROJ_FILE` | (unset) | Set for image/video input |
| `N_PARALLEL` | `3` | Parallel slots (3 × 128 K = "3 sustained" config) |
| `CTX_TOTAL` | `393216` | Total ctx; per-slot = total / N_PARALLEL = 131072 |
| `BATCH_SIZE` | `2048` | Prefill batch |
| `UBATCH_SIZE` | `512` | gfx1151 sweet spot |
| `CACHE_TYPE_K` | `q8_0` | K cache (halves footprint vs f16; needs `-fa on`) |
| `CACHE_TYPE_V` | `q8_0` | V cache |
| `FLASH_ATTN` | `on` | Mandatory on Strix Halo for q8_0 KV |
| `ENABLE_THINKING` | `true` | Fires `<\|channel\|>thought` for `gemma4` parser |
| `USE_JINJA` | `true` | Required for Gemma 4 chat template |
| `CACHE_RAM_MB` | `8192` | Host-RAM slot hot-swap region (#20574) |
| `TEMPERATURE` | `1.0` | Google's official Gemma 4 default |
| `TOP_P` | `0.95` | Google's official Gemma 4 default |
| `TOP_K` | `64` | Google's official Gemma 4 default |
| `METRICS` | `true` | Enable `/metrics` Prometheus endpoint |
| `API_KEY` | (unset) | Bearer auth |

## Alternate configs (override defaults)

**5 sessions at 64 K** (matches the PDF's compose snippet):
```bash
podman run --rm \
    --device /dev/dri \
    -p 8080:8080 \
    -v $HOME/models:/models:Z \
    -v gemma4-moe-radv-cache:/root/.cache/mesa_shader_cache \
    -e N_PARALLEL=5 \
    -e CTX_TOTAL=327680 \
    gemma4-moe-strix-llamacpp:latest
# 5 slots × 65536 ctx — "comfortably 5 at 64K"
```

**Single tenant, full 128 K, lowest TTFT:**
```bash
podman run --rm \
    --device /dev/dri \
    -p 8080:8080 \
    -v $HOME/models:/models:Z \
    -v gemma4-moe-radv-cache:/root/.cache/mesa_shader_cache \
    -e N_PARALLEL=1 \
    -e CTX_TOTAL=131072 \
    gemma4-moe-strix-llamacpp:latest
```

## Verify in production (curl)

After the container is healthy (`curl localhost:8080/health`), run all five:

```bash
# 1. Slots populated, model loaded
curl -s http://localhost:8080/slots | jq '.[].id, .[].state'
# Expect: 4 slots in state "idle"

# 2. Thinking mode actually fires — look for <|channel|>thought
curl -sN http://localhost:8080/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"gemma-4-26B-A4B-it","stream":false,
         "chat_template_kwargs":{"enable_thinking":true},
         "messages":[{"role":"user","content":"Two trains 60mph apart by 180mi, when meet?"}]}' \
    | tee /tmp/think.json | jq -r '.choices[0].message.content' | head -c 400
grep -c 'channel' /tmp/think.json   # > 0 means wired

# 3. Parallel tool calls
curl -s http://localhost:8080/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"gemma-4-26B-A4B-it","tool_choice":"required",
         "tools":[
           {"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}},
           {"type":"function","function":{"name":"get_time","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}}
         ],
         "messages":[{"role":"user","content":"Weather in Tokyo and current time in Berlin?"}]}' \
    | jq '.choices[0].message.tool_calls | length, .[].function.name'
# Expect: 2, "get_weather", "get_time"
# CHECK: tool_calls[].function.arguments — STRING (correct) or OBJECT (#20198 regression)?

# 4. 128K needle-in-haystack
python3 -c '
import json, random; random.seed(7)
filler = ("The capital of an obscure planet is " + "the moon. ") * 30000
needle = "The secret passphrase is HELIOTROPE-7741."
prompt = filler + needle + " " + filler[:40000]
print(json.dumps({"model":"gemma-4-26B-A4B-it","stream":False,
    "messages":[{"role":"user","content": prompt + "\n\nWhat is the secret passphrase?"}],
    "max_tokens":40}))' \
| curl -s http://localhost:8080/v1/chat/completions \
    -H 'Content-Type: application/json' -d @- \
| jq -r '.choices[0].message.content'
# Expect: HELIOTROPE-7741

# 5. 5 concurrent sessions
seq 1 5 | xargs -n1 -P5 -I{} curl -sN http://localhost:8080/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"gemma-4-26B-A4B-it","stream":true,"max_tokens":512,
         "messages":[{"role":"user","content":"Write a 500-word essay on slot {}"}]}' &
sleep 1
for i in 1 2 3; do
    curl -s http://localhost:8080/metrics | grep -E 'llamacpp:(prompt_tokens_total|tokens_predicted_total|n_busy_slots)'
    amd-smi metric -P --json | jq '.[].power_socket_watts, .[].temperature.junction_c'
    sleep 5
done; wait
# Expect: n_busy_slots oscillates 4-5, aggregate tg ≥ 25 tok/s
```

## Watch out for

- **Gemma 4 SWA + prefix cache is broken upstream** (llama.cpp #21468 / #21831).
  Server-side `--cache-reuse N` triggers full re-prefill on cache misses → 60-90 s
  TTFT on long system prompts. Mitigate client-side: route same-prefix requests
  to the same slot via `/slots`. Do NOT enable `--cache-reuse`.
- **AMDVLK halves prefill** if installed alongside RADV. The Dockerfile pins
  `AMD_VULKAN_ICD=RADV`, but uninstall AMDVLK on the host too.
- **`linux-firmware-20251125` breaks Strix Halo** — version-lock the package.
- **Kernel ≥ 6.18.4** required; older kernels have driver instability under
  sustained inference (kyuz0 toolbox README).
- **First-boot pipeline compile** can take 1-2 minutes (Triton-equivalent on
  RADV). Persist `/root/.cache/mesa_shader_cache` to a named volume to keep
  warm reboots near 30 s.
- **Thermal**: 85 W cTDP is the documented sweet spot for 24/7 inference on
  desktop SKUs (Framework Desktop, GMKtec EVO-X2, Beelink GTR9 Pro). HP ZBook
  Ultra G1a is thermally tighter (60-70 W).

## Choosing between MoE vs dense on Strix Halo

Pick **this container (26B-A4B MoE)** when: you want **2× decode throughput**
and **half the per-session KV** vs the 31B dense, you're running 3-5
concurrent agents, and your workload is mostly routine tool calls + chat.
Tokens/sec at 64 K depth: ~35 tok/s.

Pick **`gemma4-dense-strix-llamacpp`** (31B dense) when: your agents need the
hardest reasoning Gemma 4 can offer and you can accept ~17 tok/s decode and
roughly half the concurrent-session ceiling.
