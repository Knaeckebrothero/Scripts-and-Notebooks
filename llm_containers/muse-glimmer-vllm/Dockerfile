# Optimized vLLM image for Muse Glimmer 30B (Meta, dense VLM)
#
# Build: docker build -t muse-glimmer-vllm:latest .
# Run:   docker run --gpus all -p 8000:8000 --ipc=host muse-glimmer-vllm:latest
#
# Model: meta-models/Muse-Glimmer-30B (dense 29.6B incl. 1.8B ViT-G/14, 128K ctx)
#   - 52 layers, hidden 6656, 32 Q heads / 2 KV heads (GQA 16:1), head_dim 128
#   - Attention: [Local, Local, Local, Global] repeating -> 39 SWA(2048) + 13 global
#   - RoPE theta 500000 on the LOCAL layers only; gated attention
#   - Weights BF16:      ~55.5 GiB  (needs 80GB-class card)
#   - Weights FP8-block: ~32.1 GiB  (fits L40S-48GB — the only 8-bit repo today)
#   - Modalities: text + image in, text out (max 4096 visual tokens/image)
#   - License: Apache 2.0
#
# !! BASE IMAGE IS NOT A TAGGED vLLM RELEASE !!
#   Muse Glimmer support landed upstream in PR #51655, merged 2026-08-14 —
#   AFTER v0.27.1 (2026-08-11), the newest stable release. It is therefore in
#   NO released vLLM version. Until v0.28.0 ships, the only blessed build is
#   the launch-day branch image `vllm/vllm-openai:muse-glimmer`
#   (build commit 99a10304dce8945119bd0b1a072297803c52a749, pushed
#   2026-08-11, CUDA 13.0.2, TORCH_CUDA_ARCH_LIST includes 8.9 = Ada/L40S).
#   Swap the FROM to `vllm/vllm-openai:v0.28.0` once it is out, then rebuild
#   and invalidate VLLM_CONFIG_ROOT (stale compile graphs).
#
# GPU compatibility:
#   - L40S-48GB (sm_89):  FP8-block @ 128K ctx, FLASH_ATTN backend  <-- our target
#   - A100-80GB (sm_80):  BF16 @ 128K ctx (no native FP8 on Ampere)
#   - H100/H200 (sm_90):  BF16 or FP8-block @ 128K ctx, FP8 KV available
#   - Blackwell (sm_100+): NVFP4 (W4A4) — Blackwell-only, do NOT use on Ada
FROM vllm/vllm-openai:muse-glimmer

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

# 30B dense + FP8-block Triton autotune on Ada makes the first boot slow.
HEALTHCHECK --interval=30s --timeout=10s --start-period=600s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000 22

# Base image ships ENTRYPOINT ["vllm","serve"] — override it.
ENTRYPOINT ["/app/entrypoint.sh"]
