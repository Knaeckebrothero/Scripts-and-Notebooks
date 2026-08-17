#!/bin/bash
# Optimized entrypoint for Muse Glimmer 30B (Meta, dense vision-language model)
# Supports: L40S-48GB (primary), A100-80GB, H100/H200
#
# KV cache math (52 layers = 13 global + 39 local/SWA-2048, 2 KV heads, head_dim 128):
#   per token per layer = 2 (K+V) * 2 heads * 128 = 512 elements
#   full 128K sequence, bf16 KV:
#       global 13 * 131072 * 1024 B = 1.625 GiB
#       local  39 *   2048 * 1024 B = 0.076 GiB
#       -------------------------------------- ~1.70 GiB / sequence
#   full 128K sequence, fp8 KV:  ~0.85 GiB / sequence
#
# That is remarkably thin for a 30B — GQA 16:1 plus a 3:1 local/global mix.
# On an L40S with FP8-block weights it leaves room for 3-4 concurrent
# full-length sessions WITHOUT resorting to an FP8 KV cache.
#
# Attention sinks: NO. Use FLASH_ATTN (FA2 on Ada/Ampere, FA3 on Hopper).
# Do NOT use FLASHINFER — its kernels fail at startup for this model
# (reported during vLLM PR #51655 review).
#
# IMPORTANT: this model is a reasoning model and MUST NOT be run greedy.
# Meta's sampling recipe is temperature 1.0 / top_p 0.95 / top_k 64, shipped in
# the repo's generation_config.json — `--generation-config auto` picks it up.
# Reasoning strength (low|medium|high|xhigh) is selected via the SYSTEM PROMPT,
# not via chat-template kwargs (this differs from Gemma 4's enable_thinking).

set -e

# =============================================================================
# Persistent caches (volume-mounted)
# =============================================================================
# HF_HOME caches the ~32 GiB weight download; VLLM_CONFIG_ROOT caches JIT
# torch.compile artifacts AND the Triton block-FP8 GEMM autotune results, which
# are expensive on Ada. Mount both to a volume — cold boot drops from ~15 min
# to ~2-3 min on subsequent starts.
#
# IMPORTANT: wipe VLLM_CONFIG_ROOT whenever the vLLM image is bumped (e.g. the
# muse-glimmer branch build -> v0.28.0) or the model repo is re-uploaded.
export HF_HOME="${HF_HOME:-/mnt/cache/huggingface}"
export VLLM_CONFIG_ROOT="${VLLM_CONFIG_ROOT:-/mnt/cache/vllm}"
mkdir -p "${HF_HOME}" "${VLLM_CONFIG_ROOT}"

# Skip the 60s peer-to-peer GPU connectivity probe — irrelevant at TP=1.
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
            *B100*|*B200*|*GB10*|*GB300*)  echo "blackwell" ;;
            *)                             echo "unknown" ;;
        esac
    else
        echo "unknown"
    fi
}

GPU_ARCH=$(detect_gpu_arch)
echo "Detected GPU architecture: ${GPU_ARCH}"

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
# The default model is FP8-block (~32.1 GiB weights). It needs ~40 GiB to fit
# weights + KV + activations + CUDA graphs. BF16 (~55.5 GiB) needs an 80GB card.
MIN_VRAM_GB="${MIN_VRAM_GB:-40}"

if [ "${GPU_VRAM_GB}" -gt 0 ] && [ "${GPU_VRAM_GB}" -lt "${MIN_VRAM_GB}" ] \
   && [ "${SKIP_VRAM_CHECK}" != "true" ]; then
    echo ""
    echo "================================================================"
    echo "  WARNING: ${GPU_VRAM_GB} GB VRAM detected — need ${MIN_VRAM_GB}+ for the default FP8 model"
    echo "================================================================"
    echo "  Default MODEL='${MODEL}' is FP8-block (~32.1 GiB weights)."
    echo ""
    echo "  Smaller variants (verified HF repos — 4-bit only saves ~10 GiB,"
    echo "  the BF16 vision tower + embeddings do not shrink):"
    echo "    -e MODEL=cyankiwi/Muse-Glimmer-30B-AWQ-INT4        (~22 GiB, AWQ, Marlin on Ada)"
    echo "    -e MODEL=abhishekchohan/Muse-Glimmer-30B-GPTQ-INT4 (~22 GiB, GPTQ)"
    echo ""
    echo "  Note: Meta does NOT ship official FP8/INT4 for Muse Glimmer."
    echo "  Note: RedHatAI/Muse-Glimmer-30B-NVFP4 is W4A4 BLACKWELL-ONLY — not Ada."
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

