#!/bin/bash
# Optimized entrypoint for Gemma 4 26B A4B (sparse MoE, 4B active params)
# Supports: A100-80GB, H100-80GB, H200, L40S-48GB, B200
#
# Model memory (BF16 weights = ~50 GB):
#   - 64K ctx:  ~1.3 GB KV -> trivial fit on A100-80GB
#   - 128K ctx: ~2.6 GB KV -> default, plenty of headroom
#   - 256K ctx: ~5.2 GB KV -> still comfortable on A100-80GB
#
# Note: MoE KV cache is thinner than the dense variant (4B active routing),
# so you can run full 256K context on an A100-80GB without quantization.
#
# Attention sinks: NO — FA2 on Ampere/Ada, FA3 on Hopper.
# Interleaved 5:1 local-SWA + global — do NOT use FLASHINFER (vLLM #20865).

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

ATTENTION_BACKEND="${VLLM_ATTENTION_BACKEND_OVERRIDE:-FLASH_ATTN}"
echo "Attention backend: ${ATTENTION_BACKEND}"

# =============================================================================
# VRAM sanity check
# =============================================================================
# The default model is BF16 (~50 GB weights). It needs ~56 GB minimum to fit
# weights + KV cache + CUDA overhead. On <60 GB GPUs (L40S, RTX, etc.) the user
# must pick a quantized variant. We warn loudly but don't block — the user may
# be loading a quant repo that we can't detect from the model name alone.
MIN_VRAM_GB="${MIN_VRAM_GB:-56}"

if [ "${GPU_VRAM_GB}" -gt 0 ] && [ "${GPU_VRAM_GB}" -lt "${MIN_VRAM_GB}" ] \
   && [ "${SKIP_VRAM_CHECK}" != "true" ]; then
    echo ""
    echo "================================================================"
    echo "  WARNING: ${GPU_VRAM_GB} GB VRAM detected — need ${MIN_VRAM_GB}+ for default BF16 model"
    echo "================================================================"
    echo "  Default MODEL='${MODEL}' is BF16 (~50 GB weights)."
    echo "  This will OOM on a ${GPU_VRAM_GB} GB GPU."
    echo ""
    echo "  Pick a quantized variant for L40S-class hardware (verified HF repos):"
    echo "    -e MODEL=RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic  (~27 GB, native FP8 on Ada/Hopper)"
    echo "    -e MODEL=protoLabsAI/gemma-4-26B-A4B-it-FP8       (~27 GB, supports FP8 KV cache)"
    echo "    -e MODEL=RedHatAI/gemma-4-26B-A4B-it-NVFP4        (~17 GB, best on Hopper/Blackwell)"
    echo "    -e MODEL=cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit     (~14 GB, AWQ INT4)"
    echo ""
    echo "  Note: Google does NOT ship official FP8/INT4 for Gemma 4."
    echo "  Note: MoE quants are fragile — prefer RedHatAI / protoLabsAI repos."
    echo "  On Ada (L40S/4090) also set: -e KV_CACHE_DTYPE=fp8_e4m3"
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

MODEL="${MODEL:-google/gemma-4-26B-A4B-it}"

# 128K default — this model is small enough that full 256K fits comfortably
# even on A100-80GB. Raise to 262144 if your agent loops benefit from it.
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"

TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

# With only ~50 GB weights we can push utilization higher safely.
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.95}"

# No attention sinks on Gemma 4 — FP8 KV cache is safe and frees VRAM.
KV_CACHE_DTYPE="${KV_CACHE_DTYPE:-fp8_e5m2}"

# MoE with 4B active = high throughput potential; allow more concurrent seqs.
MAX_NUM_SEQS="${MAX_NUM_SEQS:-64}"
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-16384}"

# V1 engine default flags
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-true}"
ENABLE_CHUNKED_PREFILL="${ENABLE_CHUNKED_PREFILL:-true}"
ASYNC_SCHEDULING="${ASYNC_SCHEDULING:-true}"

# Tool calling — same parsers as the dense variant
ENABLE_AUTO_TOOL_CHOICE="${ENABLE_AUTO_TOOL_CHOICE:-true}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-gemma4}"
REASONING_PARSER="${REASONING_PARSER:-gemma4}"

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
echo "  gemma4-moe-vllm (vLLM ${VLLM_VERSION})"
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
