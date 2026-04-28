#!/bin/bash
# Entrypoint for Gemma 4 31B dense on Strix Halo via llama.cpp Vulkan.
#
# Defaults derived from the MoE sibling, adjusted for the dense SKU:
#   - 60 layers vs 30 → KV-per-token roughly doubles
#   - 30.7 B all-dense weights → ~22 GB at Q5_K_M, ~19 GB at Q4_K_M
#   - decode rate is bandwidth-bound on ~256 GB/s shared bus → roughly half
#     the MoE rate at the same context depth
#   - per-session KV at 128K is ~8 GB (vs ~4 GB on the MoE), so the realistic
#     concurrent ceiling is roughly half: 2 sustained / 3 burst at 128 K
#
# Same hard rule as the MoE: do NOT pass --cache-reuse N. Gemma 4's interleaved
# sliding-window attention (architecture-level, not MoE-specific) breaks
# server-side prefix reuse (#21468 / #21831). Mitigate via client-side affinity.

set -e

# =============================================================================
# Defaults (override via env)
# =============================================================================

# Q5_K_M = 22 GB / strong quality default. Use Q4_K_M (~19 GB) if you need
# more KV headroom for 3 concurrent slots at 128 K.
MODEL_FILE="${MODEL_FILE:-/models/gemma-4-31B-it-Q5_K_M.gguf}"
MMPROJ_FILE="${MMPROJ_FILE:-}"

HF_REPO="${HF_REPO:-}"           # e.g. unsloth/gemma-4-31B-it-GGUF (verify naming on HF)
HF_FILE="${HF_FILE:-}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
API_KEY="${API_KEY:-}"

# Slot / context layout. Default 2 slots × 131072 ctx = full 128 K per session.
# To fit 3 concurrent slots at 128 K, drop to Q4_K_M weights and raise N_PARALLEL=3,
# CTX_TOTAL=393216. The decode rate is bandwidth-bound either way.
N_PARALLEL="${N_PARALLEL:-2}"
CTX_TOTAL="${CTX_TOTAL:-262144}"

BATCH_SIZE="${BATCH_SIZE:-2048}"
UBATCH_SIZE="${UBATCH_SIZE:-512}"

N_GPU_LAYERS="${N_GPU_LAYERS:-99}"
FLASH_ATTN="${FLASH_ATTN:-on}"

CACHE_TYPE_K="${CACHE_TYPE_K:-q8_0}"
CACHE_TYPE_V="${CACHE_TYPE_V:-q8_0}"

NO_MMAP="${NO_MMAP:-true}"
ENABLE_THINKING="${ENABLE_THINKING:-true}"
USE_JINJA="${USE_JINJA:-true}"

CACHE_RAM_MB="${CACHE_RAM_MB:-8192}"

TEMPERATURE="${TEMPERATURE:-1.0}"
TOP_P="${TOP_P:-0.95}"
TOP_K="${TOP_K:-64}"

METRICS="${METRICS:-true}"

# =============================================================================
# Build command
# =============================================================================

CMD="/app/llama-server"

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

CMD="${CMD} $@"

# =============================================================================
# Print configuration
# =============================================================================
echo "=============================================================="
echo "  gemma4-dense-strix-llamacpp  (llama.cpp Vulkan / RADV / gfx1151)"
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
