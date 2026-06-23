import React from 'react'
import { type SocketMessage } from '../websocketContext'
import { isSyncScrolling } from './useRequestFocusSync'

const AT_BOTTOM_TOLERANCE_PX = 32
// Start the next day's load a little before the very top, so paging up feels
// seamless rather than stalling at the edge.
const PRELOAD_PX = 200

// Native scroll anchoring holds the viewport across a prepend during layout
// (before paint) — flash-free. Safari lacks it, so we keep a JS fallback there.
const SUPPORTS_OVERFLOW_ANCHOR =
  typeof CSS !== 'undefined' && !!CSS.supports && CSS.supports('overflow-anchor: auto')

/**
 * Lazy-load older history as the user nears the top, holding the viewport
 * position as a day prepends.
 *
 * The load is triggered by an IntersectionObserver watching a `data-history-top`
 * sentinel the panel renders at the top of its scroll content — not a scroll-
 * position threshold. That fires reliably the instant the top comes into view
 * (no "scrolled-to-rest-just-short" dead zone) and keeps firing while a short or
 * sparse panel's sentinel stays visible, so such panels fill themselves; it
 * stops once there's a scrollbar (sentinel scrolls off) or history is exhausted.
 * `batchToken` (bumped when the server's history-cursor closes a batch) releases
 * the in-flight guard exactly when a requested day is fully delivered, so one
 * trigger loads one day, not a cascade.
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
  // Latest values for the observer callback, which is created once.
  const stateRef = React.useRef({ messages, onLoadMore, reachedBeginning })
  React.useEffect(() => {
    stateRef.current = { messages, onLoadMore, reachedBeginning }
  })

  // Load the previous day if the top sentinel is near the viewport's top and a
  // load isn't already in flight. Geometry is read fresh (not the observer's
  // possibly-stale entry) so the post-batch re-check below can't over-load.
  const maybeLoad = React.useCallback(() => {
    if (loadingRef.current || isSyncScrolling()) return
    const { messages: msgs, onLoadMore: load, reachedBeginning: done } = stateRef.current
    if (done || !load || msgs.length === 0) return
    const el = scrollRef.current
    const sentinel = el?.querySelector('[data-history-top]')
    if (!el || !sentinel) return
    // A collapsed panel (e.g. a closed diagnostics accordion section is
    // display:none) has no scrollport, so it can never fill — guarding here
    // stops the fill loop walking it to the beginning while it's hidden.
    if (el.clientHeight === 0) return
    const rootRect = el.getBoundingClientRect()
    const rect = sentinel.getBoundingClientRect()
    if (rect.bottom < rootRect.top - PRELOAD_PX || rect.top > rootRect.bottom) return
    loadingRef.current = true
    load(msgs[0].timestamp - 1)
  }, [scrollRef])

  // Trigger loads from the sentinel's visibility (reliable, unlike scroll events).
  React.useEffect(() => {
    const el = scrollRef.current
    const sentinel = el?.querySelector('[data-history-top]')
    if (!el || !sentinel) return
    const observer = new IntersectionObserver(() => maybeLoad(), {
      root: el,
      rootMargin: `${PRELOAD_PX}px 0px 0px 0px`,
    })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [scrollRef, maybeLoad])

  // Track at-bottom + hand prepend position-holding to the browser while scrolled
  // up (native anchoring, flash-free; off at the bottom where the sticky-bottom
  // pin owns scrollTop). Our own anchoring scrolls fire during a load — skip
  // those so the at-bottom flag reflects the user, not our restores.
  React.useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    let lastScrollTop = el.scrollTop
    const onScroll = () => {
      if (loadingRef.current) return
      const cur = el.scrollTop
      const movedUp = cur < lastScrollTop
      lastScrollTop = cur
      if (movedUp) atBottomRef.current = false
      else if (el.scrollHeight - cur - el.clientHeight <= AT_BOTTOM_TOLERANCE_PX) {
        atBottomRef.current = true
      }
      el.style.overflowAnchor = atBottomRef.current ? 'none' : 'auto'
      distanceFromBottomRef.current = el.scrollHeight - cur
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    onScroll()
    return () => el.removeEventListener('scroll', onScroll)
  }, [scrollRef])

  // Safari fallback: it lacks native scroll anchoring, so while the user is
  // scrolled up, hold distance-from-bottom by hand across a prepend.
  React.useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el || atBottomRef.current) return
    if (SUPPORTS_OVERFLOW_ANCHOR) return
    el.scrollTop = el.scrollHeight - distanceFromBottomRef.current
  }, [messages, scrollRef])

  // A batch finished delivering — allow the next load, then re-check: a still-
  // short/sparse panel keeps its sentinel visible and pulls the next day.
  React.useEffect(() => {
    loadingRef.current = false
    maybeLoad()
  }, [batchToken, maybeLoad])
}
