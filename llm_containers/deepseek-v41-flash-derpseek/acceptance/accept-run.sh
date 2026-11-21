#!/bin/bash
# Runs the items of an acceptance queue one after the other against our own image and service.
# One item per line: <label> image [VAR=value ...]   (# starts a comment); each item is one image-variant.sh run.
#   IF_OK=<label>:<VAR=value>   on a line: add VAR=value to this item only if the item <label> passed
#                               (server running at its end, no failed check, passkey test complete)
# Usage: accept-run.sh <queue file> [number of the first item to run, default 1]
# Log: /srv/vllm/queue/queue.log (same format as queue-run.sh, so queue-events.sh and the watcher work unchanged)
# At the end the stock server is started again (END=keep leaves our service running).
set -u
QF=$1; FIRST=${2:-1}; Q=/srv/vllm/queue; mkdir -p $Q
D=/opt/deepseek-vllm/derpseek; U=deepseek-derpseek.service; F=deepseek-forwarder.service
ql(){ echo "$(date -u +%FT%TZ) [queue] $*" >> $Q/queue.log; }
grep -vE '^[[:space:]]*(#|$)' "$QF" > $Q/.items
TOTAL=$(wc -l < $Q/.items); n=0
ql "$QF: $TOTAL items, starting at item $FIRST"
START=$(grep -c '' $Q/queue.log)

passed(){   # did the item $1 pass, judged by this run's lines in the queue log?
  local L; L=$(tail -n +"$START" $Q/queue.log | grep -F "[$1]")
  echo "$L" | grep -q '\] done: container=running' || { echo "the server was not running at its end"; return 1; }
  echo "$L" | grep -qE 'START FAILED|SERVER DIED' && { echo "start failed or server died"; return 1; }
  echo "$L" | grep -q 'checks: SUMMARY' && ! echo "$L" | grep -q 'checks: SUMMARY passed=[0-9]* failed=0 ' && { echo "a check failed"; return 1; }
  local p; p=$(echo "$L" | grep -oE 'passkey \(needle\): [0-9]+/[0-9]+' | tail -1 | grep -oE '[0-9]+/[0-9]+')
  [ -n "$p" ] && [ "${p%/*}" = "${p#*/}" ] || { echo "passkey test ${p:-missing}"; return 1; }
  echo "server up, checks passed, passkey $p"; return 0
}

while IFS= read -r line <&3; do
  n=$((n+1)); [ $n -lt "$FIRST" ] && continue
  cond=$(echo "$line" | grep -oE 'IF_OK=[^ ]+' | head -1)
  line=$(echo "$line" | sed -E 's/ *IF_OK=[^ ]+//')
  if [ -n "$cond" ]; then
    ref=${cond#IF_OK=}; lab=${ref%%:*}; add=${ref#*:}
    if why=$(passed "$lab"); then line="$line $add"; ql "verdict: $lab passed ($why), so this item runs with $add"
    else ql "verdict: $lab did not pass ($why), so this item runs without $add"; fi
  fi
  ql "item $n of $TOTAL: $line"
  # a subshell, so one item's settings do not leak into the next
  ( set -- $line; $D/image-variant.sh "$@" ) >/dev/null 2>&1
done 3< $Q/.items

# Undo what the items set up for testing.
BR=br-$(docker network inspect ds-isolated --format '{{.Id}}' 2>/dev/null | cut -c1-12)
iptables -D INPUT -i "$BR" -p tcp -d 172.31.250.1 --dport 8010 -j ACCEPT 2>/dev/null
: > /run/deepseek-derpseek/override.env
if [ "${END:-stock}" = stock ]; then
  systemctl stop $F $U 2>/dev/null
  rm -f /run/systemd/system/$U.d/accept.conf; rmdir /run/systemd/system/$U.d 2>/dev/null; systemctl daemon-reload
  systemctl start deepseek-vllm
  ql "FINISHED: our service stopped, stock server starting"
else
  ql "FINISHED: our service left running (test settings still in place: no automatic restart)"
fi
