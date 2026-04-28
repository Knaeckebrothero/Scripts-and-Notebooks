#!/bin/bash
# L40S-tuned entrypoint for Gemma 4 31B dense (INT4 AWQ)
# Single GPU target: L40S-48GB (Ada, sm_89). Will refuse to start on others.
#
# KV math (fp8_e4m3, 50 SWA layers x 16 KV heads x 256 head_dim
#                  + 10 global layers x 16 KV heads x 512 head_dim):
#   - 0.547 MB per token
#   - 8K ctx:  4.48 GB per session  → ~5 concurrent fit in 26.7 GB KV pool
#   - 16K ctx: 8.96 GB per session  → ~2-3 concurrent
#   - 32K ctx: 17.92 GB per session → 1 concurrent
#   - 65K ctx: 35.84 GB              → OOM
#   - 128K+:   physically impossible on single L40S
#
# Concurrency target: 5-10 agent sessions at 8K context. Above 8K you trade
# concurrency for context — there is no free lunch on 48 GB.
#
# Attention backend: TRITON_ATTN forced. FA2 rejects head_dim=512 on the 10
# global layers (256 SRAM cap), and FlashInfer/FA4/xFormers all break Gemma 4's
# heterogeneous head-dim mix in subtle ways (output corruption by final layer).
#
# CUDA graphs: disabled (--enforce-eager). Graph capture on Ada with TRITON_ATTN
# double-counts KV blocks during warmup → OOM. Eager mode is mandatory here.

set -e

# =============================================================================
# Persistent caches (volume-mounted)
# =============================================================================
# HF_HOME caches the ~16.5 GB AWQ download; VLLM_CONFIG_ROOT caches the JIT
# Triton/AWQ-Marlin kernel artifacts. Mount /mnt/cache as a Docker volume to
# drop subsequent cold-boots from ~5-10 min to ~1-2 min.
#
# IMPORTANT: invalidate VLLM_CONFIG_ROOT manually whenever the vLLM image is
# bumped (e.g. v0.20.0 -> v0.20.1) or the AWQ checkpoint is re-quantized —
# stale compile graphs cause silent kernel-execution errors.
export HF_HOME="${HF_HOME:-/mnt/cache/huggingface}"
export VLLM_CONFIG_ROOT="${VLLM_CONFIG_ROOT:-/mnt/cache/vllm}"
mkdir -p "${HF_HOME}" "${VLLM_CONFIG_ROOT}"

# Skip the 60s peer-to-peer GPU connectivity probe — irrelevant on single-GPU
# nodes (which is the only configuration this image is sized for at TP=1).
export VLLM_SKIP_P2P_CHECK="${VLLM_SKIP_P2P_CHECK:-1}"

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
# GPU Detection & Hard Refusal on non-Ada
# =============================================================================
detect_gpu_arch() {
    if command -v nvidia-smi &> /dev/null; then
        local gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)
        case "$gpu_name" in
            *L40*|*L4*|*RTX*40*|*RTX*Ada*) echo "ada" ;;
            *A100*|*A10*|*A30*|*A40*)      echo "ampere" ;;
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

# This image is L40S-specific. Other archs should use gemma4-dense-vllm.
if [ "${GPU_ARCH}" != "ada" ] && [ "${ALLOW_NON_ADA}" != "true" ]; then
    echo ""
    echo "================================================================"
    echo "  ERROR: gemma4-dense-l40s-vllm is tuned for Ada (L40S/4090)."
    echo "  Detected: ${GPU_ARCH}"
    echo ""
    echo "  Use gemma4-dense-vllm for A100/H100/H200/B200 instead — it"
    echo "  ships FP8-Dynamic weights at full 128K context, which is the"
    echo "  better fit on HBM-class GPUs."
    echo ""
    echo "  Set ALLOW_NON_ADA=true to bypass this check (not recommended;"
    echo "  fp8_e4m3 KV degrades on Ampere, and INT4 wastes HBM bandwidth)."
    echo "================================================================"
    exit 1
fi

detect_gpu_vram_gb() {
    if command -v nvidia-smi &> /dev/null; then
        nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | awk '{printf "%d", $1/1024}'
    else
        echo "0"
    fi
}

