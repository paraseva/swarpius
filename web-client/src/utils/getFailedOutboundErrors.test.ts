import { describe, it, expect } from 'vitest'
import type { SocketMessage } from '../websocketContext'
import { getFailedOutboundErrors } from './getFailedOutboundErrors'

function outbound(clientMsgId: string, body = 'hi'): SocketMessage {
  return {
    id: `out-${clientMsgId}`,
    channel: 'chat',
    direction: 'outbound',
    body,
    payload: undefined,
    timestamp: 0,
    meta: { client_msg_id: clientMsgId },
  }
}

function assignment(requestId: string, clientMsgId: string): SocketMessage {
  return {
    id: `assign-${requestId}`,
    channel: 'agent-outputs',
    direction: 'inbound',
    body: '',
    payload: {
      event_type: 'request_id_assignment',
      request_id: requestId,
      client_msg_id: clientMsgId,
    },
    timestamp: 0,
  }
}

function errorMsg(requestId: string, error: string): SocketMessage {
  return {
    id: `err-${requestId}`,
    channel: 'errors',
    direction: 'inbound',
    body: '',
    payload: {
      source: '[Request]',
      error,
      request_id: requestId,
    },
    timestamp: 0,
  }
}

describe('getFailedOutboundErrors', () => {
  it('returns an empty map when no failures are present', () => {
    const m = getFailedOutboundErrors([
      outbound('m1'), assignment('rq-1', 'm1'),
    ])
    expect(m.size).toBe(0)
  })

  it('pairs an errors-channel entry to its outbound client_msg_id', () => {
    const m = getFailedOutboundErrors([
      outbound('m1'),
      assignment('rq-1', 'm1'),
      errorMsg('rq-1', 'AnthropicException - Server disconnected'),
    ])
    expect(m.size).toBe(1)
    expect(m.get('m1')).toBe('AnthropicException - Server disconnected')
  })

  it('ignores errors for request_ids not bound to a known outbound', () => {
    const m = getFailedOutboundErrors([
      outbound('m1'),
      assignment('rq-1', 'm1'),
      errorMsg('rq-unknown', 'orphan error'),
    ])
    expect(m.size).toBe(0)
  })

  it('records the latest error when multiple errors share a request_id', () => {
    const m = getFailedOutboundErrors([
      outbound('m1'),
      assignment('rq-1', 'm1'),
      errorMsg('rq-1', 'first failure'),
      errorMsg('rq-1', 'second failure'),
    ])
    expect(m.get('m1')).toBe('second failure')
  })

  it('falls back to a generic message when the error field is missing', () => {
    const m = getFailedOutboundErrors([
      outbound('m1'),
      assignment('rq-1', 'm1'),
      {
        id: 'err-bare',
        channel: 'errors',
        direction: 'inbound',
        body: '',
        payload: { source: '[Request]', request_id: 'rq-1' },
        timestamp: 0,
      },
    ])
    expect(m.get('m1')).toBe('Request failed')
  })

  it('does not attribute a replayed old-session error to a new bubble with the recycled request_id', () => {
    // Request IDs recycle across server restarts; the replay buffer
    // mixes old failures with new bubbles. Stream-order resolution
    // attaches each error to the assignment current at its position.
    const m = getFailedOutboundErrors([
      outbound('m1-old'),
      assignment('rq-c02-0001', 'm1-old'),
      errorMsg('rq-c02-0001', 'AnthropicException — from the old session'),
      outbound('m2-new'),
      assignment('rq-c02-0001', 'm2-new'),
    ])
    expect(m.get('m1-old')).toBe('AnthropicException — from the old session')
    expect(m.has('m2-new')).toBe(false)
  })

  it('ignores errors-channel messages with no request_id', () => {
    const m = getFailedOutboundErrors([
      outbound('m1'),
      assignment('rq-1', 'm1'),
      {
        id: 'err-noid',
        channel: 'errors',
        direction: 'inbound',
        body: '',
        payload: { source: '[generic]', error: 'noise' },
        timestamp: 0,
      },
    ])
    expect(m.size).toBe(0)
  })
})
