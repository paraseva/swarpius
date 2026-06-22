# Security Policy

## Reporting a vulnerability

If you believe you have found a security vulnerability in Swarpius, please
do **not** open a public issue. Report it privately through one of:

- **GitHub (preferred):** use the **["Report a vulnerability"](https://github.com/paraseva/swarpius/security/advisories/new)** button on the repository's **Security** tab — this opens a private security advisory visible only to you and the maintainers.
- **Email:** **[security@paraseva.ai](mailto:security@paraseva.ai)**

Please include:

- A description of the issue and where you found it.
- Steps to reproduce (or a proof-of-concept) where possible.
- Your assessment of impact and severity.

Swarpius is a solo open-source project with no formal support, so we
can't commit to firm response SLAs. We aim to acknowledge reports as
soon as practical (typically within a week or two) and will coordinate
a fix and release timeline with you if one is warranted. We follow
responsible-disclosure conventions: please give us reasonable time to
release a fix before any public disclosure (90 days from initial report
is a good default, shorter if the issue is already public).

## Supported versions

Security fixes are applied to `main` and to the latest tagged
release. Older tags are not patched — pull or rebuild from `main`
(or upgrade to the latest tag) to receive fixes.

## Threat model

Swarpius is designed to run **self-hosted on a trusted LAN by a single
operator**. The threat model assumes:

- One operator running the agent on hardware they control.
- A small set of trusted devices (operator's phone, laptop, tablet) on
  the same LAN as the agent.
- LLM API keys held by the operator with per-provider spend limits.
- A locally-installed Roon Core also under the operator's control.

The threat model does **not** assume:

- Multi-tenant operation. There is no per-user authentication or
  isolation between connected clients.
- Public-internet exposure of the agent's WebSocket endpoint. See
  [Network exposure](#network-exposure) below.
- Untrusted contributors with shell access on the host running the
  agent — anyone with shell access already has full control.

Treat each Swarpius instance as a single-operator device.

## Network exposure

The agent's WebSocket server has **no authentication, no Origin check,
and no per-client rate limiting**. A connected client can:

- Send chat messages that incur LLM token cost on the operator's API
  key.
- Issue Roon transport commands (play, pause, queue, change zones).
- Browse and read all conversation logs (chat input, LLM prompts,
  Roon library content).
- **Read the configured LLM and web-search API keys in plaintext** via
  the settings channel — the UI only masks them in the display; the
  values themselves are sent over the wire.
- **Overwrite or delete those keys** (and other settings) via the
  settings channel in source / bundled-app mode, where the agent can
  write `.env`. Under Docker the `.env` is not writable, so saves are
  rejected — but reads still return the keys.
- Trigger expensive on-demand re-analysis runs.

To make the trust assumption explicit:

- **Default bind is `127.0.0.1`** (loopback only) in every install
  mode. A fresh `docker compose up` is reachable only from the host
  that runs it; a source or installed-app run binds the agent to
  loopback as well (the agent defaults to `127.0.0.1`).
- **Opt-in LAN access.** Under **Docker**, set the relevant
  `*_BIND_IP=0.0.0.0` before `docker compose up` —
  `SWARPIUS_BIND_IP=0.0.0.0`, `CLIENT_BIND_IP=0.0.0.0`,
  `TTS_BIND_IP=0.0.0.0`, `SEARXNG_BIND_IP=0.0.0.0` — to reach the
  services from other devices on your LAN. Alternatively, set these
  in the `.env` file in the root folder for pick-up by Docker Compose.
  For a **source or installed-app** run there is no Docker port mapping, so
  set `SWARPIUS_WS_HOST="0.0.0.0"` (note the quotes) in the `.env` instead —
  `agent/.env` for a source checkout, or the `.env` in the data folder for
  the installed app.
- **Do not expose the agent or web client as-is to the public internet.**
  Browsers do **not** enforce same-origin on WebSockets, so any web
  page a LAN user visits could connect to an internet-reachable
  agent and drive it. For anything beyond a single trusted machine, put
  **authentication** in front — note that TLS / `wss://` alone only
  *encrypts*; it does not stop an unauthorised client connecting, so the
  layer must also authenticate. Options, roughly easiest first:
  - **Single machine (simplest).** Keep the loopback default and run the
    browser on the same host — nothing off-host can reach the WS.
  - **Overlay VPN** — Tailscale, ZeroTier, or a WireGuard mesh
    (recommended for multi-device; zero-effort device auth). Bind the
    agent to the overlay interface by setting `SWARPIUS_BIND_IP`
    (for Docker) or `SWARPIUS_WS_HOST` (for source/installed-app).
    Do not set it to `0.0.0.0`.
  - **Authenticating reverse proxy** in front of the loopback-bound
    agent (`wss://`) and web client (`https://`): nginx/Caddy + basic
    auth, oauth2-proxy / Authelia (cookie auth), or mTLS. Point the web
    client at it with `VITE_WS_URL` — no rebuild needed. Cloudflare
    Tunnel + Access bundles the tunnel and auth together.
  - **SSH tunnel** for ad-hoc access: `ssh -L 8080:127.0.0.1:8080
    agent-host` — encrypted and authenticated by SSH, nothing to install.
- **Limit the blast radius of the keys.** Use a **dedicated** LLM /
  search API key for Swarpius (separate from your main key) with a
  provider **spend cap** where available, so a leaked or overwritten key
  is bounded and easy to revoke. See
  [*Secrets and key rotation*](#secrets-and-key-rotation).

**Listening ports.** When you flip a `*_BIND_IP` to `0.0.0.0`, the
host firewall must allow the corresponding port inbound:

| Service | Default port (override) |
|---|---|
| Agent WebSocket | 8080 (`SWARPIUS_PORT`) — serves both `/ws` (chat) and `/tts` (TTS proxy) |
| Web client | 5173 (`CLIENT_PORT`) |
| F5-TTS TCP API | 9998 (`TTS_API_PORT`) — used by the agent only |
| SearXNG | 8888 (`SEARXNG_PORT`) |

**The web client only dials the agent.** TTS reaches the browser via
the agent's `/tts` WebSocket path on the same port as chat — one
firewall rule covers both. The TTS TCP port doesn't need to be
exposed to browsers (only to the agent). If you split services
across machines, set `VITE_WS_URL` to a URL that resolves from the
browser's network position. (The same-host case is handled
automatically — the web client rewrites loopback URLs to the
browser's host.)

## Outbound hosts

Behind an egress firewall, the running stack contacts the following
hosts. Each row only applies if you use that feature; at least one LLM
provider host must be reachable. Add to your allowlist as needed.

| Host | Purpose |
|---|---|
| `api.anthropic.com` | LLM (Anthropic models) |
| `api.openai.com` | LLM (OpenAI models) |
| `generativelanguage.googleapis.com` | LLM (Gemini models) |
| `<ollama-host>:11434` | Local LLM |
| `api.search.brave.com` | Web search (Brave) |
| `api.tavily.com` | Web search (Tavily) |
| `${SEARXNG_URL}` | Web search (SearXNG) |
| `huggingface.co`, `cdn-lfs.huggingface.co` | F5-TTS model download (first run only) |
| `${TTS_URL}` | F5-TTS server (agent connects here; browser reaches TTS via the agent's `/tts` proxy) |
| `${ROON_CORE_URL}` | Music control. Auto-discovered via UDP SOOD multicast `239.255.90.90:9003` and broadcast `255.255.255.255:9003` |
| `api.github.com` | Update check (from the browser). Read-only query for the latest release tag; nothing is sent beyond the request. On by default; opt out via Settings → "Check for updates automatically" |

**Other LiteLLM providers.** LiteLLM supports many providers beyond
the three listed above (Mistral, Groq, OpenRouter, Cohere, Bedrock,
Azure OpenAI, …); using one of those will add its own host — check
the provider's docs.

**LiteLLM model-pricing JSON.** LiteLLM may also fetch a
model-pricing JSON from `raw.githubusercontent.com` at import time;
falls back to a bundled local copy if the request fails.

**Self-hosted SearXNG.** If you self-host SearXNG, the SearXNG
instance itself makes outbound calls to the search engines it queries
(Google, Bing, DuckDuckGo, Wikipedia, etc.) on its own egress.
That's a SearXNG concern, not the agent's, but operators with strict
outbound rules will need to allow those too.

**Web client.** The web client serves static files and makes one
outbound call of its own: an opt-out check to `api.github.com` for the
latest release tag, so it can flag when a newer version is available.
It's read-only (nothing about the user is sent beyond the request),
cached for a few hours, and disabled with one toggle in Settings
("Check for updates automatically"). Cross-host paths from the browser
to the agent and TTS server are covered by the *Listening ports* table
in [*Network exposure*](#network-exposure) above.

**Build-time only** (during `docker compose build`):

- `download.pytorch.org/whl/cu128` (TTS server)
- `github.com/SWivid/F5-TTS` (TTS server)
- Docker Hub / configured registry (image pulls)

## Local logs and privacy

Swarpius writes per-request and per-server logs locally for diagnostics
and analysis. These contain **user-identifiable content** including:

- The exact text of every chat message you send.
- The full assistant reply.
- Tool call inputs and outputs — Roon library titles (artist, album,
  track names of music you browse), web search queries and result
  snippets, queue contents.
- Token usage, model name, request timings.

**Locations** (relative to `${SWARPIUS_DATA_DIR}`, default
`agent/data/`):

- `logs/conversation/<date>/<cNN>/<request-id>/`: full
  per-request trace.
- `logs/server/<date>/<cNN>/<request-id>/server.yaml`: Roon API
  browse/action detail.
- `messages.db`: the chat transcript and diagnostics, **plus the persisted
  runtime state** that lets a restart resume where you left off — the
  assistant's working memory (recent conversation turns, cached search
  results), the Roon references built during the conversation, the
  conversation-tracker state, the default zone, and a **listening-history**
  record of recently played tracks. This is retained across restarts **by
  default** (the previous `--keep-history` flag has been removed).

**Retention.** `LOG_RETENTION_DAYS` (default `7`) controls how long per-request
and server logs under `logs/` are kept; cleanup runs on agent startup (minimum
practical value `1`; `0` is treated as `1`). Persisted state in `messages.db` is
pruned on its own startup schedule, with independent windows:
`CHAT_HISTORY_RETENTION_DAYS` (chat transcript + working memory, default `90`),
`DIAGNOSTICS_RETENTION_DAYS` (agent/tool/LLM event records, default `30`), and
`LISTENING_HISTORY_RETENTION_DAYS` (default `365`). Set any to `0` to keep that
data indefinitely. There is no built-in toggle to disable logging entirely.

**Deleting chat history.** Open Settings → **Privacy & Data**. *Clear
conversation history* deletes the transcript, the assistant's working memory,
and the conversation's cached search references in one action; *Clear listening
history* deletes the played-track record separately (your saved zones and other
settings are kept). These are the supported ways to wipe locally-stored data.

**`.gitignore` coverage.** All log paths under `agent/data/` are
git-ignored; they will not be accidentally committed. The provided
`.dockerignore` also excludes `data/` from image layers.

**Sharing log bundles for support.** Two channels, depending on what
you can share publicly:

- **Public GitHub issue** — **scrub** `agent/data/`, `agent/.env`, and
  any `.env*` files first. They contain chat history, library titles,
  and possibly partial keys in LLM provider error strings. For a music
  assistant the artist / album / track names are usually the diagnostic
  signal the bug hinges on — if scrubbing removes that signal, use the
  private channel instead.
- **Private email to [dev@paraseva.ai](mailto:dev@paraseva.ai)** — full unscrubbed
  `agent/data/` bundle is fine here. Still **redact `agent/.env`** so
  your LLM API keys don't travel by email. This is a best-effort
  channel with no SLA — see
  [`CONTRIBUTING.md`](CONTRIBUTING.md#reporting) for the framing.

## Secrets and key rotation

LLM provider API keys live in `agent/.env`. The file is git-ignored
by a blanket `.*` rule plus an explicit `.env` rule in
`agent/.gitignore`, and excluded from Docker image layers by
`.dockerignore`. Plain `git add .` will not pick it up; only
`git add -f agent/.env` could commit it accidentally.

We recommend:

- Set per-provider **spend limits** at the provider's billing console.
  This caps the cost of an abusive client (or a runaway loop).
- **Rotate keys** if you suspect exposure: a lost laptop, a shared log
  bundle that leaked, a screenshot of the diagnostics panel that
  showed a provider error string. Swarpius does not do automated
  rotation.
- Keep the `.env` file outside source control (default), and outside
  any backup that goes to less-trusted storage.
- Be aware that **LLM provider error messages can include
  partial keys** in URL query parameters or auth headers. Swarpius
  redacts the common patterns (`sk-…`, `AIza…`, `Bearer …`,
  `?key=…`) before logging or emitting these to the WebSocket
  client, but redaction is best-effort. If you see a provider auth
  failure surfaced in the diagnostics panel, treat the screenshot as
  potentially sensitive.

## Container hardening

All three Swarpius-built services (agent, web client, TTS server) come with the following Docker hardening applied in `docker-compose.yml`. The passive analyser runs inside the agent process, so it inherits the agent's hardening rather than being a separate container:

- **`cap_drop: [ALL]`** — every Linux capability dropped.
- **`security_opt: no-new-privileges:true`** — processes cannot gain privileges via setuid binaries.
- **Non-root `USER`** baked into the Dockerfile — UID 1000 (`swarpius`) for the agent and TTS server, UID 101 (`nginx`) for the web-client via `nginxinc/nginx-unprivileged`.
- **`read_only: true` root filesystem** with minimal `tmpfs:` carve-outs for paths each service genuinely needs to write:
  - agent: `/tmp` only. `PYTHONDONTWRITEBYTECODE=1` is set in env to suppress `__pycache__` writes that would otherwise fail.
  - web client (nginx-unprivileged): `/tmp` for nginx PID and worker temp dirs.
  - TTS server: `/tmp` plus `/home/swarpius/.cache` and `/home/swarpius/.config` for ML library caches (PyTorch, transformers, matplotlib). The F5-TTS HuggingFace model cache sits on a named volume mounted at the deeper `/home/swarpius/.cache/huggingface/hub` path, so model downloads persist across restarts.

The published `searxng` image (when enabled via `--profile search`) inherits whatever hardening SearXNG provides; Swarpius doesn't redistribute or re-harden that image.

**Operator override caveat:** the web-client entrypoint's optional `SWARPIUS_WS_URL` `sed -i` rewrite of `/usr/share/nginx/html/assets/*.js` (used when the same image needs to serve a non-default backend URL without rebuilding) is incompatible with `read_only: true`. The typical Docker Compose flow doesn't set this env var — the bundle falls through to browser-host derivation — so the override path only matters for the multi-deployment use case. If you need it, remove `read_only: true` from the `swarpius-web-client` service.

## Container UIDs and bind-mount ownership

The agent and TTS server containers run as a non-root user with **UID
1000** by default (`swarpius`); the passive analyser runs inside the
agent process, so the same UID applies. The web-client container runs as
nginx user (UID 101) via `nginxinc/nginx-unprivileged`.

If your host user has a UID other than 1000, bind-mounted directories
(notably `./agent/data/`) will appear inside the container as owned
by an unrelated UID. Symptom: the agent or analyser fails to write
log files. To fix, set `HOST_UID` to your host user's UID (the compose
file passes it through as the image's build UID) and rebuild:

```sh
HOST_UID=$(id -u) docker compose build swarpius-agent tts-server
```

## Known accepted residuals

For transparency, these are issues we have considered and explicitly
accepted as residuals:

- **No authentication on the WebSocket endpoint.** A connected client
  can drive the agent and **read or (in source/bundle mode) overwrite
  the configured API keys**. Mitigated by the loopback-default bind, the
  documented LAN-trust assumption, and the deployment guidance in
  [*Network exposure*](#network-exposure) above; limit residual risk
  with a dedicated, spend-capped key.
- **Prompt-injection from web search results and Roon library
  metadata.** Tool outputs from `web_search` (SearXNG snippets) and
  `roon_search` / `roon_status` (track/album/artist titles) are fed
  back into the LLM context unchanged. A hostile web page or a
  carefully named track could try to redirect the agent. Mitigations
  in place: **the agent's tool inventory is bounded** —
  `roon_search`, `roon_status`, `roon_action`, `roon_config`,
  `web_search`, `result_fetch` only. There is no shell tool, no
  filesystem-write tool, no arbitrary HTTP tool. The Roon Core URL
  and SearXNG URL are operator-pinned environment variables and not
  controllable from the LLM. **Worst-case impact:** unwanted Roon
  playback or a zone-alias rename. Annoying, audible, and fully
  reversible. We accept this rather than introduce prompt-level
  filtering that could blunt legitimate tool error/recovery
  guidance.
- **Local logs contain user-identifiable content.** Documented in
  [*Local logs and privacy*](#local-logs-and-privacy) above; `logs/` retention
  defaults to 7 days. Chat history + working memory in `messages.db` persist
  across restarts by default and are not age-pruned yet — clear them from
  Settings → Privacy & Data.
- **No built-in pre-commit secret scanning.** Plain `git add .` is
  prevented by the `.gitignore` rules from picking up `.env`, but
  `git add -f agent/.env` could commit it. Operators wanting an
  extra safety net can run `pre-commit install` with
  `detect-secrets` or `gitleaks` in their working tree.
