import React from 'react'

// Matches the at-bottom tolerance the sticky-bottom and scrollback hooks use,
// so "following live" means the same thing across all three.
const AT_BOTTOM_TOLERANCE_PX = 32
// Hysteresis: the user must scroll at least this far up before the jump-to-
// bottom button fades in, so a nudge near the live edge doesn't flicker it.
const SHOW_THRESHOLD_PX = 120

export interface ScrollToBottomControls {
  /** The user is scrolled up far enough to offer a jump-to-bottom button. */
  show: boolean
  /** New content arrived below while scrolled up — highlight the button. */
  hasNew: boolean
  scrollToBottom: () => void
}

/**
 * Drives a transient "scroll to bottom" affordance for a scroll container.
 *
 * `latestKey` should change only when content is appended at the bottom (e.g.
 * the last message's id) — prepended history keeps the same newest item, so the
 * highlight fires for new live content but not for paging up.
 */
export function useScrollToBottomButton<T extends HTMLElement>(
  scrollRef: React.RefObject<T | null>,
  latestKey: string | number | undefined,
): ScrollToBottomControls {
  const [show, setShow] = React.useState(false)
  const [hasNew, setHasNew] = React.useState(false)
  const awayRef = React.useRef(false)
  const lastScrollTopRef = React.useRef(0)

  React.useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    lastScrollTopRef.current = el.scrollTop

    const distanceFromBottom = () => el.scrollHeight - el.scrollTop - el.clientHeight

    const reachBottom = () => {
      awayRef.current = false
      setShow(false)
      setHasNew(false)
    }

    const onScroll = () => {
      const cur = el.scrollTop
      const movedUp = cur < lastScrollTopRef.current
      lastScrollTopRef.current = cur
      const distance = distanceFromBottom()
      if (distance <= AT_BOTTOM_TOLERANCE_PX) {
        reachBottom()
      } else if (movedUp && distance > SHOW_THRESHOLD_PX) {
        awayRef.current = true
        setShow(true)
      }
    }

    // Height changes (live append, replay, clear-history) can land the viewport
    // back at the bottom; only ever *clear* the away-state here — raising it
    // would flash the button when content grows below while following live, as
    // the sticky-bottom hook is mid re-pin.
    const onResize = () => {
      if (distanceFromBottom() <= AT_BOTTOM_TOLERANCE_PX) reachBottom()
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    const observer = new ResizeObserver(onResize)
    observer.observe(el)
    onScroll()

    return () => {
      el.removeEventListener('scroll', onScroll)
      observer.disconnect()
    }
  }, [scrollRef])

  const prevKeyRef = React.useRef(latestKey)
  React.useEffect(() => {
    if (latestKey === prevKeyRef.current) return
    prevKeyRef.current = latestKey
    if (awayRef.current) setHasNew(true)
  }, [latestKey])

  const scrollToBottom = React.useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    // Glide to the live edge (matches the request-focus-sync scroll). The
    // animation's own scroll events re-engage the sticky-bottom pin on arrival;
    // hide right away for immediate feedback as the glide starts.
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    awayRef.current = false
    setShow(false)
    setHasNew(false)
  }, [scrollRef])

  return { show, hasNew, scrollToBottom }
}
