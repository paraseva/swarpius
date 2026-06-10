# Swarpius Web Client

This is a React + Vite frontend for the Swarpius agent WebSocket API.

## What it does

- Chat interface for sending messages and receiving agent responses
- Live playback status, controls and artwork panels driven by Roon zone events
- LLM diagnostics panel: active/completed calls, prompt token breakdowns, interrupt decisions
- Prompt budget panel: rolling and session-wide token aggregation
- Request summary and timeline panels
- Conversation analysis browser: findings, revoked findings, per-conversation history, and metrics dashboards
- Optional server-side TTS playback (toggle in UI)
- Dev mode for additional diagnostic views — double-click the Swarpius logo to toggle. Once on, Ctrl+Shift+D toggles the diagnostics drawer and Ctrl+Shift+A toggles the analysis browser

## Prerequisites

- Node.js 22
- npm 10+
- Running Swarpius agent backend

## Install

```bash
npm install
```

## Configuration

```bash
cp .env.template .env
```

| Variable | Default | Description |
|---|---|---|
| `VITE_WS_URL` | `ws://<browser-host>:8080/ws` | Agent WebSocket URL |

When unset:
- The app WS URL is derived from the browser's current host (`ws://` on HTTP, `wss://` on HTTPS)
- Loopback URLs (`localhost`, `127.0.0.1`, `::1`) are automatically rewritten to the browser host for LAN access

TTS is configured on the agent side (`TTS_URL`, set via the Settings UI's Speech tab) — the browser reaches it via the agent's `/tts` proxy on the same port as chat, so there's no separate TTS URL to bake into the web-client build.

## Run

```bash
npm run dev        # dev server at http://localhost:5173
npm run build      # type-check + production build
npm run preview    # serve built assets locally
npm run lint       # ESLint
npm test           # Vitest
```

## Docker

From the monorepo root:

```bash
docker compose up -d swarpius-web-client
```

The container serves a production build via nginx.

### Build-time vs runtime env vars

The web client has two sets of env vars that look similar but apply at different stages:

- **Build time:** `VITE_WS_URL` (read by Vite during `npm run build`). Its value is baked into the JS bundle as a literal string. See [Configuration](#configuration) above.
- **Runtime:** `SWARPIUS_WS_URL` (read by `docker-entrypoint.sh` when the container starts). The entrypoint `sed`-patches the already-built JS bundle to swap the baked URL for this value, so one Docker image can serve multiple deployments without rebuilding.

For the typical `docker compose up` flow neither is set: the build emits no explicit URL, the bundle falls through to browser-host derivation, and the entrypoint's sed pass has nothing to substitute.

### Runtime override env vars

Set on the `swarpius-web-client` container (via `docker-compose.yml` `environment:` block, an `--env-file`, or `docker run -e ...`).

| Variable | Description |
|---|---|
| `SWARPIUS_WS_URL` | Replace the baked agent WebSocket URL (e.g. `ws://192.168.1.50:8080/ws`) |

The sed pattern matches `ws://` or `wss://` followed by any IPv4 address or hostname (covers `/etc/hosts` entries, `.local` addresses, FQDNs). It only kicks in if the build baked an explicit URL — if the bundle is using browser-host derivation, there's nothing to replace. IPv6 hosts (with `[…]` brackets) aren't currently supported.
