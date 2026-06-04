# Optimized vLLM image for Gemma 4 31B dense — A100 / H100 / H200 / B200
#
# Build: docker build -t gemma4-dense-vllm:latest .
# Run:   docker run --gpus all -p 8000:8000 --ipc=host gemma4-dense-vllm:latest
#
# This image targets HBM-class GPUs (A100/H100/H200/B200) at FP8-Dynamic
# weights with full 128K context. For L40S-48GB (Ada GDDR6) use the sibling
# image gemma4-dense-l40s-vllm — it ships INT4 AWQ + 8K context tuned for
# 5 concurrent agent sessions on 48 GB.
#
# Default model: RedHatAI/gemma-4-31B-it-FP8-Dynamic (FP8 dynamic W8A8, ~31 GB)
#   - Base: google/gemma-4-31B-it (dense, 30.7B params, 256K native context)
#   - vLLM-tested by Red Hat AI (maintainers of llm-compressor)
#   - FP8 weights cut memory ~50% vs BF16 with near-lossless quality
#   - Native FP8 tensor cores on Hopper/Blackwell; Marlin W8A16 fallback on Ampere
#   - Modalities: text + image + video (no audio at this size)
#   - License: Apache 2.0 (base) + Apache 2.0 (RedHatAI re-pack)
#
# Why a dedicated image:
#   - Native `gemma4` tool parser + `gemma4` reasoning parser (vLLM-side fixes
#     from PR #38847 and #39468 required for stable tool-calls).
#   - Hybrid KV Cache Manager required for interleaved 5:1 local-SWA + global
#     attention. FLASHINFER backend is NOT compatible (vLLM issue #20865).
#   - Standard GQA (no attention sinks). head_dim=256 + interleaved SWA makes
#     FA2 on Ampere reject the config (head_size check), so we default to
#     TRITON_ATTN there. FA3 on Hopper/Blackwell handles it natively.
#
# GPU compatibility (at default FP8 weights / 128K context / BF16 KV):
#   - A100-80GB (sm_80):  exactly 1 concurrent 128K sequence (~42.7 GB / 42.6 GB pool).
#                         For >1 concurrent agent, queue externally or lower
#                         MAX_MODEL_LEN (e.g. 65536 → 2 concurrent fit).
#   - H100/H200 (sm_90):  full 128K with concurrency headroom; native FP8 TC,
#                         but KV must stay BF16 (vLLM Issue #40388).
#   - B200 (sm_100):      FP4 weights path possible, but this image stays on
#                         FP8-Dynamic. Use NVFP4 variants for max throughput.
#   - L40S-48GB:          NOT this image — use gemma4-dense-l40s-vllm.
#   - RTX 4090-24GB:      Insufficient VRAM at FP8. Use the MoE container.
#   - To run full BF16:   set MODEL=google/gemma-4-31B-it (requires A100-80GB+).

# v0.22.0 builds on v0.20.0's gemma4 tool-call parser thread-safety fixes
# (#38992, #38909, #39114 — silent JSON corruption, infinite <pad> loops) and
# adds the streaming-after-tool-results reasoning leak fix (#39885), tool-call
# float-corruption fix (#42128), and mixed-resolution image co-batching fix
# (#42217). Reasoning still needs client-side skip_special_tokens=false
# (vLLM #38855, open) — see entrypoint.sh.
FROM vllm/vllm-openai:v0.22.0

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

# Ampere (A100), Hopper (H100/H200), Blackwell (B200) — Ada lives in the
# sibling gemma4-dense-l40s-vllm image so its kernel cache is removed here.
ENV TORCH_CUDA_ARCH_LIST="8.0;9.0;10.0+PTX"

WORKDIR /app

RUN python3 -c "import vllm; print(f'vLLM version: {vllm.__version__}')"

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# start-period=1200s covers worst-case uncached cold boot (~10 min HF download +
# ~50 s torch.compile + KV profiling). Without this, k8s/RunPod readiness probes
# SIGKILL the container mid-compile, trapping it in an infinite restart loop.
# Drops to ~2-3 min when the torch_compile_cache + HF cache volumes are warm.
HEALTHCHECK --interval=30s --timeout=10s --start-period=1200s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000 22

ENTRYPOINT ["/app/entrypoint.sh"]
