# Web Client Architecture

Developer reference for the Swarpius web client: a React 19 / Vite chat and playback UI that communicates with the agent backend over a single WebSocket connection.

---

## 1. Technology Stack

| Layer | Choice |
|---|---|
| Framework | React 19.2 with React Compiler (babel-plugin-react-compiler) |
| Bundler | rolldown-vite (aliased as `vite` via npm overrides) |
| Type System | TypeScript 5.9, strict mode, `verbatimModuleSyntax` |
| Testing | Vitest 4.1 + @testing-library/react 16 + jsdom 29 |
| Linting | ESLint 9 flat config, react-hooks + react-refresh plugins |
| Styling | Global `index.css` (~1100 lines) + CSS modules per component |
| State | React context (`WebSocketContext`) + local component state; no external state library |

---

## 2. Module Map

Architecturally significant files only — minor sub-components (modals, dialogs, sub-views, utility leaves) and `*.test.*` / `*.module.css` siblings are omitted for brevity.

```
src/
├── main.tsx                    Entry point, React.StrictMode render
├── App.tsx                     Shell: header, main layout, diagnostics drawer
├── WebSocketProvider.tsx       Single WS connection, message store, send API
├── websocketContext.ts         Context type definitions, useWebSocket hook
├── config.ts                   WS URL resolution (app + TTS), loopback rewriting
├── appView.ts                  App-view type + post-restart navigation policy
├── appSurface.ts               Overlay arbitration: which full-screen surface to show
├── gettingStarted.ts           First-run welcome auto-show policy
├── updateCheck.ts              GitHub update check + localStorage opt-out preference
├── tts.tsx                     TTS via separate WS, Web Audio API streaming
├── hooks/
│   ├── useDiagnostics.ts       Unread counts, usage snapshots, drawer state
│   ├── useChatBannerManager.ts Rate-limit / error / session-control banners on ChatPanel
│   ├── useChatStepLabel.ts     Per-step activity label rendering helpers for ChatPanel
│   ├── useChatTtsAutoPlay.ts   Auto-TTS speak orchestration on inbound chat messages
│   ├── useRoonCommands.ts      Outbound Roon control: roon-control-request, feature-verify-request
│   ├── useZoneImageCache.ts    LRU image cache + dedupe for zone artwork
│   ├── useSettingsState.ts     Central Settings UI state + settings-* request/response helpers
│   ├── useClientTtsHealthOverride.ts Client-side TTS-failure latch until the agent's next probe
│   ├── useDevMode.ts           Dev mode toggle (double-click Swarpius logo), persisted to localStorage
│   ├── useFocusTrap.ts         Focus trap for modal overlays
│   └── useSpeechRecognition.ts Web Speech API integration for voice input
├── components/
│   ├── ChatPanel.tsx           Chat messages, rate-limit banners, TTS auto-speak
│   ├── ChatPanel.module.css    Chat panel scoped styles
│   ├── ZoneStatusPanel.tsx     Live zone cards, artwork, transport controls, seek
│   ├── ZoneStatusPanel.module.css  Zone panel scoped styles
│   ├── HistoryWindow.tsx       Generic diagnostics log viewer
│   ├── LlmDiagnosticsPanel.tsx Active/completed LLM call status, arbiter decisions
│   ├── PromptBudgetPanel.tsx   Rolling 60s + session prompt token budgets
│   ├── RequestSummaryPanel.tsx Per-request summary cards with timing and step counts
│   ├── SessionSummaryBar.tsx   Session-level usage summary bar
│   ├── RequestIdBadge.tsx      Click-to-copy request ID badge
│   ├── DefaultZoneBadge.tsx    Current default zone indicator
│   ├── TtsToggle.tsx           TTS on/off toggle
│   ├── TtsStatusIndicator.tsx  Per-message TTS sending/playing indicator
│   ├── FormattedMessageBody.tsx Renders parsed message (source label, JSON, plan blocks)
│   ├── AnalysisBrowser.tsx     Browse and display conversation analysis results
│   ├── AnalysisDetailView.tsx  Single-conversation detail view inside AnalysisBrowser
│   ├── AnalysisHistoryView.tsx History/timeline view inside AnalysisBrowser
│   ├── AnalysisMetricsView.tsx Aggregated metrics view inside AnalysisBrowser
│   ├── RequestLogsView.tsx     Per-request coordinator-step / tool-execution logs (in AnalysisBrowser)
│   ├── FindingCard.tsx         Single analysis finding card with feedback controls
│   ├── RevokedFindingsSection.tsx Collapsible revoked-findings section in analysis detail
│   ├── Settings/                Settings page: provider / Roon / TTS config, validation, restart
│   ├── RoonSetup/               Full-page Roon pairing setup view (initialising / failed)
│   ├── RoonExplorer.tsx        Dev-mode Roon browse-hierarchy explorer (roon-explorer-* channels)
│   ├── RestartModal.tsx        Restart-pending overlay (Save & Validate → Restart flow)
│   ├── ConnectionStatusModal.tsx First-run / pairing-pending overlay
│   ├── SessionTakeoverOverlay.tsx Shown when another tab takes over the WS session
│   ├── GettingStartedModal.tsx First-run Getting Started guide (+ stop-marker setup on desktop bundle)
│   ├── gettingStartedContext.ts Getting Started open-on-demand context
│   ├── ErrorBoundary.tsx       Error boundary wrapping key subtrees
│   ├── GuidanceProvider.tsx    Provides guidance context from markdown
│   ├── GuidanceButton.tsx      Click-to-show guidance popover
│   ├── GuidancePopover.tsx     Guidance content popover with viewport positioning
│   ├── guidanceContext.ts      Guidance context type and hook
│   ├── PanelIcons.tsx          Shared expand/collapse SVG icons
│   ├── JsonTreeView.tsx        Collapsible JSON tree for structured data display
│   ├── TokenUsagePanel.tsx     Token usage breakdown display
│   ├── zoneStatusModel.ts      Zone artwork/control TypeScript interfaces
│   └── zoneStatusUtils.ts      parseJson, imageCacheKey, formatDuration helpers
└── utils/
    ├── formatMessageBody.ts    Chat sanitisation, tool name inference, payload parsing
    ├── parseDetailsMarkup.ts   Parse <extended_info> and <list> tags into renderable segments
    ├── parseReferences.ts      Parse result reference tags for display
    ├── parseJson.ts            Safe JSON parse helper
    ├── parseGuidanceSections.ts Parse guidance markdown into structured sections
    ├── sanitiseTtsText.ts      Strip markdown for TTS
    ├── trendData.ts            Time-series data utilities for diagnostics
    └── uuid.ts                 UUID generation (crypto.randomUUID with fallback)
```

