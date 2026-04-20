#!/bin/bash
# Optimized entrypoint for Gemma 4 31B dense
# Supports: A100-80GB, H100-80GB, H200, L40S-48GB, B200
#
# Model memory (DEFAULT: FP8 weights = ~33 GB, fp8_e5m2 KV):
#   - 32K ctx:  ~1.5 GB KV -> trivial on L40S / A100
#   - 64K ctx:  ~3 GB KV   -> comfortable on L40S / A100
#   - 128K ctx: ~6 GB KV   -> default; comfortable on A100, fits L40S
#   - 256K ctx: ~10 GB KV  -> A100-80GB comfortable; overflows L40S-48GB
#
# To run full-precision BF16 (~61 GB weights, A100-80GB+ only):
#   MODEL=google/gemma-4-31B-it MAX_MODEL_LEN=65536
#
# Attention sinks: NO (standard GQA) — FA2 on Ampere, FA3 on Hopper.
# Interleaved 5:1 local-SWA + global attention handled by vLLM Hybrid KV Cache Manager.
# FLASHINFER backend is NOT compatible (vLLM issue #20865).

set -e

# =============================================================================
# SSH Server (optional, RunPod tunneling)
# =============================================================================
SSH_KEY="${PUBLIC_KEY:-${SSH_PUBLIC_KEY:-}}"
if [ -n "${SSH_KEY}" ]; then
    mkdir -p /root/.ssh
    echo "${SSH_KEY}" > /root/.ssh/authorized_keys
    chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys
    /usr/sbin/sshd
    echo "SSH server started on port 22 (key-based auth)"
elif [ -n "${SSH_PASSWORD}" ]; then
    echo "root:${SSH_PASSWORD}" | chpasswd
    /usr/sbin/sshd
    echo "SSH server started on port 22 (password auth)"
fi

# =============================================================================
# GPU Detection
# =============================================================================
detect_gpu_arch() {
    if command -v nvidia-smi &> /dev/null; then
        local gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)
        case "$gpu_name" in
            *A100*|*A10*|*A30*|*A40*)      echo "ampere" ;;
            *L40*|*L4*|*RTX*40*|*RTX*Ada*) echo "ada" ;;
            *H100*|*H200*|*H800*)          echo "hopper" ;;
            *B100*|*B200*)                 echo "blackwell" ;;
            *)                             echo "unknown" ;;
        esac
    else
        echo "unknown"
    fi
}

GPU_ARCH=$(detect_gpu_arch)
echo "Detected GPU architecture: ${GPU_ARCH}"

# Detect total VRAM (GB) to sanity-check the model choice
detect_gpu_vram_gb() {
    if command -v nvidia-smi &> /dev/null; then
        nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | awk '{printf "%d", $1/1024}'
    else
        echo "0"
    fi
}

GPU_VRAM_GB=$(detect_gpu_vram_gb)
echo "Detected GPU VRAM: ${GPU_VRAM_GB} GB"

# Gemma 4 uses standard GQA + interleaved SWA — FA2 handles it cleanly on all
# supported GPUs. Do NOT use FLASHINFER (breaks interleaved SWA, vLLM #20865).
ATTENTION_BACKEND="${VLLM_ATTENTION_BACKEND_OVERRIDE:-FLASH_ATTN}"
echo "Attention backend: ${ATTENTION_BACKEND}"

# =============================================================================
# VRAM sanity check
# =============================================================================
# Default FP8 model (~33 GB weights) at default 128K context (~6 GB FP8 KV)
# needs ~44 GB minimum (weights + KV + CUDA overhead). On <44 GB GPUs the user
# must reduce context, switch to a smaller quant, or use the MoE container.
#
# If MODEL has been overridden to BF16 (google/gemma-4-31B-it, ~61 GB weights),
# override MIN_VRAM_GB to ~70 manually — we can't reliably detect that here.
MIN_VRAM_GB="${MIN_VRAM_GB:-44}"

