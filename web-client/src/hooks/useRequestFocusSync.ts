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
): void {
  const focus = useRequestFocus()
  const focused = focus?.focusedRequest

  React.useEffect(() => {
    if (!focused || !myKey || focused.sourceKey === myKey) return
    const container = scrollRef.current
    if (!container) return
    const el = container.querySelector<HTMLElement>(
      `[data-request-id="${focused.requestId}"]`,
    )
    if (!el) return
    container.scrollTop += el.getBoundingClientRect().top - container.getBoundingClientRect().top
    el.classList.add('request-focus-flash')
    const timer = window.setTimeout(() => el.classList.remove('request-focus-flash'), 1200)
    return () => window.clearTimeout(timer)
  }, [focused, myKey, scrollRef])
}
