#!/bin/bash
# Host-side driver for one tuning step (run as root while deepseek-vllm is up and idle).
# Records server facts and telemetry, runs bench-client.sh in a throwaway container, writes summary.md.
# Usage: bench-step.sh <label> ["sweep prefill warm needle"]     Results: /srv/vllm/runs/<UTC stamp>-bench-<label>/
set -u
export LC_ALL=C
LABEL=$1; SC=${2:-"sweep prefill warm needle"}
B=/opt/deepseek-vllm/bench
OUT=/srv/vllm/runs/$(date -u +%Y%m%dT%H%M%SZ)-bench-$LABEL
mkdir -p "$OUT/json"
log(){ echo "$(date -u +%FT%TZ) $*" | tee -a "$OUT/run.log"; }
# Another engine: BENCH_URL=http://<address>:<port> SERVER_CONTAINER=<docker name> (defaults: the stock unit).
URL=${BENCH_URL:-http://127.0.0.1:8000}; SRV=${SERVER_CONTAINER:-deepseek-vllm}
curl -sf -m 5 $URL/health >/dev/null || { log "server at $URL not healthy - abort"; exit 1; }
srvlog(){ if [ "$SRV" = deepseek-vllm ]; then journalctl -u deepseek-vllm --no-pager -o cat; else docker logs "$SRV" 2>&1; fi; }

if [ "$SRV" = deepseek-vllm ]; then cp /etc/deepseek-vllm/serve.args /etc/deepseek-vllm/env "$OUT/"
else docker inspect "$SRV" --format '{{range .Args}}{{println .}}{{end}}' > "$OUT/serve.args"
     docker inspect "$SRV" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(DSV41_|DERPSEEK_|DS41_|MEGA_|VLLM_|PYTORCH_)' | sort > "$OUT/env"; fi
{ grep -i coherent /proc/driver/nvidia/params
  docker inspect "$SRV" --format 'container={{.Name}} image={{.Config.Image}} started={{.State.StartedAt}}'
  srvlog | grep -E 'GPU KV cache size|Maximum concurrency|Available KV cache|Model loading took|CPU offloaded|Engram table offloaded|init engine|Graph capturing finished|Application startup complete' | tail -10
  free -g | head -2; nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
} > "$OUT/server-facts.txt" 2>&1
KV=$(srvlog | grep -oE 'GPU KV cache size: [0-9,]+ tokens' | tail -1 | grep -oE '[0-9][0-9,]*' | tr -d ,)
log "label=$LABEL scenarios='$SC' kv_tokens=${KV:-unknown}"

nvidia-smi --query-gpu=timestamp,power.draw,temperature.gpu,temperature.memory,utilization.gpu,utilization.memory,memory.used,clocks.sm,clocks.mem,clocks_event_reasons.active --format=csv -lms 1000 > "$OUT/gpu.csv" 2>&1 &
SMI=$!
( while true; do
    printf '%s ' "$(date -u +%FT%TZ)"
    curl -s -m 3 $URL/metrics | grep -E '^vllm:(kv_cache_usage_perc|gpu_cache_usage_perc|num_requests_running|num_requests_waiting|num_preemptions_total|prefix_cache_hits_total|prefix_cache_queries_total)' | sed -E 's/\{[^}]*\}//' | tr '\n' ' '
    echo; sleep 2
  done ) > "$OUT/vllm-metrics.log" 2>&1 &
MET=$!
trap 'kill $SMI $MET 2>/dev/null' EXIT

# The agents scenario reads a fixed corpus of source code; build it once from the pinned image.
CORPUS=/srv/vllm/bench-corpus/v1
PINNED=vllm/vllm-openai@sha256:20fdb089454afbb324efa4110a2c025ff7b27cc1a086e07aa580b18df8ad1ce1
case " $SC " in *" agents "*|*" burst "*)
  if [ ! -f $CORPUS/MANIFEST.json ]; then
    log "building the corpus in $CORPUS"; mkdir -p $CORPUS
    docker run --rm --name claude-corpus --network none -v /srv/models:/models:ro -v $B:/bench:ro -v $CORPUS:/corpus \
      -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e IMAGE_REF=$PINNED \
      --entrypoint python3 $PINNED /bench/make_corpus.py --tokenizer /models/DeepSeek-V4.1-Flash --out /corpus >> "$OUT/corpus.log" 2>&1
    log "corpus build rc=$? : $(tail -1 "$OUT/corpus.log")"
  fi
  cp $CORPUS/MANIFEST.json "$OUT/corpus-manifest.json" 2>/dev/null ;;
esac

log "idle 30 s"; sleep 30
log "client start"
docker run --rm --name claude-bench --network host \
  -v /srv/models:/models:ro -v $B:/bench:ro -v "$OUT":/results -v $CORPUS:/corpus:ro \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e KV_TOKENS="${KV:-}" -e BENCH_URL=$URL \
  -e AGENT_CONTEXTS="${AGENT_CONTEXTS:-}" -e AGENT_COUNTS="${AGENT_COUNTS:-}" -e AGENT_TURNS="${AGENT_TURNS:-}" \
  -e AGENT_REPEATS="${AGENT_REPEATS:-}" -e BURST_CONTEXT="${BURST_CONTEXT:-}" -e BURST_AGENTS="${BURST_AGENTS:-}" \
  --entrypoint bash vllm/vllm-openai:latest /bench/bench-client.sh /results "$SC" >> "$OUT/bench.log" 2>&1
log "client exited rc=$?"
sleep 20
python3 $B/analyze_bench.py "$OUT" > /dev/null 2>&1 || log "analyze_bench failed"
python3 $B/summarize_extra.py "$OUT" >> "$OUT/summary.md" 2>&1 || log "summarize_extra failed"
chown -R 1000:1000 "$OUT"
log "done: $OUT/summary.md"
