# llama.cpp Vulkan image for Gemma 4 26B-A4B (MoE) on AMD Strix Halo.
#
# Target hardware: Ryzen AI Max+ 395 / Radeon 8060S iGPU = RDNA 3.5 (gfx1151).
# Backend:         Vulkan (RADV) — chosen over vLLM-ROCm per the framework gate
#                  in "Hosting Gemma 4 on Strix Halo" (Apr 2026). vLLM-ROCm
#                  on gfx1151 currently requires --enforce-eager (#32180) and
#                  has SWA disabled in V1 on RDNA (#19367) — both fatal for
#                  Gemma 4's interleaved local-sliding + global stack.
#
# Build:
#   podman build -t gemma4-moe-strix-llamacpp:latest .
#
# Run (homelab):
#   podman run --rm \
#       --device /dev/dri \
#       --device /dev/kfd \
#       -p 8080:8080 \
#       -v ./models:/models \
#       -v radv-cache:/root/.cache/mesa_shader_cache \
#       gemma4-moe-strix-llamacpp:latest
#
# Image base: upstream prebuilt server-vulkan image, pinned to the latest
# build verified at the time of writing. Bump the build number as new fixes
# for RDNA 3.5 / gfx1151 land — track ggml-org/llama.cpp release notes.
# Use `:server-vulkan` (unpinned) instead if you want auto-updates at the
# cost of build reproducibility.
FROM ghcr.io/ggml-org/llama.cpp:server-vulkan-b6653

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# RADV is the right Vulkan ICD on Strix Halo. AMDVLK silently hijacks dispatch
# and roughly halves prefill speed (per hogeheer499 guide, Mar 2026). If the
# upstream image bundles AMDVLK, force RADV via env.
ENV AMD_VULKAN_ICD=RADV
# RADV ICD filename in the upstream :server-vulkan image carries an .x86_64
# arch suffix (radeon_icd.x86_64.json), not the bare radeon_icd.json. Pinning
# the wrong path makes the Vulkan loader find no ICDs and silently fall back
# to CPU. Verified against ghcr.io/ggml-org/llama.cpp:server-vulkan-b6653.
ENV VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json

# Required to enable RDNA 3.5 codepaths in llama.cpp's Vulkan backend on
# gfx1151. Will be removed once RDNA 3.5 is no longer experimental upstream.
ENV GGML_VK_ALLOW_EXPERIMENTAL_RDNA35=1

WORKDIR /app

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# start-period covers first-boot Vulkan pipeline compile (Triton-equivalent on
# RADV) into ~/.cache/mesa_shader_cache. Cold first boot can hit 1-2 minutes;
# warm reboots with the cache volume populated land near 30s.
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
