#!/bin/sh
# =============================================================================
# VPN Sidecar Health Check
# =============================================================================
# Serves a single HTTP request (fed in on stdin, response on stdout — invoked
# per-connection by `socat TCP-LISTEN:...,fork SYSTEM:/healthcheck.sh`).
#
# Verdict table (passive staleness detection):
#
#   ppp0 missing / processes dead         -> 503 (obvious failure)
#   idle   (no SOCKS conns, tx stable)    -> 200 (nothing to prove)
#   flowing (tx delta > 0, rx delta > 0)  -> 200 (tunnel is carrying traffic)
#   stale  (tx delta > 0, rx delta == 0)  -> 503 (sent into a black hole)
#
# Counter comparison state is persisted in $STATE_FILE between probes. When the
# tunnel reconnects ppp0 counters reset to 0; we treat a negative tx delta as
# "idle" (the next non-reset probe re-baselines correctly).
# =============================================================================

STATE_FILE="${STATE_FILE:-/tmp/vpn-health-last}"
SOCKS_PORT="${SOCKS_PORT:-1080}"

# Drain the HTTP request — we don't dispatch on path or method.
while IFS= read -r line; do
    line=$(printf '%s' "$line" | tr -d '\r')
    [ -z "$line" ] && break
done

respond() {
    _status="$1"
    _body="$2"
    printf 'HTTP/1.1 %s\r\n' "$_status"
    printf 'Content-Type: text/plain\r\n'
    printf 'Content-Length: %d\r\n' "${#_body}"
    printf 'Connection: close\r\n'
    printf '\r\n'
    printf '%s' "$_body"
}

fail() { respond "503 Service Unavailable" "unhealthy: $1"; exit 0; }
ok()   { respond "200 OK"                   "healthy: $1";   exit 0; }

# ── 1. Interface present ──────────────────────────────────────────────────
[ -d /sys/class/net/ppp0 ] || fail "ppp0 interface missing"

# ppp typically reports operstate "unknown" rather than "up"; accept both.
OPERSTATE=$(cat /sys/class/net/ppp0/operstate 2>/dev/null)
case "$OPERSTATE" in
    up|unknown) ;;
    *) fail "ppp0 operstate=${OPERSTATE:-missing}" ;;
esac

# ── 2. Supervised processes alive ────────────────────────────────────────
pidof openfortivpn >/dev/null 2>&1 || fail "openfortivpn not running"
pidof microsocks   >/dev/null 2>&1 || fail "microsocks not running"

# ── 3. Traffic counters + active SOCKS connections ───────────────────────
TX=$(cat /sys/class/net/ppp0/statistics/tx_bytes 2>/dev/null || echo 0)
RX=$(cat /sys/class/net/ppp0/statistics/rx_bytes 2>/dev/null || echo 0)

# Established TCP connections whose local port is SOCKS_PORT. /proc/net/tcp
# columns: local_addr(ip:port-hex) remote_addr state(01=ESTABLISHED).
SOCKS_HEX=$(printf '%04X' "$SOCKS_PORT")
ACTIVE_CONNS=$(awk -v p=":$SOCKS_HEX" '$2 ~ p"$" && $4 == "01" { c++ } END { print c+0 }' /proc/net/tcp 2>/dev/null)
: "${ACTIVE_CONNS:=0}"

# ── 4. Delta verdict ─────────────────────────────────────────────────────
if [ -f "$STATE_FILE" ]; then
    LAST_TX=$(awk '{print $1}' "$STATE_FILE"); : "${LAST_TX:=0}"
    LAST_RX=$(awk '{print $2}' "$STATE_FILE"); : "${LAST_RX:=0}"
    TX_DELTA=$(( TX - LAST_TX ))
    RX_DELTA=$(( RX - LAST_RX ))

    # Always re-seed so a single bad probe doesn't poison the next comparison.
    printf '%s %s\n' "$TX" "$RX" > "$STATE_FILE"

    # Nothing trying to use the tunnel — can't tell stale from healthy, so pass.
    # Negative delta = counters reset by a reconnect; also treat as idle.
    if [ "$ACTIVE_CONNS" -eq 0 ] && [ "$TX_DELTA" -le 0 ]; then
        ok "idle (conns=0, tx_delta=${TX_DELTA})"
    fi

    # The stale signal: we pushed bytes in, nothing came back.
    if [ "$TX_DELTA" -gt 0 ] && [ "$RX_DELTA" -le 0 ]; then
        fail "tunnel stale (tx+${TX_DELTA}B, rx+${RX_DELTA}B, conns=${ACTIVE_CONNS})"
    fi

    ok "flowing (tx+${TX_DELTA}B, rx+${RX_DELTA}B, conns=${ACTIVE_CONNS})"
else
    # Seed on first probe — no history to compare against yet.
    printf '%s %s\n' "$TX" "$RX" > "$STATE_FILE"
    ok "initializing (tx=${TX}, rx=${RX}, conns=${ACTIVE_CONNS})"
fi
