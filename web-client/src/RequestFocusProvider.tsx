import React from 'react'
import { type FocusedRequest, RequestFocusContext } from './requestFocusContext'

/** Holds the currently-focused request so request-aware panels can sync to it
 *  when a request-id badge is clicked. */
export const RequestFocusProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [focusedRequest, setFocusedRequest] = React.useState<FocusedRequest | null>(null)
  const nonceRef = React.useRef(0)

  // Pure broadcast: each request-aware panel loads that request's day for its
  // own channel and scrolls itself (see useRequestFocusSync) — no shared state.
  const focusRequest = React.useCallback((requestId: string, sourceKey: string, day: string | null = null) => {
    nonceRef.current += 1
    setFocusedRequest({ requestId, day, sourceKey, nonce: nonceRef.current })
  }, [])

  const value = React.useMemo(
    () => ({ focusedRequest, focusRequest }),
    [focusedRequest, focusRequest],
  )

  return <RequestFocusContext value={value}>{children}</RequestFocusContext>
}
