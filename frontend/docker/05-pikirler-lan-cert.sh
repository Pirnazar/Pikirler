#!/bin/sh
# Self-signed certificate for HTTPS on the local network (local stack only;
# production mounts a real certificate and leaves PIKIRLER_LAN_HTTPS unset).
# Issued for localhost and PIKIRLER_LAN_IP, and re-issued when that changes.
set -eu

[ "${PIKIRLER_LAN_HTTPS:-}" = "1" ] || exit 0

dir=/etc/nginx/lan-certs
san="DNS:localhost,IP:127.0.0.1"
if [ -n "${PIKIRLER_LAN_IP:-}" ]; then
  san="$san,IP:$PIKIRLER_LAN_IP"
fi

if [ -f "$dir/cert.pem" ] && [ "$(cat "$dir/san" 2>/dev/null)" = "$san" ]; then
  exit 0
fi

mkdir -p "$dir"
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -subj "/CN=Pikirler (local network)" -addext "subjectAltName=$san" \
  -keyout "$dir/key.pem" -out "$dir/cert.pem" 2>/dev/null
echo "$san" > "$dir/san"
echo "pikirler: self-signed certificate issued for $san"
