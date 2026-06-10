import { describe, it, expect } from 'vitest'
import type { SocketMessage } from '../websocketContext'
import { correlateOutboundRequestIds } from './correlateOutboundRequestIds'

/**
 * Contract: pair an outbound chat-bubble id to the backend request_id
 * by ``client_msg_id`` lookup. The FE sends the id on the chat frame
 * and the server echoes it on ``request_id_assignment``.
 */
function out(id: string): SocketMessage {
  return {
    id, channel: 'chat', direction: 'outbound',
    body: id, timestamp: 0,
  }
}

function assignment(requestId: string, clientMsgId?: string): SocketMessage {
  const payload: Record<string, unknown> = {
    event_type: 'request_id_assignment',
    request_id: requestId,
  }
  if (clientMsgId !== undefined) payload.client_msg_id = clientMsgId
  return {
    id: `evt-${requestId}`,
    channel: 'agent-outputs',
    direction: 'inbound',
    body: '',
    payload,
    timestamp: 0,
  }
}

describe('correlateOutboundRequestIds', () => {
  it('matches an outbound to the assignment carrying its client_msg_id', () => {
    const map = correlateOutboundRequestIds([out('m1'), assignment('rq-1', 'm1')])
    expect(map.get('m1')).toBe('rq-1')
  })

  it('pairs by id regardless of arrival order', () => {
    const messages: SocketMessage[] = [
      out('m1'),
      out('m2'),
      assignment('rq-2', 'm2'),
      assignment('rq-1', 'm1'),
    ]
    const map = correlateOutboundRequestIds(messages)
    expect(map.get('m1')).toBe('rq-1')
    expect(map.get('m2')).toBe('rq-2')
  })

  it('leaves an outbound unmapped when no assignment carries its id', () => {
    // Keyword-interrupt outbounds (stop/cancel/...) are not paired
    // with a request_id; surrounding outbounds must still pair to
    // theirs by direct lookup.
    const map = correlateOutboundRequestIds([
      out('m1'),
      out('stop-id'),
      out('m2'),
      assignment('rq-1', 'm1'),
      assignment('rq-2', 'm2'),
    ])
    expect(map.get('m1')).toBe('rq-1')
    expect(map.has('stop-id')).toBe(false)
    expect(map.get('m2')).toBe('rq-2')
  })

  it('ignores assignments whose client_msg_id has no matching outbound', () => {
    const map = correlateOutboundRequestIds([
      assignment('rq-old', 'm-gone'),
      out('m1'),
      assignment('rq-1', 'm1'),
    ])
    expect(map.get('m1')).toBe('rq-1')
    expect(map.has('m-gone')).toBe(false)
  })

  it('ignores assignments missing client_msg_id entirely', () => {
    const map = correlateOutboundRequestIds([
      out('m1'),
      assignment('rq-1'),
    ])
    expect(map.has('m1')).toBe(false)
  })

  it('pairs replayed outbounds by their persisted client_msg_id in meta', () => {
    // Replayed outbound gets a fresh local id; meta.client_msg_id
    // carries the value the server originally received.
    const replayedOutbound: SocketMessage = {
      id: 'fresh-replay-uuid', channel: 'chat', direction: 'outbound',
      body: 'hello', timestamp: 0,
      meta: { replay: true, direction: 'outbound', client_msg_id: 'original-fe-uuid' },
    }
    const replayedAssignment = assignment('rq-1', 'original-fe-uuid')
    const map = correlateOutboundRequestIds([replayedOutbound, replayedAssignment])
    expect(map.get('original-fe-uuid')).toBe('rq-1')
  })

  it('ignores non-assignment agent-outputs events', () => {
    const noise: SocketMessage = {
      id: 'noise', channel: 'agent-outputs', direction: 'inbound',
      body: '', payload: { event_type: 'something_else', client_msg_id: 'm1' }, timestamp: 0,
    }
    const map = correlateOutboundRequestIds([out('m1'), noise, assignment('rq-1', 'm1')])
    expect(map.get('m1')).toBe('rq-1')
  })
})
