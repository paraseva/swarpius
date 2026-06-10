import type { SocketMessage } from '../websocketContext'

/**
 * Returns the stable client_msg_id for an outbound chat message:
 * ``meta.client_msg_id`` on a replayed message, else the live
 * record's ``id`` (which is also what was sent as ``client_msg_id``
 * on the WS frame).
 */
export function outboundClientMsgId(m: SocketMessage): string {
  const fromMeta = m.meta?.client_msg_id
  if (typeof fromMeta === 'string' && fromMeta) return fromMeta
  return m.id
}