if [ "${GPU_VRAM_GB}" -gt 0 ] && [ "${GPU_VRAM_GB}" -lt "${MIN_VRAM_GB}" ] \
   && [ "${SKIP_VRAM_CHECK}" != "true" ]; then
    echo ""
    echo "================================================================"
    echo "  WARNING: ${GPU_VRAM_GB} GB VRAM detected — need ${MIN_VRAM_GB}+ for defaults"
    echo "================================================================"
    echo "  Default MODEL='${MODEL}' (FP8, ~33 GB weights)"
    echo "  at MAX_MODEL_LEN=${MAX_MODEL_LEN} will OOM on ${GPU_VRAM_GB} GB."
    echo ""
    echo "  Options:"
    echo "    1) Reduce context:      -e MAX_MODEL_LEN=32768  (fits 40+ GB GPUs)"
    echo "    2) Smaller quant:       -e MODEL=QuantTrio/gemma-4-31B-it-AWQ (~20 GB)"
    echo "    3) Even smaller:        -e MODEL=RedHatAI/gemma-4-31B-it-NVFP4 (~17 GB, Hopper+)"
    echo "    4) Switch to MoE:       use the gemma4-moe-vllm container"
    echo ""
    echo "  On Ada (L40S/4090) also set: -e KV_CACHE_DTYPE=fp8_e4m3"
    echo "  If running BF16 (MODEL=google/gemma-4-31B-it), need 70+ GB VRAM."
    echo ""
    echo "  Set SKIP_VRAM_CHECK=true to silence this warning."
    echo "  Continuing in 5s — Ctrl+C to abort..."
    echo "================================================================"
    echo ""
    sleep 5
fi

# =============================================================================
# Default configuration (override via env vars)
# =============================================================================

# Model — default is RedHatAI's FP8 dynamic quant (~33 GB, vLLM-tested).
# Alternatives:
#   - google/gemma-4-31B-it                      (BF16, ~61 GB, A100-80GB+ only)
#   - RedHatAI/gemma-4-31B-it-FP8-block          (FP8 block, ~33 GB, v1.0)
#   - RedHatAI/gemma-4-31B-it-NVFP4              (W4A4, ~17 GB, Hopper/Blackwell)
#   - QuantTrio/gemma-4-31B-it-AWQ               (AWQ INT4, ~20 GB, all GPUs)
MODEL="${MODEL:-RedHatAI/gemma-4-31B-it-FP8-Dynamic}"

# Context — 128K is the safe default on both A100-80GB and L40S-48GB at FP8.
#   - Lower to 32768 or 65536 on <48 GB GPUs
#   - Raise to 262144 for full 256K (A100-80GB OK; L40S cannot fit 256K)
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"

TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

# GPU memory — 0.92 leaves bookkeeping headroom for the Hybrid KV Cache Manager.
# Bump to 0.95 on H100/H200 or when using FP8 quants of the model.
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"

# KV cache dtype — FP8 KV requires FA3 (Hopper+). On Ampere/Ada the FA2 kernel
# rejects FP8 KV when combined with Gemma 4's interleaved SWA layers, aborting
# engine startup ("kv_cache_dtype not supported"). Default to bf16 on
# Ampere/Ada, fp8_e5m2 on Hopper/Blackwell. Override explicitly if you know
# your model+backend combo supports it.
if [ -z "${KV_CACHE_DTYPE}" ]; then
    case "${GPU_ARCH}" in
        hopper|blackwell) KV_CACHE_DTYPE="fp8_e5m2" ;;
        *)                KV_CACHE_DTYPE="auto" ;;
    esac
fi

# Batching — Gemma 4 is a reasoning/multimodal generalist, so moderate batching.
MAX_NUM_SEQS="${MAX_NUM_SEQS:-32}"
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-8192}"

# Performance flags — V1 engine default is chunked prefill + prefix caching on.
# NOTE: Gemma 4 sliding-window attention reduces prefix-cache hit efficiency vs
# a pure-global model; hits still happen, just fewer (vLLM #3355, #14881).
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-true}"
ENABLE_CHUNKED_PREFILL="${ENABLE_CHUNKED_PREFILL:-true}"
ASYNC_SCHEDULING="${ASYNC_SCHEDULING:-true}"

