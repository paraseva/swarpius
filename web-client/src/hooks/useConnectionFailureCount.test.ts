import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useConnectionFailureCount } from './useConnectionFailureCount'
import type { ConnectionStatus } from '../websocketContext'

// Typing the prop as ConnectionStatus (not letting it narrow to the
// initial literal) lets rerender drive any status transition.
const setup = (initial: ConnectionStatus) =>
  renderHook(
    ({ status }: { status: ConnectionStatus }) => useConnectionFailureCount(status),
    { initialProps: { status: initial } },
  )

describe('useConnectionFailureCount', () => {
  it('starts at zero', () => {
    const { result } = setup('connecting')
    expect(result.current).toBe(0)
  })

  it('counts one failure cycle as one', () => {
    const { result, rerender } = setup('connecting')
    rerender({ status: 'closed' })
    expect(result.current).toBe(1)
  })

  it('treats error-then-close (one attempt) as a single cycle', () => {
    const { result, rerender } = setup('connecting')
    rerender({ status: 'error' })
    rerender({ status: 'closed' })
    expect(result.current).toBe(1)
  })

  it('counts two separate cycles as two', () => {
    const { result, rerender } = setup('connecting')
    rerender({ status: 'closed' })
    rerender({ status: 'connecting' })
    rerender({ status: 'closed' })
    expect(result.current).toBe(2)
  })

  it('resets to zero once the connection opens', () => {
    const { result, rerender } = setup('connecting')
    rerender({ status: 'closed' })
    rerender({ status: 'open' })
    expect(result.current).toBe(0)
  })
})
