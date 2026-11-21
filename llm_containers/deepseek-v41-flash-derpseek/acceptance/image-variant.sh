#!/bin/bash
# One item of an acceptance run: (re)start the engine service (deepseek-derpseek.service, our image) with optional
# test overrides, start the forwarder, then run API checks, a benchmark set and optionally the stress test.
# Usage: image-variant.sh <label> image [VAR=value ...]        (called by accept-run.sh for each queue line)
# Engine overrides for this start only (written to /run/deepseek-derpseek/override.env):
#   MAXLEN  KV_CACHE_BYTES  ROWMAP  DERPSEEK_STAGE  GPU_MEM_UTIL  MAX_NUM_SEQS  VLLM_DSV41_STRUCTURAL_DSML
# What runs:
#   CHECKS=1        ds_check.py through the forwarder (0 = skip)
#   VIA=forwarder   benchmarks and stress go through the forwarder; engine = straight to the engine;
#                   both = forwarder, plus the agent cells once more straight to the engine (<label>-agents-direct)
#   SET=short|full|grid, CTX, COUNTS, GRID_*, PREWARM_MAX, BURST_CTX: as in derpseek-variant.sh.   BURST=1: in a
#                   short/full set, also 8 agents starting a new BURST_CTX context at the same moment
#   STRESS_MIN=720  the dsv41 project's stress test for that many minutes (8 tool-calling agents; STRESS_CTX is
#                   the context size at which an agent starts over), then the agent cells and passkey test again
# Log: /srv/vllm/queue/queue.log     Results: /srv/vllm/runs/*-<label>*   (tables: night-report.py, queue-report.py)
set -u
export LC_ALL=C
LABEL=$1; PROFILE=$2; shift 2
SETTINGS="$*"
for kv in "$@"; do export "$kv"; done
D=/opt/deepseek-vllm/derpseek; B=/opt/deepseek-vllm/bench/bench-step.sh; Q=/srv/vllm/queue; mkdir -p $Q
U=deepseek-derpseek.service; F=deepseek-forwarder.service; CN=deepseek-derpseek
ENGINE=http://172.31.250.2:8112; FWD=http://127.0.0.1:8010; FWD_BR=http://172.31.250.1:8010
SINCE=$(date '+%Y-%m-%d %H:%M:%S')
qlog(){ echo "$(date -u +%FT%TZ) [$LABEL] $*" | tee -a $Q/queue.log; }
state(){ docker inspect -f '{{.State.Status}}' $CN 2>/dev/null || echo gone; }
srvlog(){ if docker inspect $CN >/dev/null 2>&1; then docker logs $CN 2>&1; else journalctl -u $U --since "$SINCE" -o cat --no-pager 2>/dev/null; fi; }
alloc(){ srvlog | grep -c 'memory allocation failed with OOM'; }
fatal(){ local l; l=$(srvlog | grep -E 'OutOfMemoryError' | tail -1)
         [ -n "$l" ] || l=$(srvlog | grep -E ' ERROR |Error|ds-ready:' | tail -1)
         echo "${l:-no error line in the log}" | sed -E 's/^.*\] //' | cut -c1-170; }
fm(){ curl -s -m 3 $FWD/metrics | awk -v n="$1" '$1 == n {print $2}'; }
SET=${SET:-short}; VIA=${VIA:-forwarder}

qlog "start: profile=$PROFILE settings: ${SETTINGS:-none}"
RUN=/srv/vllm/runs/$(date -u +%Y%m%dT%H%M%SZ)-$LABEL; mkdir -p "$RUN"
mkdir -p /run/deepseek-derpseek; : > /run/deepseek-derpseek/override.env
for v in MAXLEN KV_CACHE_BYTES ROWMAP DERPSEEK_STAGE GPU_MEM_UTIL MAX_NUM_SEQS VLLM_DSV41_STRUCTURAL_DSML; do
  [ -n "${!v:-}" ] && echo "$v=${!v}" >> /run/deepseek-derpseek/override.env
