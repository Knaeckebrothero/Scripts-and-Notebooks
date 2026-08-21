#!/bin/bash
# vllm-cache-guard.sh — wipe the persisted torch.compile cache when the image changes.
#
# WHY THIS EXISTS
# ---------------
# The pod mounts a persistent VLLM_CACHE_ROOT so restarts skip the ~58s
# torch.compile step. That cache is only valid for the exact vLLM build that
# wrote it. From the container README:
#
#   "WIPE THIS DIR whenever the image is bumped — stale graphs fail silently."
#
# "Silently" is the problem: there is no startup error, just wrong kernels.
# With `AutoUpdate=registry` + podman-auto-update.timer enabled, the image can
# be swapped unattended overnight, and nothing would otherwise wipe the cache.
#
# So: stamp the image ID into the cache dir, and clear the compiled graphs
# whenever it changes. Cheap (one recompile) versus debugging silent kernel
# corruption at 3am.
#
# Usage (from the Quadlet [Service] section):
#   ExecStartPre=/home/llmprod/bin/vllm-cache-guard.sh <image-ref> <cache-dir>
#
# GOTCHA (cost us one silent miss on 2026-08-21): adding ExecStartPre to a
# .container file does nothing until `systemctl --user daemon-reload` -- Quadlet
# regenerates the .service from the .container at reload, not at restart. Worse,
# `AutoUpdate=registry` is baked into the container as the
# io.containers.autoupdate label at CREATE time, so a unit that predates the
# flag stays invisible to `podman auto-update` until it is restarted once.
# After editing either, run:
#     systemctl --user daemon-reload && systemctl --user restart <unit>
# then VERIFY, don't assume:
#     systemctl --user cat <unit> | grep ExecStartPre
#     podman auto-update --dry-run          # unit must be listed at all
#     diff <(cat <cache-dir>/.image-id) <(podman inspect <ctr> --format '{{.Image}}')
#
# Conservative by design: it keys on the image id, so a rebuild that only
# changes entrypoint.sh still forces a ~58s recompile even though the vLLM
# build is identical. That is the safe direction to be wrong in.
set -euo pipefail

IMAGE="${1:?usage: vllm-cache-guard.sh <image-ref> <cache-dir>}"
CACHE="${2:?usage: vllm-cache-guard.sh <image-ref> <cache-dir>}"
STAMP="${CACHE}/.image-id"

mkdir -p "${CACHE}"

CURRENT="$(podman image inspect --format '{{.Id}}' "${IMAGE}" 2>/dev/null || echo unknown)"
PREVIOUS="$(cat "${STAMP}" 2>/dev/null || echo none)"

if [ "${CURRENT}" = "unknown" ]; then
    # Image not present yet (first boot pulls it). Nothing cached to invalidate.
    echo "vllm-cache-guard: image ${IMAGE} not resolvable yet; leaving cache untouched"
    exit 0
fi

if [ "${CURRENT}" != "${PREVIOUS}" ]; then
    echo "vllm-cache-guard: image changed"
    echo "  was: ${PREVIOUS}"
    echo "  now: ${CURRENT}"
    echo "vllm-cache-guard: clearing torch.compile cache under ${CACHE}"
    rm -rf "${CACHE}/torch_compile_cache" "${CACHE}/modelinfos"
    printf '%s\n' "${CURRENT}" > "${STAMP}"
else
    echo "vllm-cache-guard: image unchanged (${CURRENT:0:19}...); keeping compile cache"
fi
