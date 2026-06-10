import { describe, it, expect } from 'vitest'
import type { SocketMessage } from '../websocketContext'
import { getDirectiveOutboundIds } from './getDirectiveOutboundIds'

function ack(clientMsgId: string): SocketMessage {
  return {
    id: `ack-${clientMsgId}`,
    channel: 'agent-outputs',
    direction: 'inbound',
    body: '',
    payload: {
      event_type: 'control_command_acknowledged',
      client_msg_id: clientMsgId,
      action: 'interrupt_only',
    },
    timestamp: 0,
  }
}

describe('getDirectiveOutboundIds', () => {
  it('returns an empty set when no acknowledgements are present', () => {
    const set = getDirectiveOutboundIds([])
    expect(set.size).toBe(0)
  })

  it('collects the client_msg_id from each acknowledgement', () => {
    const set = getDirectiveOutboundIds([ack('m1'), ack('m2')])
    expect(set.has('m1')).toBe(true)
    expect(set.has('m2')).toBe(true)
    expect(set.size).toBe(2)
  })

  it('ignores acknowledgements missing client_msg_id', () => {
    const malformed: SocketMessage = {
      id: 'bad', channel: 'agent-outputs', direction: 'inbound',
      body: '', payload: { event_type: 'control_command_acknowledged' }, timestamp: 0,
    }
    const set = getDirectiveOutboundIds([malformed, ack('m1')])
    expect(set.size).toBe(1)
    expect(set.has('m1')).toBe(true)
  })

  it('ignores non-acknowledgement agent-outputs events that happen to carry a client_msg_id', () => {
    const other: SocketMessage = {
      id: 'rid', channel: 'agent-outputs', direction: 'inbound',
      body: '',
      payload: { event_type: 'request_id_assignment', request_id: 'rq-1', client_msg_id: 'm1' },
      timestamp: 0,
    }
    const set = getDirectiveOutboundIds([other])
    expect(set.size).toBe(0)
  })

  it('ignores events on other channels', () => {
    const stray: SocketMessage = {
      id: 'x', channel: 'chat', direction: 'inbound',
      body: '',
      payload: { event_type: 'control_command_acknowledged', client_msg_id: 'm1' },
      timestamp: 0,
    }
    const set = getDirectiveOutboundIds([stray])
    expect(set.size).toBe(0)
  })
})
