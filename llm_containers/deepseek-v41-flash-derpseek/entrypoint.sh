#!/bin/bash
# Starts the engine the way derpseek's launcher (engine/derpseek.sh, commit 8afa271) does: it reads the profile,
# sets the same environment and runs the same "vllm serve" command under numactl.
#
# Settings come from the container's environment (docker --env-file). The launcher's own knobs keep their names:
#   DS_PROFILE=exact            profile file in /derpseek/profiles
#   ROWMAP=...                  expert layout, a file in /derpseek/rowmaps (default: the profile's)
#   DERPSEEK_STAGE=0|1          prompt staging (default: the profile's, 1)
#   GPU_MEM_UTIL=0.98           vLLM's share of GPU memory (default: the profile's)
#   KV_CACHE_BYTES=...          size of the KV cache in bytes instead of "whatever is left"
#   MAXLEN=1048576              --max-model-len
#   MAX_NUM_SEQS=32  MAX_BATCHED=8192  LONG_PREFILL=6144  CAPTURE_SIZES="..."  EXTRA_CAPTURE_SIZES="..."
#   KV_OFFLOAD_GB=...  PORT=8112
#   DS_SERVED_NAMES="a b c"     names the model answers to (default: the launcher's four)
#   DS_DRY_RUN=1                print the command and the environment and exit (used to check the image)
set -euo pipefail
PROFILE=${DS_PROFILE:-exact}
P=/derpseek/profiles/$PROFILE.env
[ -f "$P" ] || { echo "ds-entrypoint: no profile $P" >&2; exit 64; }
set -a; . "$P"; set +a
[ -f "/derpseek/rowmaps/$ROWMAP" ] || { echo "ds-entrypoint: no rowmap /derpseek/rowmaps/$ROWMAP" >&2; exit 64; }
[ -d /fastpath ] && [ -f /w/mega_peer_hook.py ] || { echo "ds-entrypoint: /fastpath or /w is missing" >&2; exit 64; }
[ "${DS_DRY_RUN:-0}" = 1 ] || [ -f /model/model.safetensors.index.json ] || { echo "ds-entrypoint: no checkpoint at /model" >&2; exit 64; }
export MEGA_ROWMAP=/derpseek/rowmaps/$ROWMAP DS41_FASTPATH=$FASTPATH

NAMES=${DS_SERVED_NAMES:-deepseek-v4.1-flash deepseek DeepSeek-V4.1-Flash derpseek}
args=(vllm serve --model /model --served-model-name $NAMES --trust-remote-code
  --tensor-parallel-size 1 --moe-backend deep_gemm_mega_moe --enable-expert-parallel
  --worker-cls derpseek_engine.worker.DerpseekWorker
  --engram-config '{"cpu_offload": true, "use_thp": true}' --max-model-len "${MAXLEN:-1048576}" --max-num-seqs "${MAX_NUM_SEQS:-32}"
  --max-num-batched-tokens "${MAX_BATCHED:-8192}" --gpu-memory-utilization "${GPU_MEM_UTIL:-0.95}" --kv-cache-dtype nvfp4_ds_mla --block-size 128
  --attention-config '{"indexer_kv_dtype": "mxfp4", "indexer_sparse_logits": true}'
  --kernel-config '{"enable_flashinfer_autotune": false}'
  --tokenizer-mode deepseek_v41 --tool-call-parser deepseek_v41 --reasoning-parser deepseek_v41 --enable-auto-tool-choice
  --long-prefill-token-threshold "${LONG_PREFILL:-6144}" --enable-prefix-caching
  --speculative-config "$SPEC"
  --cudagraph-capture-sizes ${CAPTURE_SIZES:-1 2 4 6 8 12 16 18 20 24 28 32 36 40 48 56 64 72 80 96 128 144 160 192 224 256} ${EXTRA_CAPTURE_SIZES:-})
[ -z "${KV_OFFLOAD_GB:-}" ] || args+=(--kv-offloading-backend native --kv-offloading-size "$KV_OFFLOAD_GB")
[ -z "${KV_CACHE_BYTES:-}" ] || args+=(--kv-cache-memory-bytes "$KV_CACHE_BYTES")
args+=(--override-generation-config '{"temperature": 1.0, "top_p": 0.95}'
  --default-chat-template-kwargs '{"reasoning_effort":"high"}'
  --host 0.0.0.0 --port "${PORT:-8112}")

echo "ds-entrypoint: $(cat /opt/ds/BUILD-INFO 2>/dev/null | tr '\n' ' ')"
echo "ds-entrypoint: profile=$PROFILE rowmap=$ROWMAP stage=${DERPSEEK_STAGE:-} gpu_mem_util=${GPU_MEM_UTIL:-0.95} kv_cache_bytes=${KV_CACHE_BYTES:-auto} maxlen=${MAXLEN:-1048576} max_num_seqs=${MAX_NUM_SEQS:-32}"
DRY=${DS_DRY_RUN:-0}
# The launcher hands the engine only the DSV41_*, DERPSEEK_* and fixed variables, not its own knobs.
unset ROWMAP FASTPATH SPEC GPU_MEM_UTIL MAXLEN MAX_NUM_SEQS MAX_BATCHED LONG_PREFILL CAPTURE_SIZES EXTRA_CAPTURE_SIZES \
  KV_OFFLOAD_GB KV_CACHE_BYTES PORT DS_PROFILE DS_SERVED_NAMES DS_DRY_RUN NAMES PROFILE P
if [ "$DRY" = 1 ]; then
  printf 'ARG\t%s\n' /usr/bin/numactl --membind=0 "${args[@]}"
  env | sort | sed 's/^/ENV\t/'
  exit 0
fi
echo "ds-entrypoint: numactl --membind=0 ${args[*]}"
exec /usr/bin/numactl --membind=0 "${args[@]}"
