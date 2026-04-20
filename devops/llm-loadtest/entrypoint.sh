#!/bin/bash
set -e

# Create log directory
mkdir -p /var/log/supervisor

# Set default values for Frankfurt UAS VPN
VPN_ENABLED="${VPN_ENABLED:-false}"
VPN_HOST="${VPN_HOST:-vpngate.frankfurt-university.de}"
VPN_PORT="${VPN_PORT:-443}"
VPN_REALM="${VPN_REALM:-pub-all}"

echo "=== LLM Load Tester ==="
echo "VPN Enabled: $VPN_ENABLED"

if [ "$VPN_ENABLED" = "true" ]; then
    echo "VPN Host: $VPN_HOST:$VPN_PORT"
    echo "VPN Realm: $VPN_REALM"
    echo "VPN User: $VPN_USER"

    # Check required VPN variables
    if [ -z "$VPN_USER" ] || [ -z "$VPN_PASSWORD" ]; then
        echo "ERROR: VPN is enabled but VPN_USER or VPN_PASSWORD is not set"
        exit 1
    fi

    # Ensure GEANT certificate is installed
    # The certificate should be baked into the image, but we can also download it
    if [ ! -f /etc/ssl/certs/geant_chain.pem ]; then
        echo "Installing GEANT certificate chain..."
        if [ -f /app/certs/geant_chain.pem ]; then
            cp /app/certs/geant_chain.pem /usr/local/share/ca-certificates/geant_chain.crt
            update-ca-certificates
        else
            echo "WARNING: GEANT certificate not found. VPN connection may fail."
            echo "Download from: https://wiki.geant.org/display/TRUS/GEANT+OV+RSA+CA+4"
        fi
    fi

    echo "Starting VPN connection..."

    # Start openfortivpn with realm parameter
    /usr/bin/openfortivpn "$VPN_HOST:$VPN_PORT" \
        -u "$VPN_USER" \
        -p "$VPN_PASSWORD" \
        --realm="$VPN_REALM" \
        --pppd-use-peerdns=0 \
        > /var/log/supervisor/vpn.log 2>&1 &

    VPN_PID=$!
    echo "VPN started with PID $VPN_PID"

    # Wait for VPN to establish (check for ppp interface)
    echo "Waiting for VPN connection..."
    for i in {1..30}; do
        if ip link show ppp0 > /dev/null 2>&1; then
            echo "VPN connected successfully!"
            break
        fi
        if ! kill -0 $VPN_PID 2>/dev/null; then
            echo "ERROR: VPN process died. Check logs:"
            cat /var/log/supervisor/vpn.log
            exit 1
        fi
        sleep 1
    done

    if ! ip link show ppp0 > /dev/null 2>&1; then
        echo "WARNING: VPN interface not detected after 30s"
        echo "VPN log output:"
        cat /var/log/supervisor/vpn.log
        echo "Continuing anyway..."
    fi

    # Add route for the university network (10.18.x.x)
    echo "Adding route for 10.18.0.0/16 via VPN..."
    ip route add 10.18.0.0/16 dev ppp0 2>/dev/null || echo "Route may already exist"

    # Test connectivity
    echo "Testing connectivity to LLM server..."
    if ping -c 1 -W 5 10.18.2.105 > /dev/null 2>&1; then
        echo "Connectivity OK!"
    else
        echo "WARNING: Cannot ping 10.18.2.105 - VPN routing may not be working"
    fi
fi

echo "Starting supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
