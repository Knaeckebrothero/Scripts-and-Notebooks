# llama.cpp Vulkan image for Qwen3-Embedding-8B on AMD Strix Halo.
#
# Target hardware: Ryzen AI Max+ 395 / Radeon 8060S iGPU = RDNA 3.5 (gfx1151).
# Backend:         Vulkan (RADV) — same rationale as the gemma4-moe-strix
#                  image (vLLM-ROCm on gfx1151 is unviable for production).
#                  Embeddings benefit less from GPU than chat (no KV cache,
#                  short prompts), but llama-server supports them natively
#                  via --embeddings + --pooling so we get one consistent stack.
#
# Build:
#   podman build -t qwen3-embedding-strix-llamacpp:latest .
#
# Run:
#   podman run --rm \
#       --device /dev/dri --device /dev/kfd \
#       -p 8080:8080 \
#       -v ./models:/models \
#       -v radv-cache:/root/.cache/mesa_shader_cache \
#       qwen3-embedding-strix-llamacpp:latest
FROM ghcr.io/ggml-org/llama.cpp:server-vulkan-b8967

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# RADV is the right Vulkan ICD on Strix Halo. AMDVLK silently halves prefill
# speed if it ever shows up. Force RADV by env.
ENV AMD_VULKAN_ICD=RADV
ENV VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/radeon_icd.json
ENV GGML_VK_ALLOW_EXPERIMENTAL_RDNA35=1

WORKDIR /app

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