# FP8-block (compressed-tensors, 128x128 weight blocks + dynamic per-group
# activations). The ONLY 8-bit repo available today — there is no per-tensor
# FP8 build. On Ada this dispatches to vLLM's Triton block-FP8 GEMM rather
# than the sm_90+ CUTLASS path, so expect a throughput penalty vs a
# per-tensor FP8 model of the same size. Benchmark before promising SLAs.
MODEL="${MODEL:-RedHatAI/Muse-Glimmer-30B-FP8-block}"

# Native context is 131072. There is no RoPE-scaling headroom above it.
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"

TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

# 32.1 GiB of weights on a 45 GiB card is tight — 0.95 is deliberate, not
# sloppy. It buys ~6.5 GiB of KV pool (see the math in the header).
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.95}"

# KV cache dtype. Deliberately left at bf16 (`auto`) on Ada: the interleaved
# SWA + fp8_e4m3 KV combination is exactly what forced the Gemma 4 pods onto
# TRITON_ATTN, and at ~1.70 GiB/session we simply do not need the halving.
# On Hopper+ FA3 handles FP8 KV natively, so opt in there.
if [ -z "${KV_CACHE_DTYPE}" ]; then
    case "${GPU_ARCH}" in
        hopper|blackwell) KV_CACHE_DTYPE="fp8_e5m2" ;;
        *)                KV_CACHE_DTYPE="auto" ;;
    esac
fi

# 30B dense at 128K — this is a depth/quality box, not a throughput box.
# 4 gives headroom over the 1-2 concurrent sessions it is sized for while
# staying inside the KV pool (4 * 1.70 GiB = 6.8 GiB worst case).
MAX_NUM_SEQS="${MAX_NUM_SEQS:-4}"
# Lower than the MoE pods (16384): chunked-prefill activation peaks are what
# eat the headroom we need for KV on a 45 GiB card.
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-8192}"

# V1 engine default flags
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-true}"
ENABLE_CHUNKED_PREFILL="${ENABLE_CHUNKED_PREFILL:-true}"
ASYNC_SCHEDULING="${ASYNC_SCHEDULING:-true}"

# Emergency memory lever: skips CUDA-graph capture (~1-2 GiB) at a decode-speed
# cost. Flip this on before lowering MAX_MODEL_LEN if the engine OOMs at boot.
ENFORCE_EAGER="${ENFORCE_EAGER:-false}"

# Tool calling + reasoning.
# Muse Glimmer emits channel-scoped reasoning and XML-style ATEM tool calls —
# NOT JSON, and NOT <think> tags. The dedicated parsers are mandatory; without
# them both channels collapse into `content`. The reasoning parser sets
# skip_special_tokens=False itself, so — unlike Gemma 4 — the orchestrator does
# NOT need a request_defaults block for it.
ENABLE_AUTO_TOOL_CHOICE="${ENABLE_AUTO_TOOL_CHOICE:-true}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-muse_glimmer}"
REASONING_PARSER="${REASONING_PARSER:-muse_glimmer}"

# Pull temperature 1.0 / top_p 0.95 / top_k 64 from the repo's
# generation_config.json. Meta is explicit: do not run this model greedy.
GENERATION_CONFIG="${GENERATION_CONFIG:-auto}"

# Optional: bound multimodal activation memory (4096 visual tokens per image).
# Compact JSON, no spaces — see the ENABLE_THINKING note in gemma4-moe-vllm.
# Example: LIMIT_MM_PER_PROMPT={"image":2}
LIMIT_MM_PER_PROMPT="${LIMIT_MM_PER_PROMPT:-}"

