import React from 'react'
import { useRequestFocus } from '../requestFocusContext'

/**
 * Smooth-scroll the first item tagged `data-request-id="<id>"` to the top of
 * `container` and briefly flash it. Scrolls only this container (not ancestors),
 * so the diagnostics drawer layout isn't disturbed. Returns false if the item
 * isn't present (e.g. inside a still-collapsed group).
 */
export function scrollRequestIntoView(
  container: HTMLElement | null,
  requestId: string,
): boolean {
  if (!container) return false
  const el = container.querySelector<HTMLElement>(`[data-request-id="${requestId}"]`)
  if (!el) return false
  const top = container.scrollTop
    + el.getBoundingClientRect().top - container.getBoundingClientRect().top
  container.scrollTo({ top, behavior: 'smooth' })
  el.classList.add('request-focus-flash')
  window.setTimeout(() => el.classList.remove('request-focus-flash'), 1300)
  return true
}

/**
 * Sync a flat request-aware panel (chat, event streams) to the focused request:
 * when a request-id badge is clicked elsewhere, scroll this panel to that
 * request and flash it. `myKey` marks this panel as the click source so it stays
 * put. Panels with collapsible groups (Session Requests) handle focus
 * themselves, since they must expand before the item exists.
 */
export function useRequestFocusSync<T extends HTMLElement>(
  scrollRef: React.RefObject<T | null>,
  myKey: string | undefined,
): void {
  const focus = useRequestFocus()
  const focused = focus?.focusedRequest

  React.useEffect(() => {
    if (!focused || !myKey || focused.sourceKey === myKey) return
    scrollRequestIntoView(scrollRef.current, focused.requestId)
  }, [focused, myKey, scrollRef])
}
