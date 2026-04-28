# llama.cpp Vulkan image for Gemma 4 31B dense on AMD Strix Halo.
#
# Sibling of gemma4-moe-strix-llamacpp — same backend, different SKU. Pick this
# image when reasoning depth matters more than throughput; pick the MoE sibling
# for ~2× decode and half the per-session KV.
#
# Target hardware: Ryzen AI Max+ 395 / Radeon 8060S iGPU = RDNA 3.5 (gfx1151).
# Backend:         Vulkan (RADV) — vLLM-ROCm rejected per the framework gate
#                  (see ../Hosting Gemma 4 on Strix Halo_*.pdf).
#
# Build:
#   podman build -t gemma4-dense-strix-llamacpp:latest .
#
# Run (homelab):
#   podman run --rm \
#       --device /dev/dri \
#       -p 8080:8080 \
#       -v ./models:/models \
#       -v radv-cache:/root/.cache/mesa_shader_cache \
#       gemma4-dense-strix-llamacpp:latest
#
# Image base: same b8765 server-vulkan tag as the MoE container — both PRs
# #19625 (scalar FA + Wave32) and #20551 (graphics queue) are required for
# the ~25% Strix Halo throughput uplift.
FROM ghcr.io/ggml-org/llama.cpp:server-vulkan-b8765

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Force RADV; AMDVLK halves prefill on Strix Halo if it ends up first in the
# ICD search path (hogeheer499 guide, Mar 2026).
ENV AMD_VULKAN_ICD=RADV
ENV VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/radeon_icd.json

# Required to enable RDNA 3.5 codepaths in llama.cpp's Vulkan backend.
ENV GGML_VK_ALLOW_EXPERIMENTAL_RDNA35=1

WORKDIR /app

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# 31B dense has 60 layers (vs 30 on the MoE) — first-boot pipeline compile
# touches roughly twice as many shader specializations. 5 min start-period
# tolerates the cold-cache case; warm reboots land near 45-60 s.
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
