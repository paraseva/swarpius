import type { SocketMessage } from '../websocketContext'

/**
 * Index of the last message belonging to a previous server session
 * (replayed under `--keep-history`, tagged `meta.previous_session`).
 * A divider rendered after this index marks the boundary above which
 * the assistant has no memory of the conversation. Returns -1 if there
 * are no previous-session messages.
 */
export function lastPreviousSessionIndex(messages: SocketMessage[]): number {
  let idx = -1
  for (let i = 0; i < messages.length; i += 1) {
    if (messages[i].meta?.previous_session === true) idx = i
  }
  return idx
}
