import type { SocketMessage } from '../websocketContext'
import { outboundClientMsgId } from './outboundClientMsgId'

/**
 * Map outbound chat-bubble client_msg_ids to the error text reported
 * for their backend request. Pairs by ``request_id_assignment``
 * (client_msg_id ↔ request_id) and matches against ``errors``-channel
 * emissions from ``RequestFailed``.
 *
 * Single-pass in stream order so a replayed old-session error doesn't
 * misattribute to a new bubble that recycled the same request_id —
 * each error resolves against the assignment state at its arrival
 * point.
 */
export function getFailedOutboundErrors(
  messages: SocketMessage[],
): Map<string, string> {
  const outboundIds = new Set<string>()
  const reverse = new Map<string, string>()
  const failures = new Map<string, string>()

  for (const m of messages) {
    if (m.channel === 'chat' && m.direction === 'outbound') {
      outboundIds.add(outboundClientMsgId(m))
      continue
    }

    if (m.channel === 'agent-outputs' && m.direction === 'inbound') {
      const payload = m.payload && typeof m.payload === 'object' && !Array.isArray(m.payload)
        ? (m.payload as Record<string, unknown>)
        : undefined
      if (payload?.event_type === 'request_id_assignment') {
        const requestId = payload.request_id
        const clientMsgId = payload.client_msg_id
        if (typeof requestId === 'string' && typeof clientMsgId === 'string'
          && outboundIds.has(clientMsgId)) {
          reverse.set(requestId, clientMsgId)
        }
      }
      continue
    }

    if (m.channel === 'errors' && m.direction === 'inbound') {
      const payload = m.payload && typeof m.payload === 'object' && !Array.isArray(m.payload)
        ? (m.payload as Record<string, unknown>)
        : undefined
      const requestId = payload?.request_id
      if (typeof requestId !== 'string') continue
      const clientMsgId = reverse.get(requestId)
      if (!clientMsgId) continue
      const errorText = payload?.error
      failures.set(
        clientMsgId,
        typeof errorText === 'string' && errorText ? errorText : 'Request failed',
      )
    }
  }

  return failures
}
