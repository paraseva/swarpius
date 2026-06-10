import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { type SocketMessage } from '../websocketContext'
import { SessionSummaryBar } from './SessionSummaryBar'

afterEach(cleanup)

const makeMessages = (snapshot: unknown): SocketMessage[] => {
  // Seed one request_complete so the bar renders at all
  const now = Date.now()
  return [
    {
      direction: 'inbound',
      channel: 'agent-outputs',
      body: JSON.stringify({ event_type: 'request_complete', status: 'completed', total_duration_ms: 1000 }),
      payload: { event_type: 'request_complete', status: 'completed', total_duration_ms: 1000 },
      timestamp: now,
    } as SocketMessage,
    {
      direction: 'inbound',
      channel: 'usage-metrics',
      body: JSON.stringify(snapshot),
      payload: snapshot,
      timestamp: now + 1,
    } as SocketMessage,
  ]
}

describe('SessionSummaryBar cost display', () => {
  it('shows session cost rounded to 3 decimal places', () => {
    const messages = makeMessages({
      session_totals: { total_tokens: 1200, cost_usd: 0.01234 },
    })
    render(<SessionSummaryBar messages={messages} />)
    expect(screen.getByText('$0.012')).toBeInTheDocument()
  })

  it('omits cost when session cost is zero or missing', () => {
    const messages = makeMessages({
      session_totals: { total_tokens: 1200 },
    })
    render(<SessionSummaryBar messages={messages} />)
    expect(screen.queryByText(/^\$/)).not.toBeInTheDocument()
  })

  it('uses the latest snapshot when multiple are present', () => {
    const base = Date.now()
    const messages: SocketMessage[] = [
      {
        direction: 'inbound',
        channel: 'agent-outputs',
        body: JSON.stringify({ event_type: 'request_complete', status: 'completed', total_duration_ms: 1000 }),
        payload: { event_type: 'request_complete', status: 'completed', total_duration_ms: 1000 },
        timestamp: base,
      } as SocketMessage,
      {
        direction: 'inbound',
        channel: 'usage-metrics',
        body: JSON.stringify({ session_totals: { total_tokens: 1000, cost_usd: 0.005 } }),
        payload: { session_totals: { total_tokens: 1000, cost_usd: 0.005 } },
        timestamp: base + 1,
      } as SocketMessage,
      {
        direction: 'inbound',
        channel: 'usage-metrics',
        body: JSON.stringify({ session_totals: { total_tokens: 2500, cost_usd: 0.0175 } }),
        payload: { session_totals: { total_tokens: 2500, cost_usd: 0.0175 } },
        timestamp: base + 2,
      } as SocketMessage,
    ]
    render(<SessionSummaryBar messages={messages} />)
    expect(screen.getByText('$0.018')).toBeInTheDocument()
    expect(screen.queryByText('$0.005')).not.toBeInTheDocument()
  })
})
