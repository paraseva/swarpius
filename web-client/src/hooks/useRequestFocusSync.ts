import React from 'react'
import { useRequestFocus } from '../requestFocusContext'

/**
 * Sync a request-aware panel to the focused request. When a request-id badge is
 * clicked anywhere, every open panel except the one clicked scrolls its first
 * item tagged `data-request-id="<id>"` to the top and briefly flashes it.
 *
 * Scrolls only this panel's own container (not ancestors), so the diagnostics
 * drawer layout isn't disturbed. `myKey` identifies this panel as the click
 * source so it stays put.
 */
export function useRequestFocusSync<T extends HTMLElement>(
  scrollRef: React.RefObject<T | null>,
  myKey: string | undefined,
  /** Called before scrolling — e.g. to expand a collapsed group so the target
   *  item renders. Must be stable (useCallback) or the sync re-fires. */
  prepare?: (requestId: string) => void,
): void {
  const focus = useRequestFocus()
  const focused = focus?.focusedRequest

  React.useEffect(() => {
    if (!focused || !myKey || focused.sourceKey === myKey) return
    prepare?.(focused.requestId)
    let flashTimer = 0
    // rAF so any expand from `prepare` has rendered the target before we scroll.
    const raf = window.requestAnimationFrame(() => {
      const container = scrollRef.current
      if (!container) return
      const el = container.querySelector<HTMLElement>(`[data-request-id="${focused.requestId}"]`)
      if (!el) return
      const top = container.scrollTop
        + el.getBoundingClientRect().top - container.getBoundingClientRect().top
      container.scrollTo({ top, behavior: 'smooth' })
      el.classList.add('request-focus-flash')
      flashTimer = window.setTimeout(() => el.classList.remove('request-focus-flash'), 1300)
    })
    return () => {
      window.cancelAnimationFrame(raf)
      window.clearTimeout(flashTimer)
    }
  }, [focused, myKey, scrollRef, prepare])
}
