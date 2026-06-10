/**
 * Tests for the sticky-bottom scroll hook.
 *
 * JSDOM includes no ResizeObserver; we install a minimal fake that lets
 * the test trigger the callback synchronously. The element itself is
 * a plain DOM div with overridden scroll properties — exercising the
 * production hook against the real DOM event flow.
 */

import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useStickyBottomScroll } from './useStickyBottomScroll'

type ROCallback = (entries: unknown[], observer: unknown) => void

const observerInstances: FakeResizeObserver[] = []

class FakeResizeObserver {
  callback: ROCallback
  targets: Set<Element> = new Set()
  constructor(callback: ROCallback) {
    this.callback = callback
    observerInstances.push(this)
  }
  observe(target: Element) { this.targets.add(target) }
  unobserve(target: Element) { this.targets.delete(target) }
  disconnect() { this.targets.clear() }
}

// Fires the RO callback on every observer currently watching `target`.
// Mirrors the real RO contract: only observed elements deliver entries.
function simulateResize(target: Element) {
  for (const obs of observerInstances) {
    if (obs.targets.has(target)) obs.callback([], obs)
  }
}

// Fires every observer regardless of target — useful for tests that
// don't care which element triggered the callback.
let triggerResize: () => void = () => {}

function makeScrollContainer({
  scrollHeight,
  clientHeight,
  initialScrollTop,
}: {
  scrollHeight: number
  clientHeight: number
  initialScrollTop: number
}): HTMLDivElement {
  const el = document.createElement('div')
  // Override read-only-by-default scroll geometry with controllable
  // getters / setters so we can simulate user scroll and content growth.
  let _scrollTop = initialScrollTop
  Object.defineProperty(el, 'scrollHeight', {
    configurable: true,
    get: () => scrollHeight,
  })
  Object.defineProperty(el, 'clientHeight', {
    configurable: true,
    get: () => clientHeight,
  })
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => _scrollTop,
    set: (v: number) => { _scrollTop = v },
  })
  document.body.appendChild(el)
  return el
}