---

## 3. Data Flow

```
        Backend (port 8080)
               ▲
               │  single WebSocket
               ▼
    ┌────────────────────────────┐
    │ WebSocketProvider          │
    │   messages: Message[]      │
    │   sendMessage(ch, body)    │
    └─────────────┬──────────────┘
                  │ useWebSocket()
   ┌──────────────┼─────────────┬──────────────────┬─────────┐
   ▼              ▼             ▼                  ▼         ▼
 ChatPanel  ZoneStatusPanel  AnalysisBrowser   App      Diagnostic
                                            (default-      suite
                                              zone-     (see below)
                                             update)
```

The "diagnostic suite" is a family of components and hooks that all read the same diagnostic channels (`agent-outputs`, `tool-outputs`, `errors`, `usage-metrics`, `llm-diagnostics`): `useDiagnostics`, `HistoryWindow`, `LlmDiagnosticsPanel`, `PromptBudgetPanel`, `RequestSummaryPanel`, `SessionSummaryBar`. They're grouped because they share a data substrate, not because there's a single parent component. `ChatPanel` and `ZoneStatusPanel` also touch some of these channels for narrower purposes — see section 5 for the full per-channel routing.

Every inbound WS message is appended to a single `messages: SocketMessage[]` array in the provider. Each consumer filters the array by channel via `useMemo`. This is the only communication path — there is no separate state store, event bus, or pub/sub.

---

## 4. Key Components

### WebSocketProvider

Establishes and maintains the WebSocket connection, stores all messages, and exposes `sendMessage()`. Reconnects after 2 seconds on close/error. Tracks active LLM call IDs incrementally from `call_started`/`call_completed`/`call_failed` events, exposing `isLlmActive` for the thinking indicator.

### ChatPanel

