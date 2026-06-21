import { describe, expect, it } from 'vitest'
import { insertMessage } from './insertMessage'
import { type SocketMessage } from '../websocketContext'

function msg(timestamp: number, messageId?: number, id = `local-${timestamp}-${messageId ?? 'x'}`): SocketMessage {
  return {
    id,
    channel: 'chat',
    direction: 'inbound',
    body: '',
    timestamp,
    meta: messageId === undefined ? undefined : { message_id: messageId },
  }
}

describe('insertMessage', () => {
  it('appends a newer message to the end', () => {
    const out = insertMessage([msg(100, 1)], msg(200, 2))
    expect(out.map((m) => m.timestamp)).toEqual([100, 200])
  })

  it('inserts an older message before newer ones', () => {
    const out = insertMessage([msg(200, 2)], msg(100, 1))
    expect(out.map((m) => m.timestamp)).toEqual([100, 200])
  })

  it('inserts into the middle by timestamp', () => {
    const out = insertMessage([msg(100, 1), msg(300, 3)], msg(200, 2))
    expect(out.map((m) => m.timestamp)).toEqual([100, 200, 300])
  })

  it('orders ties by server message id', () => {
    const out = insertMessage([msg(100, 5)], msg(100, 2))
    expect(out.map((m) => m.meta?.message_id)).toEqual([2, 5])
  })

  it('dedupes by server message id and returns the same reference', () => {
    const start = [msg(100, 1), msg(200, 2)]
    const out = insertMessage(start, msg(200, 2))
    expect(out).toBe(start)
  })

  it('appends a live message (no server id) at the end', () => {
    const out = insertMessage([msg(100, 1)], msg(150))
    expect(out.map((m) => m.timestamp)).toEqual([100, 150])
    // a second live message with no id is not treated as a duplicate
    const out2 = insertMessage(out, msg(160))
    expect(out2).toHaveLength(3)
  })
})