GPU_VRAM_GB=$(detect_gpu_vram_gb)
echo "Detected GPU VRAM: ${GPU_VRAM_GB} GB"

# Hard floor: 40 GB. Below that, INT4 weights (~16.5 GB) + AWQ runtime overhead
# + any usable KV pool will not fit. RTX 4090-24GB hits this floor — use the
# MoE container or a smaller model on that hardware.
MIN_VRAM_GB="${MIN_VRAM_GB:-40}"
if [ "${GPU_VRAM_GB}" -gt 0 ] && [ "${GPU_VRAM_GB}" -lt "${MIN_VRAM_GB}" ] \
   && [ "${SKIP_VRAM_CHECK}" != "true" ]; then
    echo ""
    echo "================================================================"
    echo "  WARNING: ${GPU_VRAM_GB} GB VRAM detected — need ${MIN_VRAM_GB}+"
    echo "  Default INT4 AWQ (~16.5 GB) + KV pool (~26.7 GB) will OOM."
    echo "  Set SKIP_VRAM_CHECK=true to silence; continuing in 5s."
    echo "================================================================"
    sleep 5
fi

# =============================================================================
# Default configuration (override via env vars)
# =============================================================================

# Model — INT4 AWQ from cyankiwi (the variant the L40S report explicitly
# validates). ~16.5 GB resident, ~26.7 GB available for KV pool.
# Alternatives (override via MODEL=...):
#   - QuantTrio/gemma-4-31B-it-AWQ          (community AWQ, also INT4)
#   - Intel/gemma-4-31B-it-int4-AutoRound   (set QUANTIZATION=autoround)
MODEL="${MODEL:-cyankiwi/gemma-4-31B-it-AWQ-4bit}"
QUANTIZATION="${QUANTIZATION:-awq}"

# Context — 8K is the load-bearing flag for L40S agent concurrency.
#   - 8192:  ~5 concurrent sessions (RECOMMENDED for agent workloads)
#   - 16384: ~2-3 concurrent (severely restricts API concurrency)
#   - 32768: 1 concurrent only
#   - 65536+: OOM
MAX_MODEL_LEN="${MAX_MODEL_LEN:-8192}"

TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

# 0.92 leaves ~3.6 GB buffer for PyTorch context, AWQ Marlin scratch, and
# fragmentation. Going higher (0.95+) on Ada under TRITON_ATTN risks the
# preemption loop documented in DoS Issue #29.
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"

# fp8_e4m3 KV is MANDATORY on L40S. Halves KV footprint vs BF16, which is
# the only way 5 concurrent 8K sessions fit in 26.7 GB. Native FP8 TC speed
# on Ada (sm_89) — no software fallback. e4m3 has better dynamic range than
# e5m2 for KV (1 sign / 4 exp / 3 mantissa).
#
# Issue #40388 does NOT apply here: that bug is specific to FP8-WEIGHTS x
# FP8-KV interaction (per-token FP8 weight quantization broken by Gemma 4's
# heterogeneous head dims). AWQ INT4 weights take a different code path.
KV_CACHE_DTYPE="${KV_CACHE_DTYPE:-fp8_e4m3}"

# Batching — capped low because the 26.7 GB KV pool is the binding constraint.
# At 5 concurrent 8K sessions the pool is fully committed. Burst-admitting
# more triggers preemption cascades (vllm:num_preemptions_total).
MAX_NUM_SEQS="${MAX_NUM_SEQS:-10}"
# 4096 is the L40S report's safety bound. Higher values let the chunked-prefill
# scheduler ingest oversized prefills, exhaust KV blocks, and lock subsequent
# requests into a preemption loop (DoS Issue #29).
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-4096}"

# Multimodal — disabled by default. Text-only agent loop is the workload this
# image targets; freeing the vision encoder buffer (~1-2 GB) goes to the KV
# pool. Re-enable per-deployment with -e LIMIT_MM_PER_PROMPT=image=2,audio=0.
LIMIT_MM_PER_PROMPT="${LIMIT_MM_PER_PROMPT:-image=0,audio=0}"

# Performance flags
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-true}"
ENABLE_CHUNKED_PREFILL="${ENABLE_CHUNKED_PREFILL:-true}"
ASYNC_SCHEDULING="${ASYNC_SCHEDULING:-true}"

