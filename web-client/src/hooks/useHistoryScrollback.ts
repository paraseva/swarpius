import React from 'react'
import { type SocketMessage } from '../websocketContext'

const TOP_THRESHOLD_PX = 80

/**
 * Lazy-load older history when the user scrolls near the top, preserving the
 * viewport position when older messages prepend (so the content the user is
 * reading doesn't jump).
 *
 * Fire-and-forget: it calls `onLoadMore(beforeMs)` and returns; the prepended
 * messages arrive via the passive receive. Preservation keys off the oldest
 * message changing — so it also handles a future server-initiated prepend, not
 * just our own request.
 */
export function useHistoryScrollback<T extends HTMLElement>(
  scrollRef: React.RefObject<T | null>,
  messages: SocketMessage[],
  onLoadMore: ((beforeMs: number) => void) | undefined,
  reachedBeginning: boolean,
): void {
  const loadingRef = React.useRef(false)
  const prevFirstIdRef = React.useRef<string | null>(null)
  const prevHeightRef = React.useRef(0)

  // Before paint: if the oldest message changed and content grew, an older
  // batch prepended — shift scrollTop by the added height to stay anchored.
  React.useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const firstId = messages.length ? messages[0].id : null
    if (
      prevFirstIdRef.current !== null &&
      firstId !== prevFirstIdRef.current &&
      el.scrollHeight > prevHeightRef.current
    ) {
      el.scrollTop += el.scrollHeight - prevHeightRef.current
      loadingRef.current = false
    }
    prevFirstIdRef.current = firstId
    prevHeightRef.current = el.scrollHeight
  }, [messages, scrollRef])

  // A finished load that turned out to be the last clears the in-flight guard.
  React.useEffect(() => {
    if (reachedBeginning) loadingRef.current = false
  }, [reachedBeginning])

  React.useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      if (loadingRef.current || reachedBeginning || messages.length === 0 || !onLoadMore) return
      if (el.scrollTop <= TOP_THRESHOLD_PX) {
        loadingRef.current = true
        prevHeightRef.current = el.scrollHeight  // anchor before the prepend
        onLoadMore(messages[0].timestamp - 1)
      }
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [scrollRef, messages, reachedBeginning, onLoadMore])
}
