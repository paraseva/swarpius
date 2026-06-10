import React from 'react'
import type { ConnectionStatus } from '../websocketContext'

const isFailure = (s: ConnectionStatus | null): boolean => s === 'closed' || s === 'error'

/**
 * Count consecutive WebSocket failure *cycles* — transitions into a
 * failure state without an intervening 'open'. The provider emits both
 * an error and a close for a single failed attempt, so counting cycles
 * (not raw events) keeps one failed attempt as one. Resets to zero once
 * the connection opens.
 */
export function useConnectionFailureCount(status: ConnectionStatus): number {
  const previousStatus = React.useRef<ConnectionStatus | null>(null)
  const [failureCount, setFailureCount] = React.useState(0)

  React.useEffect(() => {
    const prev = previousStatus.current
    if (isFailure(status) && !isFailure(prev)) {
      setFailureCount((n) => n + 1)
    } else if (status === 'open') {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFailureCount(0)
    }
    previousStatus.current = status
  }, [status])

  return failureCount
}
