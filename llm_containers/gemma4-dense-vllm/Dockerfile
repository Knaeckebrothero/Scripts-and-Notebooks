# Optimized vLLM image for Gemma 4 31B dense
#
# Build: docker build -t gemma4-dense-vllm:latest .
# Run:   docker run --gpus all -p 8000:8000 --ipc=host gemma4-dense-vllm:latest
#
# Default model: RedHatAI/gemma-4-31B-it-FP8-Dynamic (FP8 dynamic W8A8, ~33 GB)
#   - Base: google/gemma-4-31B-it (dense, 30.7B params, 256K native context)
#   - vLLM-tested by Red Hat AI (maintainers of llm-compressor)
#   - FP8 weights cut memory ~50% vs BF16 with near-lossless quality
#   - Native FP8 tensor cores on Hopper/Blackwell; Marlin W8A16 fallback on Ampere/Ada
#   - Modalities: text + image + video (no audio at this size)
#   - License: Apache 2.0 (base) + Apache 2.0 (RedHatAI re-pack)
#
# Why a dedicated image:
#   - Native `gemma4` tool parser + `gemma4` reasoning parser (vLLM-side fixes
#     from PR #38847 and #39468 required for stable tool-calls).
#   - Hybrid KV Cache Manager required for interleaved 5:1 local-SWA + global
#     attention. FLASHINFER backend is NOT compatible (vLLM issue #20865).
#   - Standard GQA (no attention sinks) — FA2 on Ampere, FA3 on Hopper. No
#     Triton fallback penalty like gpt-oss.
#
# GPU compatibility (at default FP8 / 128K context):
#   - A100-80GB (sm_80):  fits comfortably, ~30 GB headroom for higher concurrency
#   - H100/H200 (sm_90):  native FP8 TC, full 256K ctx easy
#   - L40S-48GB (sm_89):  fits at 128K, native FP8 TC (set KV_CACHE_DTYPE=fp8_e4m3)
#   - RTX 4090-24GB:      use AWQ variant (QuantTrio/gemma-4-31B-it-AWQ, ~20 GB)
#   - To run full BF16:   set MODEL=google/gemma-4-31B-it (requires A100-80GB+)

FROM vllm/vllm-openai:v0.19.1

ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# SSH for RunPod tunneling (bypasses Cloudflare 30s timeout)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/sshd \
    && echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config \
    && echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config \
    && echo 'PermitEmptyPasswords no' >> /etc/ssh/sshd_config

# Ampere (A100), Ada (L40S/4090), Hopper (H100/H200)
ENV TORCH_CUDA_ARCH_LIST="8.0;8.9;9.0+PTX"

WORKDIR /app

RUN python3 -c "import vllm; print(f'vLLM version: {vllm.__version__}')"

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000 22

ENTRYPOINT ["/app/entrypoint.sh"]
