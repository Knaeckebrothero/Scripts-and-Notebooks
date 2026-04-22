# =============================================================================
# VPN Sidecar - SOCKS5 proxy via FortiClient VPN
# =============================================================================
#
# Provides a SOCKS5 proxy (microsocks) over an openfortivpn tunnel, so that
# co-located containers (or anything that speaks SOCKS5) can route traffic
# through the VPN.
#
# Build:
#   podman build -t vpn-sidecar ./devops/vpn-sidecar
#
# Run:
#   podman run --cap-add=NET_ADMIN --device=/dev/ppp \
#     -v ./devops/vpn-sidecar/config:/etc/openfortivpn/config:ro \
#     -p 1080:1080 vpn-sidecar
#
# Requires:
#   - VPN config at devops/vpn-sidecar/config (copy config.example and fill in)
#     OR the VPN_HOST / VPN_USER / VPN_PASS env vars (see entrypoint.sh)
#   - NET_ADMIN capability for the tunnel interface
#   - /dev/ppp device for pppd
#
# =============================================================================

# ---------------------------------------------------------------------------
# Builder: compile microsocks from source (~100 lines of C, no dependencies)
# ---------------------------------------------------------------------------
FROM docker.io/library/alpine:3.21 AS builder

RUN apk add --no-cache gcc musl-dev make git

RUN git clone --depth 1 https://github.com/rofl0r/microsocks.git /build/microsocks \
    && cd /build/microsocks \
    && make

# ---------------------------------------------------------------------------
# Final: openfortivpn + ppp + microsocks
# ---------------------------------------------------------------------------
FROM docker.io/library/alpine:3.21

LABEL org.opencontainers.image.title="vpn-sidecar"
LABEL org.opencontainers.image.description="SOCKS5 proxy via FortiClient VPN (openfortivpn + microsocks)"
LABEL org.opencontainers.image.source="https://github.com/Knaeckebrothero/Scripts-and-Notebooks"
LABEL org.opencontainers.image.licenses="CC-BY-4.0"

# openfortivpn is in Alpine edge/testing
RUN echo "@testing https://dl-cdn.alpinelinux.org/alpine/edge/testing" >> /etc/apk/repositories \
    && apk add --no-cache \
    openfortivpn@testing \
    ppp \
    socat

COPY --from=builder /build/microsocks/microsocks /usr/local/bin/microsocks
COPY entrypoint.sh /entrypoint.sh
COPY healthcheck.sh /healthcheck.sh
RUN chmod +x /entrypoint.sh /healthcheck.sh /usr/local/bin/microsocks

EXPOSE 1080 8081

ENTRYPOINT ["/entrypoint.sh"]
