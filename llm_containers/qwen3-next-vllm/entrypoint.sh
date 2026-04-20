#!/bin/bash
# Optimized entrypoint for Qwen3-Next-80B-A3B-Instruct (hybrid MoE)
# Supports: A100-80GB (AWQ only), H100-80GB, H200, B200
#
# Model memory (AWQ INT4 = ~46 GB weights):
#   - 32K ctx:   ~4 GB KV   -> comfortable on A100-80GB
#   - 64K ctx:   ~8 GB KV   -> default, safe on A100-80GB
#   - 128K ctx:  ~16 GB KV  -> requires H100/H200 or Blackwell
#   - 256K ctx:  ~32 GB KV  -> requires 2x A100 or an H200
#
# Architecture: hybrid Gated-DeltaNet + Gated-Attention, 3B active params.
# No attention sinks — FA2 on Ampere, FA3 on Hopper, FA4 on Blackwell.
#
# IMPORTANT caveats (as of April 2026):
#   - KV cache quantization is NOT supported for Qwen3-Next hybrid attention
#     (vLLM issue #26646) — stay on bfloat16 KV.
#   - Requires VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 (set in Dockerfile).
#   - Use `qwen3_xml` tool parser (NOT `qwen3_coder` — emits garbage on long
#     inputs; see Qwen HF discussion #17 and vLLM HF discussion #17).
#   - Pin vLLM 0.19.x on Ampere — main/nightly regressed FP8 Marlin (#39610).

set -e

# =============================================================================
# SSH Server (optional)
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
# Default model is already AWQ-4bit (~46 GB). FP8 KV cache is BROKEN for
# Qwen3-Next (#26646) so KV must be bf16 — eats memory fast. Need ~56 GB
# minimum (weights + bf16 KV at 32K + overhead). Below that, the user must
# switch to a smaller Qwen3 variant.
MIN_VRAM_GB="${MIN_VRAM_GB:-56}"

if [ "${GPU_VRAM_GB}" -gt 0 ] && [ "${GPU_VRAM_GB}" -lt "${MIN_VRAM_GB}" ] \
   && [ "${SKIP_VRAM_CHECK}" != "true" ]; then
    echo ""
    echo "================================================================"
    echo "  WARNING: ${GPU_VRAM_GB} GB VRAM detected — need ${MIN_VRAM_GB}+ for default AWQ model"
    echo "================================================================"
    echo "  Default MODEL='${MODEL}' is AWQ-4bit (~46 GB weights)."
    echo "  KV cache MUST be bfloat16 for Qwen3-Next (vLLM #26646)"
    echo "  — FP8 KV is unavailable, so KV memory grows fast."
    echo "  This will OOM on a ${GPU_VRAM_GB} GB GPU."
    echo ""
    echo "  Pick a smaller Qwen variant for sub-56GB hardware:"
    echo "    -e MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8  (~31 GB, coding agents)"
    echo "    -e MODEL=Qwen/Qwen3-32B-AWQ                     (~20 GB, dense)"
    echo "    -e MODEL=Qwen/Qwen3-32B-FP8                     (~35 GB, dense)"
    echo ""
    echo "  Note: smaller Qwen3 variants have BETTER prefix-cache support than"
    echo "  Qwen3-Next and don't have the bf16-only KV cache restriction."
    echo ""
    echo "  Set SKIP_VRAM_CHECK=true to silence this warning."
    echo "  Continuing in 5s — Ctrl+C to abort..."
    echo "================================================================"
    echo ""
    sleep 5
fi

# =============================================================================
# Default configuration
# =============================================================================

# Default to a community AWQ-4bit quant. Swap to official if/when Qwen ships one.
# Alternatives:
#   - Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8  (~31 GB, coding-specialized)
#   - Qwen/Qwen3-32B-AWQ                     (~20 GB, dense)
#   - Qwen/Qwen3-32B-FP8                     (~35 GB, dense, full precision)
MODEL="${MODEL:-cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit}"

# Quantization — AWQ is the A100-native path (no FP8 tensor cores on sm_80).
# Set to 'auto' for FP8/GPTQ variants, 'awq' for AWQ repos, 'awq_marlin' for Marlin kernels.
QUANTIZATION="${QUANTIZATION:-awq_marlin}"

# 64K default — safe with ~46 GB weights + bf16 KV on A100-80GB.
# Full 256K requires H100+ (or accept tighter concurrency).
MAX_MODEL_LEN="${MAX_MODEL_LEN:-65536}"

TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.93}"

# bfloat16 is required — FP8 KV quant is broken for Qwen3-Next (#26646).
KV_CACHE_DTYPE="${KV_CACHE_DTYPE:-bfloat16}"

# 3B active params = very high throughput even at high concurrency.
MAX_NUM_SEQS="${MAX_NUM_SEQS:-64}"
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-8192}"

# Prefix caching hit-rate can be low on some A3B variants (vLLM #36493 reports
# ~0% for Qwen3.5-35B-A3B). Still enabled — free speedup when it hits, no cost
# when it doesn't. Disable if you observe cache poisoning.
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-true}"
ENABLE_CHUNKED_PREFILL="${ENABLE_CHUNKED_PREFILL:-true}"
ASYNC_SCHEDULING="${ASYNC_SCHEDULING:-true}"

# Tool calling — qwen3_xml is the stable parser for Qwen3-Next / Coder variants.
# Do NOT use qwen3_coder: has known infinite special-char bug on long inputs.
ENABLE_AUTO_TOOL_CHOICE="${ENABLE_AUTO_TOOL_CHOICE:-true}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-qwen3_xml}"

# Reasoning parser — Qwen3-Next ships a thinking mode. Empty = disabled.
# Set REASONING_PARSER=qwen3 if you want thinking content parsed into a separate field.
REASONING_PARSER="${REASONING_PARSER:-}"

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

if [ -n "${QUANTIZATION}" ] && [ "${QUANTIZATION}" != "auto" ]; then
    CMD="${CMD} --quantization ${QUANTIZATION}"
fi

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
echo "  qwen3-next-vllm (vLLM ${VLLM_VERSION})"
echo "=============================================="
echo "GPU:               ${GPU_ARCH}"
echo "Attention:         ${ATTENTION_BACKEND}"
echo "Model:             ${MODEL}"
echo "Quantization:      ${QUANTIZATION}"
echo "Max context:       ${MAX_MODEL_LEN}"
echo "GPU memory util:   ${GPU_MEMORY_UTILIZATION}"
echo "KV cache dtype:    ${KV_CACHE_DTYPE}"
echo "Async scheduling:  ${ASYNC_SCHEDULING}"
echo "Prefix caching:    ${ENABLE_PREFIX_CACHING}"
echo "Chunked prefill:   ${ENABLE_CHUNKED_PREFILL}"
echo "Tool parser:       ${TOOL_CALL_PARSER}"
echo "Reasoning parser:  ${REASONING_PARSER:-disabled}"
echo "Endpoint:          http://${HOST}:${PORT}/v1"
echo "=============================================="

exec ${CMD}
