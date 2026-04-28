#!/bin/bash
# Optimized entrypoint for Gemma 4 31B dense — HBM-class GPUs only
# Supports: A100-80GB, H100-80GB, H200, B200
# (For L40S-48GB Ada, use sibling image gemma4-dense-l40s-vllm.)
#
# Model memory (DEFAULT: FP8 weights ~31 GB, BF16 KV — see Issue #40388):
#   - 32K ctx:  ~10.5 GB per sequence  (4 concurrent fit on A100-80GB)
#   - 64K ctx:  ~21.4 GB per sequence  (2 concurrent fit on A100-80GB)
#   - 128K ctx: ~42.7 GB per sequence  (exactly 1 concurrent at 0.92 util)
#   - 256K ctx: ~85 GB per sequence    (will not fit any single GPU)
#
# Per-sequence KV is dominated by global layers (10 layers x 16 forced KV heads
# x 512 head_dim x BF16 x ctx). vLLM PagedAttention forces uniform tensor shapes
# across the 5:1 SWA/global layer mix, so global layers are allocated as if they
# had 16 KV heads (not 4) and separate K/V (not shared) — see vllm-metal #276.
# The "128K KV pool ~42.6 GB" headroom on A100 means: route long-context agents
# through external queueing in the application router, not vLLM's scheduler.
#
# To run full-precision BF16 (~61 GB weights, A100-80GB+ only):
#   MODEL=google/gemma-4-31B-it MAX_MODEL_LEN=65536
#
# Attention sinks: NO (standard GQA). head_dim=256 + interleaved SWA makes
# FA2 on Ampere/Ada reject the config (head_size check in cuda.py:269), so
# we default to TRITON_ATTN there. FA3 on Hopper/Blackwell handles it natively.
# Interleaved 5:1 local-SWA + global attention handled by vLLM Hybrid KV Cache Manager.
# FLASHINFER backend is NOT compatible (vLLM issue #20865).

set -e

# =============================================================================
# Persistent caches (volume-mounted)
# =============================================================================
# HF_HOME caches the ~31 GB weight download; VLLM_CONFIG_ROOT caches the JIT
# torch.compile artifacts (Dynamo bytecode + Triton kernel graphs). Mount both
# to a Docker volume to drop cold-boot from ~15 min to ~2-3 min on subsequent
# starts. Bound to /mnt/cache/* by convention; override the env vars to redirect.
#
# IMPORTANT: invalidate VLLM_CONFIG_ROOT manually whenever the vLLM image is
# bumped (e.g. v0.20.0 -> v0.20.1) or the model repo is patched on Hugging
# Face — stale compile graphs cause silent kernel-execution errors.
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

# Ada (L40S/4090) has its own dedicated image. Refuse here to prevent users
# from running the FP8-weights / 128K-context defaults on a 48 GB card and
# wasting boot cycles on inevitable OOMs. Override with ALLOW_ADA=true if
# you know what you're doing (e.g. running with custom MAX_MODEL_LEN).
if [ "${GPU_ARCH}" = "ada" ] && [ "${ALLOW_ADA}" != "true" ]; then
    echo ""
    echo "================================================================"
    echo "  ERROR: Ada (L40S/4090) detected. This image targets HBM-class"
    echo "  GPUs (A100/H100/H200/B200). Use gemma4-dense-l40s-vllm for"
    echo "  L40S — it ships INT4 AWQ + 8K context tuned for 5 concurrent"
    echo "  agent sessions on 48 GB."
    echo ""
    echo "  Set ALLOW_ADA=true to bypass (you must also override MODEL,"
    echo "  MAX_MODEL_LEN, and KV_CACHE_DTYPE manually)."
    echo "================================================================"
    exit 1
fi

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

# FA2 on Ampere (sm_80) rejects Gemma 4's head_dim=256 combined with
# interleaved SWA ("head_size not supported" in vllm/platforms/cuda.py
# during layer init). Default to TRITON_ATTN there. FA3 on Hopper/Blackwell
# handles head_dim=256 + SWA natively, so keep FLASH_ATTN on sm_90+.
# Do NOT use FLASHINFER on any arch (breaks interleaved SWA, vLLM #20865).
if [ -n "${VLLM_ATTENTION_BACKEND_OVERRIDE}" ]; then
    ATTENTION_BACKEND="${VLLM_ATTENTION_BACKEND_OVERRIDE}"
else
    case "${GPU_ARCH}" in
        ampere)            ATTENTION_BACKEND="TRITON_ATTN" ;;
        hopper|blackwell)  ATTENTION_BACKEND="FLASH_ATTN" ;;
        ada)               ATTENTION_BACKEND="TRITON_ATTN" ;;
        *)                 ATTENTION_BACKEND="FLASH_ATTN" ;;
    esac
fi
echo "Attention backend: ${ATTENTION_BACKEND}"

