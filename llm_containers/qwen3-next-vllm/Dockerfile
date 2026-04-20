# Optimized vLLM image for Qwen3-Next-80B-A3B (hybrid MoE)
#
# Build: docker build -t qwen3-next-vllm:latest .
# Run:   docker run --gpus all -p 8000:8000 --ipc=host qwen3-next-vllm:latest
#
# Model: cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit (default; AWQ INT4)
#   - 80B total / 3B active (MoE), hybrid Gated-DeltaNet + Gated-Attention
#   - Weights AWQ-4bit: ~46 GB  (leaves ~30 GB on A100-80GB for KV cache)
#   - Weights BF16:     ~151 GB (requires multi-GPU, not supported by this image)
#   - Native context: 262K (up to 1M with YaRN)
#   - License: Apache 2.0 (weights); community quant
#
# Why a dedicated image:
#   - Requires vLLM >= 0.19.0 stable for hybrid-attention kernel support.
#     Main/nightly has FP8 Marlin regression on Ampere (vLLM issue #39610) —
#     do NOT upgrade past 0.19.x on A100 without re-testing.
#   - Needs VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 for 256K+ context.
#   - KV cache quantization is BROKEN for Qwen3-Next as of April 2026
#     (vLLM issue #26646) — must stay on bfloat16 KV.
#   - Uses `qwen3_xml` tool parser (NOT `qwen3_coder` — that parser can
#     emit infinite special chars on long inputs, see vLLM HF discussion #17).
#   - Hermes streaming regressions fixed in vLLM PR #38168.
#
# GPU compatibility:
#   - A100-80GB (sm_80):  AWQ INT4 @ 64K ctx (tight), 32K ctx (comfortable)
#   - H100/H200 (sm_90):  AWQ INT4 @ 128K ctx, FA3 backend
#   - L40S / 4090 <80GB:  does not fit, use a smaller Qwen3 variant
#
# Alternative models (set MODEL env var):
#   - Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (~31 GB, coding agents)
#   - Qwen/Qwen3-32B-AWQ                    (~20 GB, dense reasoning)
#   - Qwen/Qwen3-32B-FP8                    (~35 GB, dense, full precision)

FROM vllm/vllm-openai:v0.19.0

ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/sshd \
    && echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config \
    && echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config \
    && echo 'PermitEmptyPasswords no' >> /etc/ssh/sshd_config

ENV TORCH_CUDA_ARCH_LIST="8.0;8.9;9.0+PTX"

# Required for Qwen3-Next 256K+ context
ENV VLLM_ALLOW_LONG_MAX_MODEL_LEN=1

WORKDIR /app

RUN python3 -c "import vllm; print(f'vLLM version: {vllm.__version__}')"

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000 22

ENTRYPOINT ["/app/entrypoint.sh"]
