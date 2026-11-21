#!/bin/bash
# Stops a unit if node0 (the host's own memory) runs out of free + reclaimable memory for 3 samples in a row, so the
# engine cannot push the host into a memory lockup.
# Usage: ds-memguard.sh <limit GiB> <unit to stop>
LIMIT=${1:-6}; UNIT=${2:-deepseek-derpseek.service}; low=0
while true; do
  avail=$(awk '/MemFree:|Active\(file\)|Inactive\(file\)/ {s+=$4} END {printf "%d", s/1048576}' /sys/devices/system/node/node0/meminfo)
  if [ "$avail" -lt "$LIMIT" ]; then low=$((low+1)); else low=0; fi
  if [ $low -ge 3 ]; then
    echo "$(date -u +%FT%TZ) node0 free+reclaimable ${avail} GiB < ${LIMIT} GiB: stopping $UNIT" | tee -a /srv/vllm/memguard.log | logger -t ds-memguard
    systemctl --no-block stop "$UNIT"
    low=0; sleep 60
  fi
  sleep 2
done
