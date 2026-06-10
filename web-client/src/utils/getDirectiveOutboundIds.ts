import type { SocketMessage } from '../websocketContext'

/**
 * Returns the set of outbound chat-bubble client_msg_ids that the
 * server has acknowledged as keyword directives (``stop``, ``cancel``
 * etc.) via ``control_command_acknowledged`` events.
 */
export function getDirectiveOutboundIds(
  messages: SocketMessage[],
): Set<string> {
  const ids = new Set<string>()
  for (const m of messages) {
    if (m.channel !== 'agent-outputs' || m.direction !== 'inbound') continue
    const payload = m.payload && typeof m.payload === 'object' && !Array.isArray(m.payload)
      ? (m.payload as Record<string, unknown>)
      : undefined
    if (payload?.event_type !== 'control_command_acknowledged') continue
    const clientMsgId = payload.client_msg_id
    if (typeof clientMsgId !== 'string') continue
    ids.add(clientMsgId)
  }
  return ids
}
