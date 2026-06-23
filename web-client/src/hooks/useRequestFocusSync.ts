import React from 'react'
import { useRequestFocus } from '../requestFocusContext'

// A request-focus sync drives programmatic scrolls in several panels at once.
// While that's happening, scroll-back must not treat a panel reaching its top as
// a user scroll-up and lazy-load the previous day (which would yank everything
// to the newly-loaded day). This flag marks the brief sync-scroll window.
let syncScrolling = false
let syncScrollTimer = 0

export function isSyncScrolling(): boolean {
  return syncScrolling
}

/**
 * Smooth-scroll the item tagged `data-request-id="<id>"` to the top of
 * `container` and briefly flash it. Scrolls only this container (not ancestors),
 * so the diagnostics drawer layout isn't disturbed. Returns false if the item
 * isn't present (e.g. inside a still-collapsed group).
 *
 * `day` disambiguates duplicate request ids: conversation ids reset each day,
 * so the same `rq-cNN-NNNN` can appear on several days. When given, only the
 * item whose `data-request-day` also matches is targeted.
 */
export function scrollRequestIntoView(
  container: HTMLElement | null,
  requestId: string,
  day?: string | null,
): boolean {
  if (!container) return false
  const selector = day
    ? `[data-request-id="${requestId}"][data-request-day="${day}"]`
    : `[data-request-id="${requestId}"]`
  const el = container.querySelector<HTMLElement>(selector)
  if (!el) return false
  syncScrolling = true
  window.clearTimeout(syncScrollTimer)
  syncScrollTimer = window.setTimeout(() => { syncScrolling = false }, 700)
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
    scrollRequestIntoView(scrollRef.current, focused.requestId, focused.day)
  }, [focused, myKey, scrollRef])
}
