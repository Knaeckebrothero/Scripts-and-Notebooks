#!/bin/bash
# Entrypoint for Gemma 4 26B-A4B (MoE) on Strix Halo via llama.cpp Vulkan.
#
# Default config matches the launch config in "Hosting Gemma 4 on Strix Halo:
# pick llama.cpp/Vulkan, dodge vLLM's RDNA 3.5 minefield" (Apr 2026):
#   - unsloth/gemma-4-26B-A4B-it-GGUF @ UD-Q5_K_XL (~19 GB weights)
#   - 4 parallel slots × 64K per slot (262144 total ctx)
#   - q8_0 K + q8_0 V cache (halves KV; requires -fa on)
#   - Flash attention on (Vulkan scalar FA, post PR #19625)
#   - --jinja for the bundled Gemma 4 chat template
#   - enable_thinking=true to fire the <|channel|>thought ... <|channel|>final
#     control-token sequence the gemma4 reasoning_parser was built for
#
# Hard rule: do NOT pass --cache-reuse N. Gemma 4's interleaved sliding-window
# attention breaks server-side prefix reuse (llama.cpp #21468 / #21831): the
# server logs "cache reuse is not supported" and falls back to full re-prefill
# on cache misses — which costs 60-90s TTFT on Claude-Code-style 46K-token
# system prompts. Mitigation: client-side affinity routing (pin same-prefix
# requests to the same slot via /slots id), per-slot cache stays warm.

set -e

# =============================================================================
# Defaults (override via env)
# =============================================================================

MODEL_FILE="${MODEL_FILE:-/models/gemma-4-26B-A4B-it-UD-Q5_K_XL.gguf}"
MMPROJ_FILE="${MMPROJ_FILE:-}"   # set to /models/mmproj-*.gguf for multimodal

HF_REPO="${HF_REPO:-}"           # alternative: pull from HF, e.g. unsloth/gemma-4-26B-A4B-it-GGUF
HF_FILE="${HF_FILE:-}"           # e.g. gemma-4-26B-A4B-it-UD-Q5_K_XL.gguf

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
API_KEY="${API_KEY:-}"

# Slot / context layout. Total ctx = N_PARALLEL * (per-slot ctx).
# Default 3 slots × 131072 ctx → full 128K per session, matches the PDF's
# "3 sustained / 5 burst at 128K" verdict.
# Alt configs:
#   - 4 slots × 64K  : N_PARALLEL=4 CTX_TOTAL=262144 (5 sessions at 64K)
#   - 5 slots × 64K  : N_PARALLEL=5 CTX_TOTAL=327680
#   - 2 slots × 128K : N_PARALLEL=2 CTX_TOTAL=262144 (looser KV budget)
N_PARALLEL="${N_PARALLEL:-3}"
CTX_TOTAL="${CTX_TOTAL:-393216}"

BATCH_SIZE="${BATCH_SIZE:-2048}"
UBATCH_SIZE="${UBATCH_SIZE:-512}"   # gfx1151 sweet spot per ollama #15601 / lhl benchmarks

N_GPU_LAYERS="${N_GPU_LAYERS:-99}"  # all layers on iGPU; unified memory makes this cheap
FLASH_ATTN="${FLASH_ATTN:-on}"      # mandatory on Strix Halo for q8_0 KV path; tri-state on|off|auto in b6653+

CACHE_TYPE_K="${CACHE_TYPE_K:-q8_0}"
CACHE_TYPE_V="${CACHE_TYPE_V:-q8_0}"

NO_MMAP="${NO_MMAP:-true}"          # avoid mmap; --no-mmap is the kyuz0 toolbox default
ENABLE_THINKING="${ENABLE_THINKING:-true}"
USE_JINJA="${USE_JINJA:-true}"

# Host-RAM hot-swap region for slots that overflow the 80 GB Gemma budget.
# 8 GB is conservative; raise to 16384 if you observe slot evictions.
CACHE_RAM_MB="${CACHE_RAM_MB:-8192}"

