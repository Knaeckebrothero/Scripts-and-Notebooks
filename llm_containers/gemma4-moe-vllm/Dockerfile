# Optimized vLLM image for Gemma 4 26B A4B (sparse MoE)
#
# Build: docker build -t gemma4-moe-vllm:latest .
# Run:   docker run --gpus all -p 8000:8000 --ipc=host gemma4-moe-vllm:latest
#
# Model: google/gemma-4-26B-A4B-it (MoE, 26B total / 4B active, 256K native ctx)
#   - 8 of 128 experts routed per token + 1 shared expert
#   - Weights BF16: ~50 GB  (comfortable on A100-80GB with 25+ GB for KV)
#   - Weights INT4: ~17 GB  (leaves ~60 GB free on A100-80GB)
#   - Modalities: text + image + video (no audio)
#   - License: Apache 2.0
#
# Why a dedicated image:
#   - Same gemma4 parser/backend story as the dense variant but with much
#     more memory headroom — full 256K context is trivial on 80GB.
#   - 4B active params = throughput closer to a 4B dense than a 26B dense,
#     ideal for tight tool-call / agent loops.
#   - Lower capability ceiling than the 31B dense — pick this when throughput
#     and context length matter more than raw reasoning depth.
#
# GPU compatibility:
#   - A100-80GB (sm_80):  BF16 @ full 256K ctx, FA2 backend
#   - L40S-48GB (sm_89):  BF16 @ 64K ctx, FA2 backend
#   - RTX 4090-24GB:      INT4 quant only, 32K ctx
#   - H100/H200:          BF16 @ 256K ctx, FA3 backend

FROM vllm/vllm-openai:v0.19.1

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

WORKDIR /app

RUN python3 -c "import vllm; print(f'vLLM version: {vllm.__version__}')"

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=240s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000 22

ENTRYPOINT ["/app/entrypoint.sh"]
