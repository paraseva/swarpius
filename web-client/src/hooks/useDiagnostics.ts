import React from 'react'
import { parseJson } from '../utils/parseJson'
import { type SocketMessage } from '../websocketContext'

const DIAGNOSTIC_CHANNELS = [
  'agent-outputs',
  'tool-outputs',
  'errors',
  'usage-metrics',
  'llm-diagnostics',
] as const
type DiagnosticChannel = (typeof DIAGNOSTIC_CHANNELS)[number]

export interface UsageSnapshot {
  source?: string
  outcome?: string
  call?: {
    input_tokens?: number
    output_tokens?: number
    total_tokens?: number
    outcome?: string
    cache_creation_input_tokens?: number
    cache_read_input_tokens?: number
    cost_usd?: number
  }
  session_totals?: {
    input_tokens?: number
    output_tokens?: number
    total_tokens?: number
    cache_creation_input_tokens?: number
    cache_read_input_tokens?: number
    cost_usd?: number
  }
  tokens_per_minute?: {
    input_tokens?: number
    output_tokens?: number
    total_tokens?: number
    cache_read_input_tokens?: number
  }
  requests_per_minute?: {
    request_count?: number
    window_seconds?: number
  }
  session_breakdown?: {
    success_input_tokens?: number
    success_output_tokens?: number
    success_total_tokens?: number
    rate_limited_retry_input_tokens_estimated?: number
    rate_limited_retry_total_tokens_estimated?: number
  }
  tokens_per_minute_breakdown?: {
    success_total_tokens?: number
    rate_limited_retry_input_tokens_estimated?: number
    rate_limited_retry_total_tokens_estimated?: number
    window_seconds?: number
  }
}

const EMPTY_COUNTS: Record<DiagnosticChannel, number> = {
  'agent-outputs': 0,
  'tool-outputs': 0,
  errors: 0,
  'usage-metrics': 0,
  'llm-diagnostics': 0,
}


export const useDiagnostics = (messages: SocketMessage[]) => {
  const [isDiagnosticsOpen, setIsDiagnosticsOpen] = React.useState(false)
  const [seenCounts, setSeenCounts] = React.useState<Record<DiagnosticChannel, number>>(EMPTY_COUNTS)

  const inboundDiagnostics = React.useMemo(
    () =>
      messages.filter(
        (message) =>
          message.direction === 'inbound' &&
          DIAGNOSTIC_CHANNELS.includes(message.channel as DiagnosticChannel),
      ),
    [messages],
  )

  const currentCounts = React.useMemo(() => {
    const counts = { ...EMPTY_COUNTS }
    for (const message of inboundDiagnostics) {
      counts[message.channel as DiagnosticChannel] += 1
    }
    return counts
  }, [inboundDiagnostics])

  // Snapshot the current message counts as "seen" whenever the
  // drawer is open and new messages arrive. seenCounts is the
  // baseline; unread is derived (currentCounts - seenCounts). Can't
  // be pure derived state since the baseline needs to advance as
  // messages are observed.
  React.useEffect(() => {
    if (!isDiagnosticsOpen) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSeenCounts(currentCounts)
  }, [currentCounts, isDiagnosticsOpen])

  const unreadCounts = React.useMemo(
    () => ({
      'agent-outputs': Math.max(0, currentCounts['agent-outputs'] - seenCounts['agent-outputs']),
      'tool-outputs': Math.max(0, currentCounts['tool-outputs'] - seenCounts['tool-outputs']),
      errors: Math.max(0, currentCounts.errors - seenCounts.errors),
      'usage-metrics': Math.max(0, currentCounts['usage-metrics'] - seenCounts['usage-metrics']),
      'llm-diagnostics': Math.max(0, currentCounts['llm-diagnostics'] - seenCounts['llm-diagnostics']),
    }),
    [currentCounts, seenCounts],
  )

  const totalUnread =
    unreadCounts['agent-outputs'] +
    unreadCounts['tool-outputs'] +
    unreadCounts.errors +
    unreadCounts['usage-metrics'] +
    unreadCounts['llm-diagnostics']

  const latestUsage = React.useMemo(() => {
    for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
      const message = messages[idx]
      if (message.direction !== 'inbound' || message.channel !== 'usage-metrics') continue
      const parsed = parseJson<UsageSnapshot>(message.payload ?? message.body)
      if (parsed) return parsed
    }
    return null
  }, [messages])

  const toggleDiagnostics = () => {
    setIsDiagnosticsOpen((open) => {
      if (!open) {
        setSeenCounts(currentCounts)
      }
      return !open
    })
  }

  return {
    isDiagnosticsOpen,
    setIsDiagnosticsOpen,
    unreadCounts,
    totalUnread,
    latestUsage,
    toggleDiagnostics,
  }
}
