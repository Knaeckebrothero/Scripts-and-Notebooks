#!/bin/bash
# vllm-cache-guard.sh: wipe the persisted torch.compile cache when the image changes.
# Docker variant of llm_containers/muse-glimmer-vllm/scripts/vllm-cache-guard.sh (see there for the why:
# a stale compile cache fails silently, with no startup error, just wrong kernels).
# Usage (ExecStartPre): vllm-cache-guard.sh <image-ref> <host dir mounted as /root/.cache/vllm>
set -euo pipefail
IMAGE="${1:?usage: vllm-cache-guard.sh <image-ref> <cache-dir>}"
CACHE="${2:?usage: vllm-cache-guard.sh <image-ref> <cache-dir>}"
STAMP="$CACHE/.image-id"
mkdir -p "$CACHE"
CUR=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || echo unknown)
PREV=$(cat "$STAMP" 2>/dev/null || echo none)
if [ "$CUR" = unknown ]; then echo "vllm-cache-guard: $IMAGE not present; cache untouched"; exit 0; fi
if [ "$PREV" = none ]; then echo "vllm-cache-guard: first stamp, keeping cache"; printf '%s\n' "$CUR" > "$STAMP"; exit 0; fi
if [ "$CUR" != "$PREV" ]; then
  echo "vllm-cache-guard: image changed ($PREV -> $CUR): clearing torch.compile cache"
  rm -rf "$CACHE/torch_compile_cache" "$CACHE/modelinfos"
  printf '%s\n' "$CUR" > "$STAMP"
else
  echo "vllm-cache-guard: image unchanged, keeping compile cache"
fi
