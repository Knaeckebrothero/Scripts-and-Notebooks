# L40S-tuned vLLM image for Gemma 4 31B dense (INT4 AWQ)
#
# Build: docker build -t gemma4-dense-l40s-vllm:latest .
# Run:   docker run --gpus all -p 8000:8000 --ipc=host gemma4-dense-l40s-vllm:latest
#
# Default model: cyankiwi/gemma-4-31B-it-AWQ-4bit (INT4 AWQ, ~16.5 GB)
#   - Base: google/gemma-4-31B-it (dense, 30.7B params, 60 layers)
#   - INT4 weights via Activation-aware Weight Quantization (AWQ)
#   - Bypasses dynamic FP8's runtime compute overhead — strictly memory-bound
#     on the L40S's 864 GB/s GDDR6 bandwidth (vs HBM2e's 2 TB/s on A100).
#   - License: Apache 2.0 (base) + community quant
#
# Why a dedicated L40S image (separate from gemma4-dense-vllm):
#   - L40S is VRAM-bound at 48 GB; FP8 weights (~31 GB) leave only 12 GB for KV,
#     which forces 1 concurrent session at any usable context. INT4 (~16.5 GB)
#     leaves ~26.7 GB for the KV pool — enough for 5 concurrent agents at 8K.
#   - Concurrency comes from clamping context to 8192 tokens. The native 128K /
#     256K context is operationally unusable on a single L40S for agent serving.
#   - fp8_e4m3 KV is mandatory here: native FP8 tensor cores on Ada (sm_89)
#     deliver hardware-speed compression with no software fallback. Halving KV
#     footprint vs BF16 is what makes 5 concurrent sessions reach the target.
#     The Issue #40388 risk that gates `auto` on the FP8-weights image does not
#     apply: AWQ INT4 weights bypass per-token FP8 weight quantization, so the
#     KV-side fp8_e4m3 path stays clean. (#40388 is an FP8-weights-x-FP8-KV
#     interaction, not a generic FP8-KV bug.)
#   - --enforce-eager: CUDA graph capture on Ada under TRITON_ATTN double-counts
#     KV blocks during warmup, triggering OOM. Eager mode bypasses graph capture
#     entirely. Slight per-step overhead, but it is the difference between
#     boot-and-serve vs crash-loop on this hardware.
#   - --limit-mm-per-prompt image=0,audio=0: text-only agent loop. Disabling
#     vision frees the multimodal encoder buffer (~1-2 GB) for the KV pool.
#
# GPU compatibility:
#   - L40S-48GB (sm_89):     PRIMARY TARGET. ~5 concurrent at 8K, native FP8 TC.
#   - RTX 4090-24GB (sm_89): Will OOM. INT4 weights (~16.5 GB) leave ~6 GB for
#                            KV — only 1 session at 8K, no headroom. Use the
#                            MoE container instead.
#   - A100-80GB (sm_80):     Use gemma4-dense-vllm. INT4 here wastes the 80 GB.
#   - H100/H200 (sm_90):     Use gemma4-dense-vllm. INT4 here wastes HBM3.

# v0.22.0 builds on v0.20.0's gemma4 tool-call parser thread-safety fixes
# (#38992, #38909, #39114 — silent JSON corruption, infinite <pad> loops),
# required for INT4 + concurrent agent loops, and adds the
# streaming-after-tool-results reasoning leak fix (#39885) and tool-call
# float-corruption fix (#42128). Reasoning still needs client-side
# skip_special_tokens=false (vLLM #38855, open) — see entrypoint.sh.
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

# Ada (L40S/4090) only — strip Ampere/Hopper/Blackwell from CUDA arch list
# to shrink the kernel cache. The image refuses to start on non-Ada GPUs anyway.
ENV TORCH_CUDA_ARCH_LIST="8.9+PTX"

WORKDIR /app

RUN python3 -c "import vllm; print(f'vLLM version: {vllm.__version__}')"

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# start-period=900s: AWQ checkpoint load + Triton kernel warmup is ~5-10 min on
# L40S (PCIe 4.0 GDDR6 transfer + JIT compile of the AWQ Marlin path). Without
# this, k8s/RunPod readiness probes SIGKILL mid-warmup, locking the container
# in restart loops. Drops to ~1-2 min when HF cache + VLLM_CONFIG_ROOT are warm.
HEALTHCHECK --interval=30s --timeout=10s --start-period=900s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000 22

ENTRYPOINT ["/app/entrypoint.sh"]
