import React from 'react'
import s from './SessionSummaryBar.module.css'
import { parseJson } from '../utils/parseJson'
import { type SocketMessage } from '../websocketContext'

interface SessionSummaryBarProps {
  messages: SocketMessage[]
}

interface UsageSnapshot {
  session_totals?: {
    total_tokens?: number
    cache_read_input_tokens?: number
    cost_usd?: number
  }
}

interface RequestCompletePayload {
  event_type?: string
  status?: string
}

export const SessionSummaryBar: React.FC<SessionSummaryBarProps> = ({ messages }) => {
  const summary = React.useMemo(() => {
    let totalRequests = 0
    let incompleteCount = 0
    let errorCount = 0
    let totalDurationMs = 0
    let latestSessionTokens = 0
    let latestCacheReadTokens = 0
    let latestSessionCost = 0

    for (const message of messages) {
      if (message.direction !== 'inbound') continue

      if (message.channel === 'agent-outputs') {
        const event = parseJson<RequestCompletePayload & { total_duration_ms?: number }>(message.payload ?? message.body)
        if (event?.event_type === 'request_complete') {
          totalRequests += 1
          totalDurationMs += event.total_duration_ms ?? 0
          if (event.status && event.status !== 'completed' && event.status !== 'awaiting_user_response') {
            incompleteCount += 1
          }
        }
      }

      if (message.channel === 'errors') {
        errorCount += 1
      }

      if (message.channel === 'usage-metrics') {
        const snapshot = parseJson<UsageSnapshot>(message.payload ?? message.body)
        if (snapshot?.session_totals?.total_tokens != null) {
          latestSessionTokens = snapshot.session_totals.total_tokens
        }
        if (snapshot?.session_totals?.cache_read_input_tokens != null) {
          latestCacheReadTokens = snapshot.session_totals.cache_read_input_tokens
        }
        if (snapshot?.session_totals?.cost_usd != null) {
          latestSessionCost = snapshot.session_totals.cost_usd
        }
      }
    }

    const avgMs = totalRequests > 0 ? totalDurationMs / totalRequests : 0

    return { totalRequests, incompleteCount, errorCount, avgMs, latestSessionTokens, latestCacheReadTokens, latestSessionCost }
  }, [messages])

  if (summary.totalRequests === 0 && summary.latestSessionTokens === 0) return null

  return (
    <dl className={s.bar} role="status">
      <div className={s.item}>
        <dt className="sr-only">Requests</dt>
        <dd>{summary.totalRequests} {summary.totalRequests === 1 ? 'request' : 'requests'}</dd>
      </div>
      {summary.incompleteCount > 0 ? (
        <div className={s.item}>
          <dt className="sr-only">Incomplete</dt>
          <dd className={s.warnings}>{summary.incompleteCount} incomplete</dd>
        </div>
      ) : null}
      {summary.errorCount > 0 ? (
        <div className={s.item}>
          <dt className="sr-only">Errors</dt>
          <dd className={s.errors}>{summary.errorCount} {summary.errorCount === 1 ? 'error' : 'errors'}</dd>
        </div>
      ) : null}
      {summary.totalRequests > 0 ? (
        <div className={s.item}>
          <dt className="sr-only">Average duration</dt>
          <dd>avg {(summary.avgMs / 1000).toFixed(1)}s</dd>
        </div>
      ) : null}
      <div className={s.item}>
        <dt className="sr-only">Tokens</dt>
        <dd>
          {(summary.latestSessionTokens - summary.latestCacheReadTokens).toLocaleString()} tokens
          {summary.latestCacheReadTokens > 0 ? <span className="token-cached"> (+{summary.latestCacheReadTokens.toLocaleString()} cached)</span> : ''}
        </dd>
      </div>
      {summary.latestSessionCost > 0 ? (
        <div className={s.item}>
          <dt className="sr-only">Cost</dt>
          <dd>${summary.latestSessionCost.toFixed(3)}</dd>
        </div>
      ) : null}
    </dl>
  )
}
