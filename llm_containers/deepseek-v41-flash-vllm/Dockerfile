# vLLM image for DeepSeek-V4.1-Flash on one DGX Station GB300 (arm64, Grace + Blackwell Ultra)
#
# Build (on the Station; the base is arm64-only here):
#   docker build -t deepseek-v41-flash-vllm:0.34.0-1 .
# Run: see deepseek-vllm.service (systemd) and README.md.
#
# Model: deepseek-ai/DeepSeek-V4.1-Flash (0909) or -1017 (weight refresh), MIT, not gated.
#   - 552B MoE backbone + 196.6B params of Engram lookup tables, ~476 GiB on disk (FP8, FP4 experts)
#   - Does NOT fit the 252 GB HBM: the Engram tables (~189 GiB) and part of the routed experts
#     (--cpu-offload-gb 60) live in pinned Grace memory and are read over NVLink-C2C (UVA offload)
#
# Thin layer on purpose:
#   - The base is the official release, pinned by digest (v0.34.0, torch 2.14.0+cu130). No auto-update:
#     a new image repeats the 75-90 min FlashInfer autotune and invalidates the torch.compile cache.
#   - No weights in the image; they are mounted read-only at /models.
#   - No flags in the image; vllm serve reads them from /etc/deepseek-vllm/serve.args (one argument per
#     line), so a tuning step changes one mounted file, not the image.
#   - No SSH server (unlike muse-glimmer-vllm): the host is ours, use docker exec.
ARG BASE=vllm/vllm-openai@sha256:20fdb089454afbb324efa4110a2c025ff7b27cc1a086e07aa580b18df8ad1ce1
FROM ${BASE}
ARG BASE
LABEL org.opencontainers.image.title="deepseek-v41-flash-vllm" \
      org.opencontainers.image.base.name="${BASE}" \
      org.opencontainers.image.description="vLLM 0.34.0 for DeepSeek-V4.1-Flash on one DGX Station GB300"

# Defaults from the vLLM recipe for dgx_station_gb300; the env file can override them.
# Offline: the weights are local, nothing should be fetched at start.
ENV VLLM_ENGINE_READY_TIMEOUT_S=3600 \
    VLLM_USE_RUST_FRONTEND=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    VLLM_NO_USAGE_STATS=1 \
    DO_NOT_TRACK=1

COPY entrypoint.sh /usr/local/bin/deepseek-entrypoint
RUN chmod 0755 /usr/local/bin/deepseek-entrypoint

# The base image ships ENTRYPOINT ["vllm","serve"]; override it.
ENTRYPOINT ["/usr/local/bin/deepseek-entrypoint"]