# Tool calling — Gemma 4 uses a custom serialization format (non-JSON).
# The `gemma4` parser handles it. `gemma4` reasoning parser extracts thinking.
ENABLE_AUTO_TOOL_CHOICE="${ENABLE_AUTO_TOOL_CHOICE:-true}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-gemma4}"
REASONING_PARSER="${REASONING_PARSER:-gemma4}"

# API
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
API_KEY="${API_KEY:-}"
LOG_LEVEL="${LOG_LEVEL:-info}"

# =============================================================================
# Build command
# =============================================================================

CMD="vllm serve ${MODEL}"
CMD="${CMD} --host ${HOST} --port ${PORT}"
CMD="${CMD} --max-model-len ${MAX_MODEL_LEN}"
CMD="${CMD} --tensor-parallel-size ${TENSOR_PARALLEL_SIZE}"
CMD="${CMD} --gpu-memory-utilization ${GPU_MEMORY_UTILIZATION}"
CMD="${CMD} --max-num-seqs ${MAX_NUM_SEQS}"
CMD="${CMD} --max-num-batched-tokens ${MAX_NUM_BATCHED_TOKENS}"

if [ -n "${KV_CACHE_DTYPE}" ] && [ "${KV_CACHE_DTYPE}" != "auto" ]; then
    CMD="${CMD} --kv-cache-dtype ${KV_CACHE_DTYPE}"
fi

CMD="${CMD} --attention-config {\"backend\":\"${ATTENTION_BACKEND}\"}"

if [ "${ASYNC_SCHEDULING}" = "true" ]; then
    CMD="${CMD} --async-scheduling"
else
    CMD="${CMD} --no-async-scheduling"
fi

[ "${ENABLE_PREFIX_CACHING}" = "true" ] && CMD="${CMD} --enable-prefix-caching"

if [ "${ENABLE_CHUNKED_PREFILL}" = "true" ]; then
    CMD="${CMD} --enable-chunked-prefill"
else
    CMD="${CMD} --no-enable-chunked-prefill"
fi

if [ "${ENABLE_AUTO_TOOL_CHOICE}" = "true" ]; then
    CMD="${CMD} --enable-auto-tool-choice --tool-call-parser ${TOOL_CALL_PARSER}"
fi

[ -n "${REASONING_PARSER}" ] && CMD="${CMD} --reasoning-parser ${REASONING_PARSER}"
[ -n "${API_KEY}" ]          && CMD="${CMD} --api-key ${API_KEY}"

CMD="${CMD} --trust-remote-code --enable-prompt-tokens-details"
CMD="${CMD} --uvicorn-log-level ${LOG_LEVEL}"
CMD="${CMD} $@"

# =============================================================================
# Print configuration and start
# =============================================================================
VLLM_VERSION=$(python3 -c "import vllm; print(vllm.__version__)" 2>/dev/null || echo "unknown")
echo "=============================================="
echo "  gemma4-dense-vllm (vLLM ${VLLM_VERSION})"
echo "=============================================="
echo "GPU:               ${GPU_ARCH}"
echo "Attention:         ${ATTENTION_BACKEND}"
echo "Model:             ${MODEL}"
echo "Max context:       ${MAX_MODEL_LEN}"
echo "GPU memory util:   ${GPU_MEMORY_UTILIZATION}"
echo "KV cache dtype:    ${KV_CACHE_DTYPE}"
echo "Async scheduling:  ${ASYNC_SCHEDULING}"
echo "Prefix caching:    ${ENABLE_PREFIX_CACHING}"
echo "Chunked prefill:   ${ENABLE_CHUNKED_PREFILL}"
echo "Tool parser:       ${TOOL_CALL_PARSER}"
echo "Reasoning parser:  ${REASONING_PARSER}"
echo "Endpoint:          http://${HOST}:${PORT}/v1"
echo "=============================================="

exec ${CMD}
