import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import {
  WebSocketContext,
  type SocketMessage,
  type WebSocketContextValue,
} from '../websocketContext'
import { LlmDiagnosticsPanel } from './LlmDiagnosticsPanel'

function llmDiag(payload: Record<string, unknown>, id = `id-${Math.random()}`): SocketMessage {
  return {
    id,
    channel: 'llm-diagnostics',
    direction: 'inbound',
    body: '',
    payload,
    timestamp: 0,
  }
}

const baseCtx: WebSocketContextValue = {
  status: 'open',
  messages: [],
  sendMessage: () => '',
  isLlmActive: false,
  latestZoneSnapshot: null,
  connectionGeneration: 0,
  isRestarting: false,
  markRestarting: () => {},
  trimmedCount: 0,
}

function renderPanel(messages: SocketMessage[]) {
  const ctx: WebSocketContextValue = { ...baseCtx, messages, isLlmActive: messages.length > 0 }
  return render(
    <WebSocketContext.Provider value={ctx}>
      <LlmDiagnosticsPanel />
    </WebSocketContext.Provider>,
  )
}

describe('LlmDiagnosticsPanel — active call tracking', () => {
  afterEach(cleanup)

  it('keeps showing the coordinator as active when a nested arbiter call starts and completes mid-flight', () => {
    renderPanel([
      llmDiag({ event_type: 'call_started', call_id: 'rq-c01-0001', agent_name: 'Coordinator', model: 'sonnet', timestamp_ms: 1000 }, 'a'),
      llmDiag({ event_type: 'call_started', call_id: 'arb-1', agent_name: 'Arbiter', model: 'haiku', timestamp_ms: 2000 }, 'b'),
      llmDiag({ event_type: 'call_completed', call_id: 'arb-1' }, 'c'),
    ])
    expect(screen.queryByText(/LLM call in progress/)).toBeInTheDocument()
    expect(screen.queryByText(/No active LLM call/)).toBeNull()
    expect(screen.getByText(/Agent: Coordinator/)).toBeInTheDocument()
  })

  it('shows the most recent active call when multiple overlap', () => {
    renderPanel([
      llmDiag({ event_type: 'call_started', call_id: 'rq-c01-0001', agent_name: 'Coordinator', model: 'sonnet', timestamp_ms: 1000 }, 'a'),
      llmDiag({ event_type: 'call_started', call_id: 'arb-1', agent_name: 'Arbiter', model: 'haiku', timestamp_ms: 2000 }, 'b'),
    ])
    expect(screen.getByText(/Agent: Arbiter/)).toBeInTheDocument()
  })

  it('surfaces a sticky "Last call failed" line when the latest call_failed has no successor', () => {
    renderPanel([
      llmDiag({ event_type: 'call_started', call_id: 'rq-c01-0001', agent_name: 'Coordinator', model: 'sonnet', timestamp_ms: 1000 }, 'a'),
      llmDiag({ event_type: 'call_failed', call_id: 'rq-c01-0001', error: 'AnthropicException - Server disconnected' }, 'b'),
    ])
    expect(screen.queryByText(/No active LLM call/)).toBeInTheDocument()
    expect(screen.getByText(/Last call failed/)).toBeInTheDocument()
    expect(screen.getByText(/AnthropicException - Server disconnected/)).toBeInTheDocument()
  })

  it('clears the sticky failure when a fresh call_started arrives', () => {
    renderPanel([
      llmDiag({ event_type: 'call_started', call_id: 'rq-c01-0001', agent_name: 'Coordinator', model: 'sonnet', timestamp_ms: 1000 }, 'a'),
      llmDiag({ event_type: 'call_failed', call_id: 'rq-c01-0001', error: 'boom' }, 'b'),
      llmDiag({ event_type: 'call_started', call_id: 'rq-c01-0002', agent_name: 'Coordinator', model: 'sonnet', timestamp_ms: 3000 }, 'c'),
    ])
    expect(screen.queryByText(/Last call failed/)).toBeNull()
    expect(screen.getByText(/LLM call in progress/)).toBeInTheDocument()
  })
})

describe('LlmDiagnosticsPanel — Last Interrupt Decision', () => {
  afterEach(cleanup)

  it('shows arbiter "queue" decisions in the panel', () => {
    renderPanel([
      llmDiag({
        event_type: 'interrupt_decision',
        decision_source: 'arbiter',
        action: 'queue',
        confidence: 0.85,
        reason: 'Same topic; defer',
      }, 'd1'),
    ])
    expect(screen.queryByText(/Last Interrupt Decision/)).toBeInTheDocument()
    expect(screen.getByText('Arbiter')).toBeInTheDocument()
    expect(screen.getByText('queue')).toBeInTheDocument()
    expect(screen.getByText(/confidence 0\.85/)).toBeInTheDocument()
    expect(screen.getByText('Same topic; defer')).toBeInTheDocument()
  })

  it('flags arbiter fallback distinctly', () => {
    renderPanel([
      llmDiag({
        event_type: 'interrupt_decision',
        decision_source: 'arbiter_fallback',
        action: 'queue',
        confidence: 0,
        reason: 'Arbiter failed; defaulting to queue',
      }, 'd2'),
    ])
    expect(screen.getByText('Arbiter (fallback)')).toBeInTheDocument()
  })

  it('shows keyword directives as a decision source', () => {
    renderPanel([
      llmDiag({
        event_type: 'interrupt_decision',
        decision_source: 'keyword',
        action: 'interrupt_only',
        reason: "Keyword directive matched: 'stop'",
      }, 'd3'),
    ])
    expect(screen.getByText('Keyword directive')).toBeInTheDocument()
    expect(screen.getByText('interrupt_only')).toBeInTheDocument()
  })
})
