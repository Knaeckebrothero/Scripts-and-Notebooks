#!/bin/bash
# Starts vllm serve with the arguments in $VLLM_ARGS_FILE (default /etc/deepseek-vllm/serve.args):
# one argument per line, so JSON values need no quoting; blank lines and lines starting with # are ignored.
# Arguments passed to the container are appended (for one-off tests).
set -euo pipefail
ARGS_FILE="${VLLM_ARGS_FILE:-/etc/deepseek-vllm/serve.args}"
[ -r "$ARGS_FILE" ] || { echo "entrypoint: cannot read $ARGS_FILE" >&2; exit 64; }
mapfile -t args < <(sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' "$ARGS_FILE" | grep -vE '^(#|$)')
args+=("$@")
echo "entrypoint: image=${IMAGE_REF:-unknown} args_file=${ARGS_FILE}"
env | grep -E '^(VLLM_|HF_HUB_OFFLINE|TRANSFORMERS_OFFLINE|NVIDIA_VISIBLE_DEVICES)' | sort | sed 's/^/entrypoint: env /'
echo "entrypoint: vllm serve ${args[*]}"
exec vllm serve "${args[@]}"
