#!/bin/bash
# memguard.sh: stop deepseek-vllm if node0 (the Grace LPDDR5X) runs out of free + reclaimable memory for
# 3 samples in a row, so an unattended start cannot push the host into a memory lockup.
# Pinned Engram tables + offloaded experts use ~430 of ~494 GiB, so the margin is thin.
# Usage: memguard.sh <limit GiB>   (run it as a transient unit next to a first start)
LIMIT=${1:-6}; low=0
while true; do
  avail=$(awk '/MemFree:|Active\(file\)|Inactive\(file\)/ {s+=$4} END {printf "%d", s/1048576}' /sys/devices/system/node/node0/meminfo)
  if [ "$avail" -lt "$LIMIT" ]; then low=$((low+1)); else low=0; fi
  if [ $low -ge 3 ]; then
    echo "$(date -u +%FT%TZ) node0 free+reclaimable ${avail} GiB < ${LIMIT} GiB: stopping deepseek-vllm" | logger -t memguard
    docker kill deepseek-vllm >/dev/null 2>&1; systemctl stop deepseek-vllm; low=0
  fi
  sleep 2
done
