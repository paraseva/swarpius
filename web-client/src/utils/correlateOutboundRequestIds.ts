import type { SocketMessage } from '../websocketContext'
import { outboundClientMsgId } from './outboundClientMsgId'

/**
 * Map outbound chat-bubble client_msg_ids to the backend request_id
 * assigned to each. Pairs by the ``client_msg_id`` the FE sent on the
 * chat frame and the server echoes on ``request_id_assignment``;
 * unmatched outbounds or assignments produce no mapping.
 *
 * Outbound key resolution falls back to ``meta.client_msg_id`` on
 * replayed messages — see :func:`outboundClientMsgId`.
 */
export function correlateOutboundRequestIds(
  messages: SocketMessage[],
): Map<string, string> {
  const outboundIds = new Set<string>()
  const map = new Map<string, string>()
  for (const m of messages) {
    if (m.channel === 'chat' && m.direction === 'outbound') {
      outboundIds.add(outboundClientMsgId(m))
      continue
    }
    if (m.channel !== 'agent-outputs' || m.direction !== 'inbound') continue
    const payload = m.payload && typeof m.payload === 'object' && !Array.isArray(m.payload)
      ? (m.payload as Record<string, unknown>)
      : undefined
    if (payload?.event_type !== 'request_id_assignment') continue
    const requestId = payload.request_id
    const clientMsgId = payload.client_msg_id
    if (typeof requestId !== 'string' || typeof clientMsgId !== 'string') continue
    if (!outboundIds.has(clientMsgId)) continue
    map.set(clientMsgId, requestId)
  }
  return map
}
