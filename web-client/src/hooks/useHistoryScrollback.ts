import React from 'react'
import { type SocketMessage } from '../websocketContext'

const TOP_THRESHOLD_PX = 80
const AT_BOTTOM_TOLERANCE_PX = 32

/**
 * Lazy-load older history when the user scrolls near the top, preserving the
 * viewport position as a day prepends.
 *
 * A day arrives as many messages over many renders, all inserted above the
 * viewport. We keep the user's *distance from the bottom* constant across the
 * whole batch, so the content being read stays put regardless of how many
 * messages land. Loads are fire-and-forget; `batchToken` (bumped when the
 * server's history-cursor closes a batch) releases the in-flight guard exactly
 * when the requested day is fully delivered — so one scroll-to-top loads one
 * day, not a cascade.
 *
 * When the user is at the bottom this hook stays out of the way: the
 * sticky-bottom hook owns that case (following live messages).
 */
export function useHistoryScrollback<T extends HTMLElement>(
  scrollRef: React.RefObject<T | null>,
  messages: SocketMessage[],
  onLoadMore: ((beforeMs: number) => void) | undefined,
  reachedBeginning: boolean,
  batchToken: number,
): void {
  const distanceFromBottomRef = React.useRef(0)
  const atBottomRef = React.useRef(true)
  const loadingRef = React.useRef(false)

  // Capture the user's position + trigger a load near the top. Programmatic
  // scrolls during a batch (our own anchoring) are ignored so the captured
  // distance and at-bottom flag reflect the user, not our restores.
  React.useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      if (loadingRef.current) return
      atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= AT_BOTTOM_TOLERANCE_PX
      distanceFromBottomRef.current = el.scrollHeight - el.scrollTop
      if (!reachedBeginning && onLoadMore && messages.length > 0 && el.scrollTop <= TOP_THRESHOLD_PX) {
        loadingRef.current = true
        onLoadMore(messages[0].timestamp - 1)
      }
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    onScroll()
    return () => el.removeEventListener('scroll', onScroll)
  }, [scrollRef, messages, reachedBeginning, onLoadMore])

  // Before paint: while scrolled up, hold distance-from-bottom constant so a
  // prepend doesn't shift the read position. At the bottom, defer to sticky.
  React.useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el || atBottomRef.current) return
    el.scrollTop = el.scrollHeight - distanceFromBottomRef.current
  }, [messages, scrollRef])

  // A batch finished delivering — allow the next load.
  React.useEffect(() => {
    loadingRef.current = false
  }, [batchToken])

  // Auto-fill: if the loaded content doesn't fill the viewport and older
  // history exists, pull the previous day so there's always something to
  // scroll (no scrollbar otherwise = no way to scroll back). Re-checked after
  // each batch; stops once the viewport overflows or history is exhausted.
  React.useEffect(() => {
    const el = scrollRef.current
    if (!el || loadingRef.current || reachedBeginning || !onLoadMore || messages.length === 0) return
    if (el.scrollHeight <= el.clientHeight + AT_BOTTOM_TOLERANCE_PX) {
      loadingRef.current = true
      onLoadMore(messages[0].timestamp - 1)
    }
  }, [batchToken, messages, reachedBeginning, onLoadMore, scrollRef])
}
