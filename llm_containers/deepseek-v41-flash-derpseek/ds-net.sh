#!/bin/bash
# Run before the engine container starts (ExecStartPre of deepseek-derpseek.service, as root):
# makes sure the isolated Docker network, the rule that keeps the container from opening connections to this
# host, the state folders and the (possibly empty) file of test overrides exist.
set -eu
NET=ds-isolated; SUBNET=172.31.250.0/24
docker network inspect $NET >/dev/null 2>&1 || docker network create --internal --subnet $SUBNET $NET >/dev/null
BR=br-$(docker network inspect $NET --format '{{.Id}}' | cut -c1-12)
# New connections from the bridge to the host are dropped; answers to the host's own requests still pass.
iptables -C INPUT -i "$BR" -m conntrack --ctstate NEW -j DROP 2>/dev/null || iptables -A INPUT -i "$BR" -m conntrack --ctstate NEW -j DROP
STATE=${STATE:-/srv/vllm/ds-state}
mkdir -p "$STATE"/{vllm,flashinfer} "$STATE"/jit/{tilelang,triton,nv,dj}
mkdir -p /run/deepseek-derpseek
[ -f /run/deepseek-derpseek/override.env ] || : > /run/deepseek-derpseek/override.env
echo "ds-net: network $NET on $BR, state in $STATE, overrides: $(grep -cvE '^\s*(#|$)' /run/deepseek-derpseek/override.env || true) line(s)"