# =============================================================================
# VRAM sanity check
# =============================================================================
# Default FP8 weights (~31 GB) + 1× 128K BF16 KV (~42.7 GB) + CUDA/exec overhead
# needs ~75 GB minimum. The 44 GB floor below allows lower-context overrides
# (MAX_MODEL_LEN=32768 uses ~10.5 GB KV → fits 44 GB cards). On <44 GB GPUs the
# user must reduce context, switch to a smaller quant, or use the MoE container.
#
# If MODEL has been overridden to BF16 (google/gemma-4-31B-it, ~61 GB weights),
# override MIN_VRAM_GB to ~110 manually — we can't reliably detect that here.
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
    echo "    1) For L40S-48GB:       use gemma4-dense-l40s-vllm (INT4 + 8K)"
    echo "    2) Reduce context:      -e MAX_MODEL_LEN=32768  (fits 40+ GB GPUs)"
    echo "    3) Smaller quant:       -e MODEL=RedHatAI/gemma-4-31B-it-NVFP4 (~17 GB, Hopper+)"
    echo "    4) Switch to MoE:       use the gemma4-moe-vllm container"
    echo ""
    echo "  Do NOT enable FP8 KV cache for Gemma 4 — vLLM Issue #40388"
    echo "  (heterogeneous head dims break per-token FP8 scale alignment, init crash)."
    echo "  If running BF16 (MODEL=google/gemma-4-31B-it), need 110+ GB VRAM at 128K."
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

# Context — 128K is the safe default on A100-80GB; H100/H200 fit it with
# headroom for >1 concurrent session.
#   - Lower to 32768 or 65536 on tighter VRAM budgets
#   - Raise to 262144 for full 256K (A100-80GB OK; H200 comfortable)
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"

TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

# GPU memory — 0.92 leaves bookkeeping headroom for the Hybrid KV Cache Manager.
# Bump to 0.95 on H100/H200 or when using FP8 quants of the model.
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"

# KV cache dtype — MUST stay on `auto` (BF16) for Gemma 4 on every architecture.
# vLLM Issue #40388: per-token FP8 KV quantization requires a 2:1 block-to-scale
# alignment that is broken by Gemma 4's heterogeneous head dimensions (256 on
# SWA layers, 512 on global layers). Enabling fp8_e4m3 / fp8_e5m2 produces
# immediate memory access violations during engine init. This is a structural
# model-side limitation, NOT a hardware limit, so it bites on H100/H200/B200
# too — not just Ampere/Ada. Revisit only after Issue #40388 lands upstream.
if [ -z "${KV_CACHE_DTYPE}" ]; then
    KV_CACHE_DTYPE="auto"
fi

# Batching — capped at 16 because at 128K context a single sequence consumes
# ~42.7 GB of the ~42.6 GB KV pool on A100-80GB at 0.92 util. Admitting more
# concurrent sequences than the pool can sustain triggers cascading preemptions
# (watch vllm:num_preemptions_total). Long-context concurrency is the router's
# job, not vLLM's scheduler. Raise only if the typical session length is well
# below the max — e.g. with 32K average context, 4 concurrent fit comfortably.
MAX_NUM_SEQS="${MAX_NUM_SEQS:-16}"
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-8192}"

# Restrict CUDA graph capture to the realistic batch sizes for an agentic
# workload. Default vLLM captures graphs for dozens of batch sizes up to 512,
# eating GBs of VRAM that the agent loop will never use. Saved memory goes to
# the KV pool. Trade-off: a burst exceeding 16 concurrent decodes drops to
# eager execution for that step (slower but correct).
CUDAGRAPH_CAPTURE_SIZES="${CUDAGRAPH_CAPTURE_SIZES:-1,2,4,8,16}"

# Multimodal — Gemma 4 31B-it supports text + image + video, NO audio (audio is
# E2B/E4B only). Setting audio=0 frees the audio encoder buffer for the KV pool.
LIMIT_MM_PER_PROMPT="${LIMIT_MM_PER_PROMPT:-image=2,audio=0}"

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

# Thinking mode — server-side injection of the <|think|> activation token.
# Without this, --reasoning-parser gemma4 has nothing to extract because the
# model emits Chain-of-Thought inline in `content`. Pair this with client-side
# `"skip_special_tokens": false` in the request payload so the <|channel|>
# delimiters survive vLLM's text decoder and reach the reasoning parser
# (vLLM Issue #38855). Set ENABLE_THINKING=false to disable globally.
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

[ -n "${CUDAGRAPH_CAPTURE_SIZES}" ] && CMD="${CMD} --cudagraph-capture-sizes ${CUDAGRAPH_CAPTURE_SIZES}"
[ -n "${LIMIT_MM_PER_PROMPT}" ]     && CMD="${CMD} --limit-mm-per-prompt ${LIMIT_MM_PER_PROMPT}"

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
echo "  gemma4-dense-vllm (vLLM ${VLLM_VERSION})"
echo "=============================================="
echo "GPU:               ${GPU_ARCH}"
echo "Attention:         ${ATTENTION_BACKEND}"
echo "Model:             ${MODEL}"
echo "Max context:       ${MAX_MODEL_LEN}"
echo "GPU memory util:   ${GPU_MEMORY_UTILIZATION}"
echo "KV cache dtype:    ${KV_CACHE_DTYPE}"
echo "Max num seqs:      ${MAX_NUM_SEQS}"
echo "Max batched tok:   ${MAX_NUM_BATCHED_TOKENS}"
echo "Cudagraph sizes:   ${CUDAGRAPH_CAPTURE_SIZES}"
echo "Limit MM:          ${LIMIT_MM_PER_PROMPT}"
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
