#!/bin/bash
# Entrypoint for Qwen3-Embedding-8B on Strix Halo via llama.cpp Vulkan.
#
# Defaults target the Q8_0 GGUF (~8.7 GB) for highest practical quality.
# Qwen3 embedding model card specifies LAST-TOKEN pooling and a 32K native
# context. Llama-server flips into embedding mode with --embeddings and
# returns OpenAI-compatible /v1/embeddings.

set -e

MODEL_FILE="${MODEL_FILE:-/models/Qwen3-Embedding-8B-Q8_0.gguf}"

HF_REPO="${HF_REPO:-}"          # alternative: pull from HF, e.g. Qwen/Qwen3-Embedding-8B-GGUF
HF_FILE="${HF_FILE:-}"          # e.g. Qwen3-Embedding-8B-Q8_0.gguf

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
API_KEY="${API_KEY:-}"

# Embedding-specific knobs.
# - POOLING: last  (Qwen3 spec; do NOT change to mean/cls — wrong vectors)
# - CTX: 32K  (Qwen3-Embedding native context)
# - PARALLEL: 4 slots so concurrent embedders don't queue
POOLING="${POOLING:-last}"
CTX_TOTAL="${CTX_TOTAL:-32768}"
N_PARALLEL="${N_PARALLEL:-4}"

BATCH_SIZE="${BATCH_SIZE:-2048}"
UBATCH_SIZE="${UBATCH_SIZE:-512}"

N_GPU_LAYERS="${N_GPU_LAYERS:-99}"  # all layers on iGPU
NO_MMAP="${NO_MMAP:-true}"

# Prometheus /metrics
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

CMD="${CMD} --host ${HOST} --port ${PORT}"
CMD="${CMD} --n-gpu-layers ${N_GPU_LAYERS}"
CMD="${CMD} --embeddings"
CMD="${CMD} --pooling ${POOLING}"
CMD="${CMD} --parallel ${N_PARALLEL}"
CMD="${CMD} --ctx-size ${CTX_TOTAL}"
CMD="${CMD} --batch-size ${BATCH_SIZE}"
CMD="${CMD} --ubatch-size ${UBATCH_SIZE}"

[ "${NO_MMAP}" = "true" ] && CMD="${CMD} --no-mmap"
[ "${METRICS}" = "true" ] && CMD="${CMD} --metrics"
[ -n "${API_KEY}" ] && CMD="${CMD} --api-key ${API_KEY}"

# Append any caller-supplied flags
CMD="${CMD} $@"

echo "=============================================================="
echo "  qwen3-embedding-strix-llamacpp  (llama.cpp Vulkan / RADV / gfx1151)"
echo "=============================================================="
echo "Model file:       ${MODEL_FILE}"
[ -n "${HF_REPO}" ] && echo "HF source:        ${HF_REPO} / ${HF_FILE}"
echo "Pooling:          ${POOLING}"
echo "Context:          ${CTX_TOTAL}"
echo "Slots:            ${N_PARALLEL}"
echo "Batch / ubatch:   ${BATCH_SIZE} / ${UBATCH_SIZE}"
echo "Endpoint:         http://${HOST}:${PORT}/v1/embeddings"
echo "Metrics:          http://${HOST}:${PORT}/metrics"
echo "=============================================================="

exec bash -c "${CMD}"
