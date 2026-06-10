#!/bin/sh
set -e

# Patch the baked-in agent WebSocket URL in the built JS bundles if
# an override env var is provided. Lets one image work across
# deployments without rebuilding.
#
# The regex matches `ws://` or `wss://`, any IPv4 address or hostname
# (digits, letters, dots, hyphens — covers /etc/hosts entries, .local
# addresses, FQDNs), and the literal `:8080/ws` the build emits.
# IPv6 hosts (with `[…]` brackets) are not currently supported.
#
# TTS doesn't appear here: the browser derives its TTS URL from the
# chat URL by swapping the path to /tts, so the chat patch covers it.
if [ -n "${SWARPIUS_WS_URL:-}" ]; then
  for f in /usr/share/nginx/html/assets/index-*.js; do
    sed -i -E "s#wss?://[A-Za-z0-9.\-]+:8080/ws#${SWARPIUS_WS_URL}#g" "$f"
  done
fi

exec nginx -g "daemon off;"
