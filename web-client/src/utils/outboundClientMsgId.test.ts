import { describe, it, expect } from 'vitest'
import type { SocketMessage } from '../websocketContext'
import { outboundClientMsgId } from './outboundClientMsgId'

describe('outboundClientMsgId', () => {
  it('returns the live outbound id when no meta is present', () => {
    const m: SocketMessage = {
      id: 'fe-uuid-1', channel: 'chat', direction: 'outbound',
      body: 'hi', timestamp: 0,
    }
    expect(outboundClientMsgId(m)).toBe('fe-uuid-1')
  })

  it('returns the persisted client_msg_id when present in meta', () => {
    const m: SocketMessage = {
      id: 'fresh-replay-id', channel: 'chat', direction: 'outbound',
      body: 'hi', timestamp: 0,
      meta: { replay: true, direction: 'outbound', client_msg_id: 'fe-uuid-1' },
    }
    expect(outboundClientMsgId(m)).toBe('fe-uuid-1')
  })

  it('falls back to id when meta.client_msg_id is not a string', () => {
    const m: SocketMessage = {
      id: 'fe-uuid-2', channel: 'chat', direction: 'outbound',
      body: 'hi', timestamp: 0,
      meta: { replay: true, client_msg_id: 42 },
    }
    expect(outboundClientMsgId(m)).toBe('fe-uuid-2')
  })
})