describe('useStickyBottomScroll', () => {
  beforeEach(() => {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
      FakeResizeObserver
    observerInstances.length = 0
    triggerResize = () => {
      for (const obs of observerInstances) obs.callback([], obs)
    }
  })

  afterEach(() => {
    document.body.innerHTML = ''
    observerInstances.length = 0
    triggerResize = () => {}
  })

  it('scrolls to bottom on initial mount (default: at bottom)', () => {
    const el = makeScrollContainer({
      scrollHeight: 1000, clientHeight: 200, initialScrollTop: 0,
    })
    const ref = { current: el }
    renderHook(() => useStickyBottomScroll(ref))
    expect(el.scrollTop).toBe(1000)
  })

  it('scrolls to bottom when content height grows and user was at bottom', () => {
    const el = makeScrollContainer({
      scrollHeight: 800, clientHeight: 200, initialScrollTop: 600,
    })
    const ref = { current: el }
    renderHook(() => useStickyBottomScroll(ref))
    // After mount, scrollTop pinned to current scrollHeight (800).
    expect(el.scrollTop).toBe(800)
    // Simulate content growth (e.g. async list expansion, replay batch).
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 1200 })
    triggerResize()
    expect(el.scrollTop).toBe(1200)
  })

  it('does NOT scroll when the user has scrolled up', () => {
    const el = makeScrollContainer({
      scrollHeight: 1000, clientHeight: 200, initialScrollTop: 0,
    })
    const ref = { current: el }
    renderHook(() => useStickyBottomScroll(ref))
    // Mount pinned us to 1000.
    expect(el.scrollTop).toBe(1000)
    // User scrolls up — beyond the tolerance.
    el.scrollTop = 100
    el.dispatchEvent(new Event('scroll'))
    // New content arrives — should NOT yank to bottom.
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 1500 })
    triggerResize()
    expect(el.scrollTop).toBe(100)
  })

  it('stays pinned when async growth fires a scroll event after the pin', () => {
    let scrollHeight = 800
    const el = makeScrollContainer({
      scrollHeight, clientHeight: 200, initialScrollTop: 0,
    })
    Object.defineProperty(el, 'scrollHeight', {
      configurable: true,
      get: () => scrollHeight,
    })
    const ref = { current: el }
    renderHook(() => useStickyBottomScroll(ref))
    expect(el.scrollTop).toBe(800)
    // Content grows before the pin's queued scroll event fires, so
    // the event sees a distance well past tolerance.
    scrollHeight = 1500
    el.dispatchEvent(new Event('scroll'))
    triggerResize()
    expect(el.scrollTop).toBe(1500)
  })

  it('resumes auto-pin after the user scrolls back to the bottom', () => {
    const el = makeScrollContainer({
      scrollHeight: 1000, clientHeight: 200, initialScrollTop: 0,
    })
    const ref = { current: el }
    renderHook(() => useStickyBottomScroll(ref))
    // Mount → pinned to 1000. User scrolls up.
    el.scrollTop = 200
    el.dispatchEvent(new Event('scroll'))
    // User scrolls back to within tolerance of the bottom.
    // distance = 1000 - 790 - 200 = 10, under the 32px tolerance.
    el.scrollTop = 790
    el.dispatchEvent(new Event('scroll'))
    // New content arrives — should pin again.
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 1400 })
    triggerResize()
    expect(el.scrollTop).toBe(1400)
  })

  it('observes children added after mount (empty-state → list swap on replay)', async () => {
    let scrollHeight = 40
    const el = makeScrollContainer({
      scrollHeight, clientHeight: 200, initialScrollTop: 0,
    })
    Object.defineProperty(el, 'scrollHeight', {
      configurable: true,
      get: () => scrollHeight,
    })
    // Initial DOM mirrors a fresh-mount panel: only an empty-state
    // placeholder is present.
    const placeholder = document.createElement('p')
    el.appendChild(placeholder)

    const ref = { current: el }
    renderHook(() => useStickyBottomScroll(ref))

    // React swaps in the message list on first replay tick: placeholder
    // out, ul in. The hook must start observing the ul, otherwise
    // every subsequent message arrival fails to trigger a pin.
    el.removeChild(placeholder)
    const ul = document.createElement('ul')
    el.appendChild(ul)
    // MutationObserver callbacks are microtask-scheduled.
    await Promise.resolve()

    // Server replay batch lands inside the ul.
    scrollHeight = 5000
    simulateResize(ul)
    expect(el.scrollTop).toBe(5000)
  })

  it('does not throw if the ref is null', () => {
    const ref: { current: HTMLDivElement | null } = { current: null }
    expect(() => renderHook(() => useStickyBottomScroll(ref))).not.toThrow()
  })

  it('restores saved position on remount with a storageKey', () => {
    const el1 = makeScrollContainer({
      scrollHeight: 2000, clientHeight: 200, initialScrollTop: 0,
    })
    const ref1 = { current: el1 }
    const { unmount } = renderHook(() => useStickyBottomScroll(ref1, 'k'))
    // User scrolls up beyond tolerance.
    el1.scrollTop = 400
    el1.dispatchEvent(new Event('scroll'))
    unmount()

    // Server-restart: AppShell remounts → fresh DOM element. The
    // replay batch hasn't filled scrollHeight yet, so the first RO
    // fire can't restore — the hook waits and tries again after
    // content grows.
    let scrollHeight = 100
    const el2 = makeScrollContainer({
      scrollHeight, clientHeight: 200, initialScrollTop: 0,
    })
    Object.defineProperty(el2, 'scrollHeight', {
      configurable: true,
      get: () => scrollHeight,
    })
    const ref2 = { current: el2 }
    renderHook(() => useStickyBottomScroll(ref2, 'k'))
    triggerResize()
    expect(el2.scrollTop).toBe(0)
    scrollHeight = 2000
    triggerResize()
    expect(el2.scrollTop).toBe(400)
  })

  it('does not restore when the saved state was at the bottom', () => {
    const el1 = makeScrollContainer({
      scrollHeight: 1000, clientHeight: 200, initialScrollTop: 0,
    })
    const ref1 = { current: el1 }
    const { unmount } = renderHook(() => useStickyBottomScroll(ref1, 'k2'))
    // Hook pinned to bottom; default at-bottom state saved.
    unmount()

    let scrollHeight = 50
    const el2 = makeScrollContainer({
      scrollHeight, clientHeight: 200, initialScrollTop: 0,
    })
    Object.defineProperty(el2, 'scrollHeight', {
      configurable: true,
      get: () => scrollHeight,
    })
    const ref2 = { current: el2 }
    renderHook(() => useStickyBottomScroll(ref2, 'k2'))
    scrollHeight = 1500
    triggerResize()
    // Sticky-pin still applies — saved state was at bottom.
    expect(el2.scrollTop).toBe(1500)
  })

  it('cleans up listeners on unmount', () => {
    const el = makeScrollContainer({
      scrollHeight: 1000, clientHeight: 200, initialScrollTop: 0,
    })
    const removeSpy = vi.spyOn(el, 'removeEventListener')
    const ref = { current: el }
    const { unmount } = renderHook(() => useStickyBottomScroll(ref))
    unmount()
    expect(removeSpy).toHaveBeenCalledWith('scroll', expect.any(Function))
  })
})
