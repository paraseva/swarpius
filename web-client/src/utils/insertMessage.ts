import { type SocketMessage } from '../websocketContext'

/**
 * Insert a message into the array keeping it ordered by (timestamp, server
 * message id), deduping by server message id. This is the single passive
 * receive path: live messages (newest, no server id) land at the end;
 * historical/replayed messages (older, with a server id) sort into place; a
 * future server-pushed message would too. Returns the *same array reference*
 * when the message is a duplicate, so callers can skip a state update.
 */
export function insertMessage(
  messages: SocketMessage[],
  record: SocketMessage,
): SocketMessage[] {
  const mid = record.meta?.message_id
  if (typeof mid === 'number' && messages.some((m) => m.meta?.message_id === mid)) {
    return messages
  }

  const n = messages.length
  if (n === 0 || compareKey(record, messages[n - 1]) >= 0) {
    return [...messages, record]
  }

  // First index whose key is strictly greater than the record's.
  let lo = 0
  let hi = n
  while (lo < hi) {
    const mid2 = (lo + hi) >> 1
    if (compareKey(messages[mid2], record) <= 0) lo = mid2 + 1
    else hi = mid2
  }
  return [...messages.slice(0, lo), record, ...messages.slice(lo)]
}

function compareKey(a: SocketMessage, b: SocketMessage): number {
  if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp
  const am = (a.meta?.message_id as number | undefined) ?? Number.MAX_SAFE_INTEGER
  const bm = (b.meta?.message_id as number | undefined) ?? Number.MAX_SAFE_INTEGER
  return am - bm
}