# Optional: DFlash block speculative decoding (~4.8 GiB extra VRAM for the
# draft head). Reported 3.1x on RTX 5090, but flagged "still unusable" on
# SM120 Blackwell during PR review and UNVERIFIED on Ada. num_speculative_tokens
# is fixed at 15 by the draft head's block size (16) — do not tune it.
# On a 45 GiB L40S this trades roughly half the KV pool for latency; only
# worth it at the 1-2 concurrent sessions this pod targets. Off by default.
ENABLE_DFLASH="${ENABLE_DFLASH:-false}"
DFLASH_MODEL="${DFLASH_MODEL:-meta-models/Muse-Glimmer-30B-assistant}"

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
[ "${ENFORCE_EAGER}" = "true" ]         && CMD="${CMD} --enforce-eager"

if [ "${ENABLE_CHUNKED_PREFILL}" = "true" ]; then
    CMD="${CMD} --enable-chunked-prefill"
else
    CMD="${CMD} --no-enable-chunked-prefill"
fi

if [ "${ENABLE_AUTO_TOOL_CHOICE}" = "true" ]; then
    CMD="${CMD} --enable-auto-tool-choice --tool-call-parser ${TOOL_CALL_PARSER}"
fi

[ -n "${REASONING_PARSER}" ]    && CMD="${CMD} --reasoning-parser ${REASONING_PARSER}"
[ -n "${GENERATION_CONFIG}" ]   && CMD="${CMD} --generation-config ${GENERATION_CONFIG}"
[ -n "${LIMIT_MM_PER_PROMPT}" ] && CMD="${CMD} --limit-mm-per-prompt ${LIMIT_MM_PER_PROMPT}"
[ -n "${API_KEY}" ]             && CMD="${CMD} --api-key ${API_KEY}"

# Compact JSON, no spaces, no surrounding quotes — it has to survive
# word-splitting under `exec ${CMD}` as a single token.
if [ "${ENABLE_DFLASH}" = "true" ]; then
    CMD="${CMD} --speculative-config {\"method\":\"dflash\",\"model\":\"${DFLASH_MODEL}\",\"num_speculative_tokens\":15}"
fi

CMD="${CMD} --trust-remote-code --enable-prompt-tokens-details"
CMD="${CMD} --uvicorn-log-level ${LOG_LEVEL}"
CMD="${CMD} $@"

# =============================================================================
# Print configuration and start
# =============================================================================
VLLM_VERSION=$(python3 -c "import vllm; print(vllm.__version__)" 2>/dev/null || echo "unknown")
echo "=============================================="
echo "  muse-glimmer-vllm (vLLM ${VLLM_VERSION})"
echo "=============================================="
echo "GPU:               ${GPU_ARCH}"
echo "Attention:         ${ATTENTION_BACKEND}"
echo "Model:             ${MODEL}"
echo "Max context:       ${MAX_MODEL_LEN}"
echo "GPU memory util:   ${GPU_MEMORY_UTILIZATION}"
echo "KV cache dtype:    ${KV_CACHE_DTYPE}"
echo "Max num seqs:      ${MAX_NUM_SEQS}"
echo "Async scheduling:  ${ASYNC_SCHEDULING}"
echo "Prefix caching:    ${ENABLE_PREFIX_CACHING}"
echo "Chunked prefill:   ${ENABLE_CHUNKED_PREFILL}"
echo "Enforce eager:     ${ENFORCE_EAGER}"
echo "Tool parser:       ${TOOL_CALL_PARSER}"
echo "Reasoning parser:  ${REASONING_PARSER}"
echo "Generation config: ${GENERATION_CONFIG}"
echo "DFlash spec-dec:   ${ENABLE_DFLASH}"
echo "HF_HOME:           ${HF_HOME}"
echo "VLLM_CONFIG_ROOT:  ${VLLM_CONFIG_ROOT}"
echo "Endpoint:          http://${HOST}:${PORT}/v1"
echo "=============================================="

exec ${CMD}