# CUDA graphs OFF. Graph capture on Ada under TRITON_ATTN double-counts KV
# blocks during warmup, leading to OOM crashes before the engine reaches
# steady state. Eager mode bypasses graph capture entirely. Cost: ~5-10%
# per-step overhead. Benefit: the container actually boots.
ENFORCE_EAGER="${ENFORCE_EAGER:-true}"

# Tool calling
ENABLE_AUTO_TOOL_CHOICE="${ENABLE_AUTO_TOOL_CHOICE:-true}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-gemma4}"
REASONING_PARSER="${REASONING_PARSER:-gemma4}"

# Thinking mode — server-side <|think|> activation. Pair with client-side
# `"skip_special_tokens": false` so the <|channel|> delimiters reach the
# reasoning parser (vLLM Issue #38855).
ENABLE_THINKING="${ENABLE_THINKING:-true}"

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
CMD="${CMD} --kv-cache-dtype ${KV_CACHE_DTYPE}"

[ -n "${QUANTIZATION}" ] && CMD="${CMD} --quantization ${QUANTIZATION}"

# Force TRITON_ATTN — FA2 rejects 512 head_dim on global layers, FlashInfer/
# FA4 corrupt outputs on heterogeneous head dims. There is no override on Ada.
CMD="${CMD} --attention-config {\"backend\":\"TRITON_ATTN\"}"

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

[ "${ENFORCE_EAGER}" = "true" ] && CMD="${CMD} --enforce-eager"

if [ "${ENABLE_AUTO_TOOL_CHOICE}" = "true" ]; then
    CMD="${CMD} --enable-auto-tool-choice --tool-call-parser ${TOOL_CALL_PARSER}"
fi

[ -n "${REASONING_PARSER}" ] && CMD="${CMD} --reasoning-parser ${REASONING_PARSER}"
[ -n "${API_KEY}" ]          && CMD="${CMD} --api-key ${API_KEY}"
[ -n "${LIMIT_MM_PER_PROMPT}" ] && CMD="${CMD} --limit-mm-per-prompt ${LIMIT_MM_PER_PROMPT}"

if [ "${ENABLE_THINKING}" = "true" ]; then
    CMD="${CMD} --default-chat-template-kwargs {\"enable_thinking\":true}"
fi

CMD="${CMD} --trust-remote-code --enable-prompt-tokens-details"
CMD="${CMD} --uvicorn-log-level ${LOG_LEVEL}"
CMD="${CMD} $@"

# =============================================================================
# Print configuration and start
# =============================================================================
VLLM_VERSION=$(python3 -c "import vllm; print(vllm.__version__)" 2>/dev/null || echo "unknown")
echo "=============================================="
echo "  gemma4-dense-l40s-vllm (vLLM ${VLLM_VERSION})"
echo "=============================================="
echo "GPU:               ${GPU_ARCH} (${GPU_VRAM_GB} GB)"
echo "Attention:         TRITON_ATTN (forced on Ada)"
echo "Model:             ${MODEL}"
echo "Quantization:      ${QUANTIZATION}"
echo "Max context:       ${MAX_MODEL_LEN}"
echo "GPU memory util:   ${GPU_MEMORY_UTILIZATION}"
echo "KV cache dtype:    ${KV_CACHE_DTYPE}"
echo "Max num seqs:      ${MAX_NUM_SEQS}"
echo "Max batched tok:   ${MAX_NUM_BATCHED_TOKENS}"
echo "Limit MM:          ${LIMIT_MM_PER_PROMPT}"
echo "Enforce eager:     ${ENFORCE_EAGER}"
echo "Async scheduling:  ${ASYNC_SCHEDULING}"
echo "Prefix caching:    ${ENABLE_PREFIX_CACHING}"
echo "Chunked prefill:   ${ENABLE_CHUNKED_PREFILL}"
echo "Tool parser:       ${TOOL_CALL_PARSER}"
echo "Reasoning parser:  ${REASONING_PARSER}"
echo "Thinking mode:     ${ENABLE_THINKING}"
echo "HF_HOME:           ${HF_HOME}"
echo "VLLM_CONFIG_ROOT:  ${VLLM_CONFIG_ROOT}"
echo "Endpoint:          http://${HOST}:${PORT}/v1"
echo "=============================================="

exec ${CMD}
