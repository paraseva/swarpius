import { describe, expect, it } from 'vitest'
import { lastPreviousSessionIndex } from './previousSessionDivider'
import type { SocketMessage } from '../websocketContext'

const msg = (id: string, previous?: boolean): SocketMessage => ({
  id,
  channel: 'chat',
  direction: 'outbound',
  body: '',
  timestamp: 0,
  ...(previous ? { meta: { previous_session: true } } : {}),
})

describe('lastPreviousSessionIndex', () => {
  it('returns -1 for an empty list', () => {
    expect(lastPreviousSessionIndex([])).toBe(-1)
  })

  it('returns -1 when nothing is from a previous session', () => {
    expect(lastPreviousSessionIndex([msg('a'), msg('b')])).toBe(-1)
  })

  it('returns the index of the last previous-session message (boundary)', () => {
    expect(
      lastPreviousSessionIndex([msg('a', true), msg('b', true), msg('c'), msg('d')]),
    ).toBe(1)
  })

  it('handles an all-previous list (divider trails at the end)', () => {
    expect(lastPreviousSessionIndex([msg('a', true), msg('b', true)])).toBe(1)
  })
})
