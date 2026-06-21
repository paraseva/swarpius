import React from 'react'

const AT_BOTTOM_TOLERANCE_PX = 32

type StickyPosition = { scrollTop: number; atBottom: boolean }

// Module-level store survives AppShell remounts (which is what fires
// on WS reconnect — see App.tsx's `key={connectionGeneration}`) but
// not browser refresh — so per-session restore on reconnect, fresh
// sticky-bottom start on reload.
const positionStore = new Map<string, StickyPosition>()

/**
 * Pins a scrollable container to its bottom edge as content grows,
 * *if* the user is at the bottom. With `storageKey`, also persists
 * the user's scroll position across mount/unmount cycles (server
 * restart triggers an AppShell remount), restoring it once the new
 * content has grown enough to reach the saved offset.
 */
export function useStickyBottomScroll<T extends HTMLElement>(
  scrollRef: React.RefObject<T | null>,
  storageKey?: string,
  suppressed = false,
) {
  const wasAtBottomRef = React.useRef(true)
  // While a date-jump is in flight, the jump owns the scroll position — don't
  // let bottom-pinning fight it as the requested range streams in. Read via a
  // ref so the listeners see the latest without re-running the main effect
  // (which would reset wasAtBottom).
  const suppressedRef = React.useRef(suppressed)
  React.useEffect(() => {
    suppressedRef.current = suppressed
  }, [suppressed])

  React.useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    let pendingRestore: number | null = null
    if (storageKey != null) {
      const saved = positionStore.get(storageKey)
      if (saved) {
        wasAtBottomRef.current = saved.atBottom
        if (!saved.atBottom) pendingRestore = saved.scrollTop
      }
    }

    const isAtBottom = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight
      return distance <= AT_BOTTOM_TOLERANCE_PX
    }

    // Only mark "not at bottom" on user-initiated upward scrolls. A
    // scroll event from our own pin can arrive after async growth has
    // already extended scrollHeight past the just-set scrollTop; using
    // isAtBottom() there would falsely disable sticky-pin.
    let lastScrollTop = el.scrollTop

    const handleScroll = () => {
      const currentTop = el.scrollTop
      const movedUp = currentTop < lastScrollTop
      lastScrollTop = currentTop
      if (movedUp || isAtBottom()) {
        wasAtBottomRef.current = isAtBottom()
      }
      if (storageKey != null) {
        positionStore.set(storageKey, {
          scrollTop: currentTop,
          atBottom: wasAtBottomRef.current,
        })
      }
    }

    const onResize = () => {
      if (suppressedRef.current) return  // a date-jump owns the scroll
      // A pending cross-mount restore beats sticky-pin. Wait for the
      // replay batch to grow scrollHeight enough that the saved offset
      // is actually reachable, then land there and re-derive
      // wasAtBottom from the restored position.
      if (pendingRestore !== null) {
        const target = pendingRestore
        const maxScroll = el.scrollHeight - el.clientHeight
        if (maxScroll >= target) {
          el.scrollTop = target
          lastScrollTop = el.scrollTop
          pendingRestore = null
          wasAtBottomRef.current = isAtBottom()
        }
        return
      }
      if (wasAtBottomRef.current) {
        el.scrollTop = el.scrollHeight
        lastScrollTop = el.scrollTop
      }
    }

    el.addEventListener('scroll', handleScroll, { passive: true })

    const observer = new ResizeObserver(onResize)
    observer.observe(el)
    const observeChildren = () => {
      for (const child of Array.from(el.children)) observer.observe(child)
    }
    observeChildren()

    // Consumers commonly render an empty-state placeholder while
    // waiting for replay, then swap it for a `<ul>` once messages
    // arrive. Without this MutationObserver, the freshly-added ul
    // is never observed, no resize ever fires, and the panel
    // strands wherever the placeholder-era pin left it.
    const childObserver = new MutationObserver(observeChildren)
    childObserver.observe(el, { childList: true })

    onResize()

    return () => {
      el.removeEventListener('scroll', handleScroll)
      observer.disconnect()
      childObserver.disconnect()
    }
  }, [scrollRef, storageKey])
}
