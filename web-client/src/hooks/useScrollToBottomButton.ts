import React from 'react'

// Matches the at-bottom tolerance of the sticky-bottom + scrollback hooks.
const AT_BOTTOM_TOLERANCE_PX = 32
// Hysteresis: scroll up at least this far before showing, so the button
// doesn't flicker near the bottom.
const SHOW_THRESHOLD_PX = 120

export interface ScrollToBottomControls {
  show: boolean
  hasNew: boolean
  scrollToBottom: () => void
}

/**
 * `latestKey` must change only on bottom-append (e.g. the last message's id):
 * prepended history keeps the same newest item, so `hasNew` fires for new live
 * content, not for paging up.
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

    // Only ever clears the away-state, never raises it: raising it when content
    // grows below (while following live) would flash the button as the
    // sticky-bottom hook re-pins.
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
    // Smooth, matching the request-focus-sync scroll; hide now for feedback.
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    awayRef.current = false
    setShow(false)
    setHasNew(false)
  }, [scrollRef])

  return { show, hasNew, scrollToBottom }
}
