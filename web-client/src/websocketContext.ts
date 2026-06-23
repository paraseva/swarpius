import { createContext, useContext } from 'react'

export type ChannelId =
  | 'chat'
  | 'tool-outputs'
  | 'agent-outputs'
  | 'errors'
  | 'zone-snapshots'
  | 'usage-metrics'
  | 'llm-diagnostics'
  | 'roon-image-request'
  | 'roon-image-response'
  | 'roon-control-request'
  | 'roon-control-response'
  | 'roon-core-status'
  | 'roon-explorer-request'
  | 'roon-explorer-response'
  | 'feature-availability'
  | 'feature-verify-request'
  | 'open-data-folder-request'
  | 'session-control-request'
  | 'session-control-response'
  | 'clear-conversation-request'
  | 'clear-conversation-response'
  | 'rate-limit'
  | 'analysis-list-request'
  | 'analysis-list-response'
  | 'analysis-detail-request'
  | 'analysis-detail-response'
  | 'analysis-run-request'
  | 'analysis-run-response'
  | 'analysis-metrics-request'
  | 'analysis-metrics-response'
  | 'cost-metrics-request'
  | 'cost-metrics-response'
  | 'settings-read-request'
  | 'settings-read-response'
  | 'settings-save-request'
  | 'settings-save-response'
  | 'settings-reload-request'
  | 'settings-reload-response'
  | 'settings-test-request'
  | 'settings-test-response'
  | 'validation-status'
  | (string & {})

export type ConnectionStatus = 'connecting' | 'open' | 'closed' | 'error' | 'taken_over'

export interface SocketMessage {
  id: string
  channel: ChannelId
  direction: 'inbound' | 'outbound'
  body: string
  payload?: unknown
  meta?: Record<string, unknown>
  timestamp: number
}

export interface WebSocketContextValue {
  status: ConnectionStatus
  messages: SocketMessage[]
  sendMessage: (channel: ChannelId, body: string) => string
  /** Wipe the local message view. Used after the server confirms a
   *  conversation-history clear, so the UI reflects the now-empty store
   *  without waiting for a reconnect+replay. Optional so test fixtures need
   *  not supply it; the live provider always does. */
  clearMessages?: () => void
  /** Fire-and-forget request for the most recent non-empty day of history at
   *  or before `beforeMs`. With `channel`, only that channel is loaded (and its
   *  cursor echoed) so a diagnostics panel loads independently of the others.
   *  The reply arrives as ordinary messages (passive receive) plus a
   *  history-cursor signal — there is no response to await. */
  requestHistory?: (beforeMs: number, channel?: string) => void
  /** Fire-and-forget request for a contiguous range [startMs, endMs). With
   *  `channel`, scoped to that channel (and its cursor echoed). Used to fill the
   *  gap to an older day, keeping a channel's loaded history contiguous. */
  requestHistoryRange?: (startMs: number, endMs: number, channel?: string) => void
  /** Per-channel "scroll-back exhausted" flag (no older history past what's
   *  loaded), keyed by channel; absent ⇒ not yet known. Every panel loads its
   *  own channel, so there is no global equivalent. */
  reachedBeginningByChannel?: Record<string, boolean>
  /** Per-channel batch token: increments each time that channel's history batch
   *  finishes delivering (its history-cursor arrives), so scroll-back releases
   *  its in-flight guard exactly when the requested day is loaded. */
  historyBatchTokenByChannel?: Record<string, number>
  /** Whether any LLM call is currently in-flight (tracked incrementally). */
  isLlmActive: boolean
  /** Parsed payload of the most recent `zone-snapshots` message, or null
   *  if none received yet. Overwritten on each event — snapshots are
   *  high-volume (~1 Hz during playback) and have latest-only semantics,
   *  so they bypass the `messages` history to keep that array bounded. */
  latestZoneSnapshot: unknown
  /** Number of messages trimmed from the front of the array since mount. */
  trimmedCount: number
  /** Increments every time the socket opens. Components can use this as
   *  a remount key to wipe local state on reconnect — the server replays
   *  whatever still applies. */
  connectionGeneration: number
  /** True between a Restart click and the next successful WS
   *  reconnect. Drives the full-screen modal that blocks input while
   *  the agent is bouncing. */
  isRestarting: boolean
  /** Called by the Settings page when a Restart succeeds. */
  markRestarting: () => void
}

export const WebSocketContext = createContext<WebSocketContextValue | undefined>(undefined)

export const useWebSocket = (): WebSocketContextValue => {
  const ctx = useContext(WebSocketContext)
  if (!ctx) {
    throw new Error('useWebSocket must be used within a WebSocketProvider')
  }

  return ctx
}