Primary chat interface. Features:
- Message display with `FormattedMessageBody` rendering and `RequestIdBadge` per message
- "Thinking..." pulsing indicator when an LLM call is in progress
- Rate-limit banners with countdown, retry button, and structured `rate-limit` channel support
- Auto-TTS: speaks inbound messages via `playServerTts()` when enabled, with smart truncation for long/listy output
- Enter-to-send input with Shift+Enter for newlines
- History browsing: only the most recent non-empty day loads on connect; scrolling up lazy-loads earlier days one at a time (skipping empty days), with day separators between them. Message timestamps show the real send/receive time.
- A `HistoryDatePicker` calendar icon in the header jumps to any day (loading the range up to what's in memory so history stays contiguous).

### History browsing & request sync

- **`useHistoryScrollback`** lazy-loads older days when the user nears the top: it fires a fire-and-forget request and anchors the viewport (holds distance-from-bottom) so the read position doesn't jump as a day prepends. One day per scroll-to-top, gated on a batch-complete token. Auto-fill (load until the viewport fills) is on for the chat, off for the sparse diagnostics panels.
- **Passive receive:** `WebSocketProvider` sorts and de-dupes every incoming message by a stable server message id (`utils/insertMessage`), so live messages, replay, and lazy-loaded history all assemble through one path with no request/response coupling.
- **Request sync:** clicking a `RequestIdBadge` on a request-aware surface (chat, Agents, Tools, Errors, Session Requests) focuses that request everywhere via `RequestFocusProvider` / `useRequestFocusSync` — every other open panel scrolls to it and flashes; the clicked panel stays put. `HistoryWindow` panels also lazy-load older days like the chat.

### Privacy & Data (Settings → Privacy & Data)

`Settings/PrivacyTab` — an action tab (not part of Save & Validate) with two destructive controls, each behind an inline confirm: **Clear conversation history** (chat transcript + working memory) and **Clear listening history**. Conversation clearing is disabled while a request is in flight.

### ZoneStatusPanel

Live Roon zone playback display. Features:
- Zone cards rebuilt from each `zone-snapshots` message (the agent's authoritative view of every zone)
- Per-zone shallow compare + identity reuse on unchanged zones to avoid card-level re-renders
- Artwork fetching via `roon-image-request/response` with deduplication and caching
- Transport controls (play, pause, previous, next) via `roon-control-request`
- Seek slider with client-side position interpolation between server updates
- Expanded artwork overlay (high-resolution, click to open/close)
- Zones disappear from the snapshot → their cards are removed

### FormattedMessageBody

Parses raw message bodies into structured display data. Handles:
- Chat message sanitisation (removes leaked structured fields)
- `<extended_info>` and `<list>` tag parsing into collapsible sections
- Source label extraction from `[Bracket Source]` first-line pattern
- JSON prettification and `plan` field detection
- Tool name inference for `tool-outputs` channel

### LlmDiagnosticsPanel / PromptBudgetPanel / RequestSummaryPanel

Live Diagnostics views showing real-time and historical LLM call data:
- Active call status, prompt token breakdowns, context provider sizes
- Interrupt arbiter decisions
- Rolling 60-second and session-wide token aggregation
- Per-request summary cards with timing, step counts, and tool usage

### Dev Mode

Toggled by double-clicking the Swarpius logo (persisted to localStorage via `useDevMode`). Once enabled, Ctrl+Shift+D toggles the diagnostics drawer and Ctrl+Shift+A toggles the analysis browser. Disabled by default.

---

## 5. WebSocket Channel Contract

The client's `ChannelId` type and the backend's `CHANNEL_*` constants must stay aligned. The table below covers the main channels and isn't exhaustive — see `app/constants.py` (backend) and `web-client/src/websocketContext.ts` (client) for the full set, including the Settings channels (`settings-*`), `validation-status`, the Roon Explorer (`roon-explorer-*`), `roon-core-status`, and `open-data-folder-request`.

| Channel | Direction | Consumer |
|---|---|---|
| `chat` | Both | ChatPanel (display + send) |
| `agent-outputs` | In | HistoryWindow, useDiagnostics, LlmDiagnosticsPanel, RequestSummaryPanel, SessionSummaryBar, ChatPanel |
| `tool-outputs` | In | HistoryWindow, useDiagnostics |
| `errors` | In | HistoryWindow, useDiagnostics, useChatBannerManager (banners on ChatPanel), SessionSummaryBar |
| `usage-metrics` | In | HistoryWindow, useDiagnostics, SessionSummaryBar |
| `llm-diagnostics` | In | LlmDiagnosticsPanel, PromptBudgetPanel, RequestSummaryPanel, useDiagnostics, WebSocketProvider (active call tracking) |
| `rate-limit` | In | useChatBannerManager (banners on ChatPanel) |
| `zone-snapshots` | In | ZoneStatusPanel (full zone state, re-emitted on any change) |
| `roon-image-request` | Out | ZoneStatusPanel |
| `roon-image-response` | In | ZoneStatusPanel |
| `roon-control-request` | Out | ZoneStatusPanel (via useRoonCommands) |
| `roon-control-response` | In | ZoneStatusPanel |
| `feature-availability` | In | ZoneStatusPanel |
| `feature-verify-request` | Out | useRoonCommands (called from ZoneStatusPanel) |
| `queue-updates` | In | ZoneStatusPanel |
| `default-zone-update` | In | App (DefaultZoneBadge state) |
| `session-control-request` | Out | ChatPanel (retry_now) |
| `session-control-response` | In | useChatBannerManager (banners on ChatPanel) |
| `history-request` | Out | WebSocketProvider (`requestHistory` / `requestHistoryRange`) — fire-and-forget day / range load |
| `history-cursor` | In | WebSocketProvider (passive: whether older history exists; closes a load batch) |
| `clear-conversation-request` / `-response` | Both | Settings/PrivacyTab |
| `clear-listening-history-request` / `-response` | Both | Settings/PrivacyTab |
| `analysis-*` | Both | AnalysisBrowser (covers `analysis-list-*`, `-detail-*`, `-run-*`, `-metrics-*`, `-update`, `-feedback-*`, `-result-handle-*`, `-request-logs-*`) |