# Gemma 4 sampler defaults (Google's official recommendation).
TEMPERATURE="${TEMPERATURE:-1.0}"
TOP_P="${TOP_P:-0.95}"
TOP_K="${TOP_K:-64}"

# Prometheus /metrics
METRICS="${METRICS:-true}"

# =============================================================================
# Build command
# =============================================================================

CMD="/app/llama-server"

# Model source — prefer local file, fall back to HF download
if [ -n "${HF_REPO}" ]; then
    CMD="${CMD} --hf-repo ${HF_REPO}"
    [ -n "${HF_FILE}" ] && CMD="${CMD} --hf-file ${HF_FILE}"
else
    CMD="${CMD} --model ${MODEL_FILE}"
fi

[ -n "${MMPROJ_FILE}" ] && CMD="${CMD} --mmproj ${MMPROJ_FILE}"

CMD="${CMD} --host ${HOST} --port ${PORT}"
CMD="${CMD} --n-gpu-layers ${N_GPU_LAYERS}"
CMD="${CMD} --parallel ${N_PARALLEL}"
CMD="${CMD} --ctx-size ${CTX_TOTAL}"
CMD="${CMD} --batch-size ${BATCH_SIZE}"
CMD="${CMD} --ubatch-size ${UBATCH_SIZE}"
CMD="${CMD} --flash-attn ${FLASH_ATTN}"
CMD="${CMD} --cache-type-k ${CACHE_TYPE_K}"
CMD="${CMD} --cache-type-v ${CACHE_TYPE_V}"
CMD="${CMD} --cache-prompt"
CMD="${CMD} --cache-ram ${CACHE_RAM_MB}"

[ "${NO_MMAP}" = "true" ] && CMD="${CMD} --no-mmap"
[ "${USE_JINJA}" = "true" ] && CMD="${CMD} --jinja"

if [ "${ENABLE_THINKING}" = "true" ]; then
    CMD="${CMD} --chat-template-kwargs '{\"enable_thinking\": true}'"
fi

CMD="${CMD} --temp ${TEMPERATURE} --top-p ${TOP_P} --top-k ${TOP_K}"

[ "${METRICS}" = "true" ] && CMD="${CMD} --metrics"
[ -n "${API_KEY}" ] && CMD="${CMD} --api-key ${API_KEY}"

# Append any caller-supplied flags
CMD="${CMD} $@"

# =============================================================================
# Print configuration
# =============================================================================
echo "=============================================================="
echo "  gemma4-moe-strix-llamacpp  (llama.cpp Vulkan / RADV / gfx1151)"
echo "=============================================================="
echo "Model file:       ${MODEL_FILE}"
[ -n "${HF_REPO}" ] && echo "HF source:        ${HF_REPO} / ${HF_FILE}"
[ -n "${MMPROJ_FILE}" ] && echo "MMProj:           ${MMPROJ_FILE}"
echo "Slots × ctx:      ${N_PARALLEL} × $((CTX_TOTAL / N_PARALLEL))  (total ${CTX_TOTAL})"
echo "Batch / ubatch:   ${BATCH_SIZE} / ${UBATCH_SIZE}"
echo "KV cache:         K=${CACHE_TYPE_K}, V=${CACHE_TYPE_V}"
echo "Flash attn:       ${FLASH_ATTN}"
echo "Thinking mode:    ${ENABLE_THINKING}"
echo "RAM hot-swap:     ${CACHE_RAM_MB} MB"
echo "Endpoint:         http://${HOST}:${PORT}/v1"
echo "Metrics:          http://${HOST}:${PORT}/metrics"
echo "=============================================================="
echo "REMINDER: client-side affinity routing required for system-prompt"
echo "          cache reuse on Gemma 4 SWA (#21468 / #21831). Do NOT"
echo "          pass --cache-reuse N — server-side prefix reuse is broken."
echo "=============================================================="

exec bash -c "${CMD}"