done
cp /run/deepseek-derpseek/override.env "$RUN/override.env"; cp /etc/deepseek-derpseek/*.env "$RUN/" 2>/dev/null
# During a test the unit must not restart by itself: a death has to show as a death.
mkdir -p /run/systemd/system/$U.d; printf '[Service]\nRestart=no\n' > /run/systemd/system/$U.d/accept.conf; systemctl daemon-reload
systemctl stop $F $U deepseek-vllm 2>/dev/null; systemctl reset-failed $U 2>/dev/null
timeout 5700 systemctl start $U; rc=$?                      # returns when the engine is ready and warmed, or has failed
journalctl -u $U --since "$SINCE" -o cat --no-pager > "$RUN/journal-start.log" 2>&1
grep -E '^ds-(ready|net|entrypoint):' "$RUN/journal-start.log" > "$RUN/launcher.log"
docker logs $CN > "$RUN/server.log" 2>&1 || cp "$RUN/journal-start.log" "$RUN/server.log"
docker inspect $CN > "$RUN/docker-inspect.json" 2>/dev/null
if [ $rc -ne 0 ] || [ "$(state)" != running ]; then
  qlog "START FAILED rc=$rc: $(fatal) (see $RUN/server.log)"
  systemctl stop $U 2>/dev/null
  exit 1
fi
systemctl start $F
BR=br-$(docker network inspect ds-isolated --format '{{.Id}}' | cut -c1-12)
# The stress test's container sits on the isolated network; let it reach the forwarder on this host, nothing else.
iptables -C INPUT -i "$BR" -p tcp -d 172.31.250.1 --dport 8010 -j ACCEPT 2>/dev/null || iptables -I INPUT -i "$BR" -p tcp -d 172.31.250.1 --dport 8010 -j ACCEPT
sleep 2
WIN=$(curl -s -m 10 $FWD/v1/models | grep -oE '"max_model_len": ?[0-9]+' | grep -oE '[0-9]+' | head -1)
qlog "ready: $(grep -oE 'GPU KV cache size: [0-9,]+ tokens' "$RUN/server.log" | tail -1); $(grep -oE 'ready on :[0-9]+ after [0-9]+ s' "$RUN/launcher.log" | tail -1); window=${WIN:-?}; $(grep -oE 'Available KV cache memory: [0-9.]+ GiB' "$RUN/server.log" | tail -1); forwarder=$(systemctl is-active $F)"
KV=$(grep -oE 'GPU KV cache size: [0-9,]+ tokens' "$RUN/server.log" | tail -1 | grep -oE '[0-9][0-9,]*' | head -1 | tr -d ,)
[ -n "$KV" ] || KV=2600000

if [ "${CHECKS:-1}" = 1 ]; then
  python3 -I $D/ds_check.py --base $FWD --slots 8 ${MAXLEN:+--window $MAXLEN} --out "$RUN/checks.txt" >/dev/null 2>"$RUN/checks.err"
  qlog "checks: $(tail -1 "$RUN/checks.txt" 2>/dev/null || echo 'no result') $(grep -E '^(FAIL|INFO)' "$RUN/checks.txt" 2>/dev/null | cut -c1-150 | tr '\n' ';' | cut -c1-640)"
fi

case "$VIA" in engine) export BENCH_URL=$ENGINE; SURL=$ENGINE ;; *) export BENCH_URL=$FWD; SURL=$FWD_BR ;; esac
export SERVER_CONTAINER=$CN
GMAX=${GRID_MAX:-10}; REPS=${GRID_REPEATS:-3}
PW=${PREWARM_MAX:-8}; [ "$SET" = grid ] && [ -z "${PREWARM_MAX:-}" ] && PW=$GMAX
AGENT_CONTEXTS=20000 AGENT_COUNTS=$(seq -s, 1 "$PW") AGENT_TURNS=2 $B "$LABEL-prewarm" agents >/dev/null 2>&1

CTXS=${CTX:-118000,385000}; CTXS=${CTXS//,/ }
needle(){ $B "$LABEL-$1" needle >/dev/null 2>&1
          local d; d=$(ls -d /srv/vllm/runs/*-bench-"$LABEL-$1" 2>/dev/null | tail -1)
          local p; p=$(python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1])).get("needle",[]); print(str(sum(1 for x in r if x.get("pass"))) + "/" + str(len(r)))' "$d/needle.json" 2>/dev/null)
          qlog "passkey ($1): ${p:-no result}; container=$(state) alloc-failures=$(alloc)"; }
case "$SET" in
  grid)
    GC=${GRID_CTX:-128000,256000,385000,512000}
    for L in ${GC//,/ }; do
      n=$(( KV / L + 1 )); [ $n -gt "$GMAX" ] && n=$GMAX
      AGENT_CONTEXTS=$L AGENT_COUNTS=$(seq -s, 1 $n) AGENT_REPEATS=$REPS $B "$LABEL-grid-$L" agents >/dev/null 2>&1
      qlog "grid $L: agents 1-$n, $REPS times; container=$(state) alloc-failures=$(alloc)"
    done ;;
  *)
    [ "$SET" = full ] && $B "$LABEL-sweep" sweep >/dev/null 2>&1
    AGENT_CONTEXTS="$CTXS" AGENT_COUNTS="${COUNTS:-1,5,8}" $B "$LABEL-agents" agents >/dev/null 2>&1
    qlog "agents $CTXS x ${COUNTS:-1,5,8} via $VIA: $(grep -h 'SUMMARY' /srv/vllm/runs/*-bench-"$LABEL-agents"/bench.log 2>/dev/null | tail -1 | cut -c1-200); container=$(state) alloc-failures=$(alloc)"
    if [ "$VIA" = both ]; then
      BENCH_URL=$ENGINE AGENT_CONTEXTS="$CTXS" AGENT_COUNTS="${COUNTS:-1,5,8}" $B "$LABEL-agents-direct" agents >/dev/null 2>&1
      qlog "agents straight to the engine: $(grep -h 'SUMMARY' /srv/vllm/runs/*-bench-"$LABEL-agents-direct"/bench.log 2>/dev/null | tail -1 | cut -c1-200)"
    fi
    [ "$SET" = full ] && AGENT_CONTEXTS=1000000 AGENT_COUNTS=1,2 $B "$LABEL-agents-1m" agents >/dev/null 2>&1 ;;
esac
if [ "$SET" = grid ] || [ "${BURST:-0}" = 1 ]; then
  BL=${BURST_CTX:-385000}; bn=$(( KV / BL )); [ $bn -gt 8 ] && bn=8
  if [ $bn -ge 2 ]; then
    BURST_CONTEXT=$BL BURST_AGENTS=$bn $B "$LABEL-burst" burst >/dev/null 2>&1
    qlog "burst $BL: $bn agents at once: $(grep -h 'SUMMARY burst' /srv/vllm/runs/*-bench-"$LABEL-burst"/bench.log 2>/dev/null | tail -1 | cut -c1-220); container=$(state) alloc-failures=$(alloc)"
  fi
fi
needle needle

if [ -n "${STRESS_MIN:-}" ]; then
  OUT=/srv/vllm/runs/$(date -u +%Y%m%dT%H%M%SZ)-stress-$LABEL; mkdir -p "$OUT"
  qlog "stress start: $STRESS_MIN min, 8 agents, contexts up to ${STRESS_CTX:-200000}, via $VIA; container=$(state) alloc-failures=$(alloc)"
  nvidia-smi --query-gpu=timestamp,power.draw,temperature.gpu,temperature.memory,utilization.gpu,memory.used,clocks.sm,clocks_event_reasons.active \
    --format=csv -lms 2000 > "$OUT/gpu.csv" 2>&1 &
  SMI=$!
  ( while true; do   # one line every 5 minutes: is the server alive, allocation failures, graphs, cache use, forwarder counters
      echo "$(date -u +%FT%TZ) container=$(state) alloc-failures=$(alloc) graphs=$(srvlog | grep -c 'DSV41_WHOLE_STEP captured') gpu[MiB,W,C]=$(nvidia-smi --query-gpu=memory.used,power.draw,temperature.gpu --format=csv,noheader,nounits | tr -d ' ') $(curl -s -m 3 $FWD/metrics | grep -E '^(vllm:(kv_cache_usage_perc|num_requests_running|num_requests_waiting|num_preemptions_total)|dsfwd_(requests_active|requests_waiting|client_aborts_total|responses_server_error_total|upstream_unreachable_total))' | sed -E 's/\{[^}]*\}//' | tr '\n' ' ')"
      sleep 300
    done ) > "$OUT/soak-samples.log" 2>&1 &
  SAM=$!
  docker rm -f claude-derpseek-stress >/dev/null 2>&1
  S0=$(date +%s); died=""
  docker run --rm --name claude-derpseek-stress --network ds-isolated -v /srv/src/dsv41:/src:ro -v "$OUT":/out --entrypoint python3 \
    vllm/vllm-openai:v0.32.0-aarch64 -I /src/bench/agent_stress.py --url $SURL/v1 --agents 8 --minutes "$STRESS_MIN" \
    --max-context "${STRESS_CTX:-200000}" --files /src --log /out/agent_stress.log > "$OUT/stress.out" 2>&1 &
  SP=$!
  while kill -0 $SP 2>/dev/null; do   # stop the test when the server is gone instead of hammering a dead port for hours
    sleep 60
    if [ "$(state)" != running ]; then died=$(( ($(date +%s) - S0) / 60 )); docker kill claude-derpseek-stress >/dev/null 2>&1; break; fi
  done
  wait $SP; src=$?
  kill $SMI $SAM 2>/dev/null
  echo "$(date -u +%FT%TZ) container=$(state) alloc-failures=$(alloc) (end)" >> "$OUT/soak-samples.log"
  srvlog | tail -n 600 > "$OUT/server-tail.log"; chown -R 1000:1000 "$OUT"
  [ -n "$died" ] && qlog "stress stopped after $died min: the server is not running any more"
  qlog "stress rc=$src: $(grep -E 'done:' "$OUT/stress.out" | tail -1 | cut -c1-330)"
  if [ "$(state)" = running ]; then   # the same cells and the passkey test again: is the server as fast and as right as before?
    if [ "$SET" != grid ]; then
      AGENT_CONTEXTS="$CTXS" AGENT_COUNTS="${COUNTS:-1,5,8}" $B "$LABEL-agents-post" agents >/dev/null 2>&1
      qlog "agents after the stress test: $(grep -h 'SUMMARY' /srv/vllm/runs/*-bench-"$LABEL-agents-post"/bench.log 2>/dev/null | tail -1 | cut -c1-200)"
    fi
    needle needle-post
  fi
fi
# Keep the whole server log, the forwarder's log and the container's final state in the run folder.
srvlog > "$RUN/server-final.log" 2>&1
docker inspect -f '{{json .State}}' $CN > "$RUN/state-final.json" 2>/dev/null
journalctl -u $F --since "$SINCE" -o cat --no-pager > "$RUN/forwarder.log" 2>&1
curl -s -m 5 $FWD/metrics | grep -E '^dsfwd_' > "$RUN/forwarder-metrics.txt" 2>/dev/null
chown -R 1000:1000 "$RUN" 2>/dev/null
qlog "graphs: $(grep -c 'DSV41_WHOLE_STEP captured' "$RUN/server-final.log") captured; last: $(grep 'DSV41_WHOLE_STEP captured' "$RUN/server-final.log" | tail -1 | sed -E 's/.*in [0-9.]+ s; //' | cut -c1-150)"
qlog "forwarder: $(systemctl is-active $F), requests=$(fm dsfwd_requests_total) ok=$(fm dsfwd_responses_ok_total) client-errors=$(fm dsfwd_responses_client_error_total) server-errors=$(fm dsfwd_responses_server_error_total) aborts=$(fm dsfwd_client_aborts_total) queue-full=$(fm dsfwd_rejected_queue_full_total) engine-unreachable=$(fm dsfwd_upstream_unreachable_total) waited=$(fm dsfwd_slot_wait_seconds_sum)s"
[ "$(state)" = running ] || qlog "SERVER DIED: $(fatal)"
qlog "done: container=$(state) alloc-failures=$(alloc) gpu-mem=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader) gpu-errors-in-kernel-log=$(dmesg 2>/dev/null | grep -ciE 'NVRM: Xid')"
