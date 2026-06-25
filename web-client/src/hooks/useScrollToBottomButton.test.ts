/**
 * Tests for the scroll-to-bottom button hook.
 *
 * JSDOM has no ResizeObserver and computes no layout; we install a minimal
 * fake observer the test can fire synchronously, and back the element with
 * controllable scroll geometry — exercising the production hook against the
 * real DOM event flow (matches useStickyBottomScroll.test.ts).
 */

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useScrollToBottomButton } from './useScrollToBottomButton'

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

function triggerResize() {
  for (const obs of observerInstances) obs.callback([], obs)
}

function makeScrollContainer({
  scrollHeight,
  clientHeight,
  initialScrollTop,
}: {
  scrollHeight: number
  clientHeight: number
  initialScrollTop: number
}): { el: HTMLDivElement; setScrollHeight: (v: number) => void } {
  const el = document.createElement('div')
  let _scrollTop = initialScrollTop
  let _scrollHeight = scrollHeight
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => _scrollHeight })
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => clientHeight })
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => _scrollTop,
    set: (v: number) => { _scrollTop = v },
  })
  document.body.appendChild(el)
  return { el, setScrollHeight: (v: number) => { _scrollHeight = v } }
}

function scrollTo(el: HTMLElement, top: number) {
  act(() => {
    el.scrollTop = top
    el.dispatchEvent(new Event('scroll'))
  })
}

describe('useScrollToBottomButton', () => {
  beforeEach(() => {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = FakeResizeObserver
    observerInstances.length = 0
  })

  afterEach(() => {
    document.body.innerHTML = ''
    observerInstances.length = 0
  })

  it('is hidden when mounted at the bottom', () => {
    // distance = 1000 - 800 - 200 = 0
    const { el } = makeScrollContainer({ scrollHeight: 1000, clientHeight: 200, initialScrollTop: 800 })
    const ref = { current: el }
    const { result } = renderHook(() => useScrollToBottomButton(ref, 'a'))
    expect(result.current.show).toBe(false)
  })

  it('shows once the user scrolls up beyond the threshold', () => {
    const { el } = makeScrollContainer({ scrollHeight: 1000, clientHeight: 200, initialScrollTop: 800 })
    const ref = { current: el }
    const { result } = renderHook(() => useScrollToBottomButton(ref, 'a'))
    // distance = 1000 - 200 - 200 = 600, well past the show threshold
    scrollTo(el, 200)
    expect(result.current.show).toBe(true)
  })

  it('does not show for a tiny nudge above the bottom', () => {
    const { el } = makeScrollContainer({ scrollHeight: 1000, clientHeight: 200, initialScrollTop: 800 })
    const ref = { current: el }
    const { result } = renderHook(() => useScrollToBottomButton(ref, 'a'))
    // distance = 1000 - 760 - 200 = 40: not at bottom, but under the show threshold
    scrollTo(el, 760)
    expect(result.current.show).toBe(false)
  })

  it('hides again when the user scrolls back to the bottom', () => {
    const { el } = makeScrollContainer({ scrollHeight: 1000, clientHeight: 200, initialScrollTop: 800 })
    const ref = { current: el }
    const { result } = renderHook(() => useScrollToBottomButton(ref, 'a'))
    scrollTo(el, 200)
    expect(result.current.show).toBe(true)
    // distance = 1000 - 790 - 200 = 10, within the at-bottom tolerance
    scrollTo(el, 790)
    expect(result.current.show).toBe(false)
  })

  it('does NOT pop visible when content grows below while following the bottom', () => {
    const { el, setScrollHeight } = makeScrollContainer({
      scrollHeight: 1000, clientHeight: 200, initialScrollTop: 800,
    })
    const ref = { current: el }
    const { result } = renderHook(() => useScrollToBottomButton(ref, 'a'))
    // A live message appends below: height grows, scrollTop unchanged (the
    // sticky-bottom hook would re-pin in production; this hook must not flash).
    setScrollHeight(2000)
    act(() => { triggerResize() })
    expect(result.current.show).toBe(false)
  })

  it('highlights when a new item arrives while scrolled up', () => {
    const { el } = makeScrollContainer({ scrollHeight: 1000, clientHeight: 200, initialScrollTop: 800 })
    const ref = { current: el }
    const { result, rerender } = renderHook(
      ({ key }) => useScrollToBottomButton(ref, key),
      { initialProps: { key: 'a' } },
    )
    scrollTo(el, 200)
    expect(result.current.hasNew).toBe(false)
    rerender({ key: 'b' })
    expect(result.current.hasNew).toBe(true)
  })

  it('does not highlight when a new item arrives while at the bottom', () => {
    const { el } = makeScrollContainer({ scrollHeight: 1000, clientHeight: 200, initialScrollTop: 800 })
    const ref = { current: el }
    const { result, rerender } = renderHook(
      ({ key }) => useScrollToBottomButton(ref, key),
      { initialProps: { key: 'a' } },
    )
    rerender({ key: 'b' })
    expect(result.current.hasNew).toBe(false)
  })

  it('smooth-scrolls to the bottom and clears state when scrollToBottom is called', () => {
    const { el } = makeScrollContainer({ scrollHeight: 1000, clientHeight: 200, initialScrollTop: 800 })
    const scrollToSpy = vi.fn()
    el.scrollTo = scrollToSpy as unknown as typeof el.scrollTo
    const ref = { current: el }
    const { result, rerender } = renderHook(
      ({ key }) => useScrollToBottomButton(ref, key),
      { initialProps: { key: 'a' } },
    )
    scrollTo(el, 200)
    rerender({ key: 'b' })
    expect(result.current.show).toBe(true)
    expect(result.current.hasNew).toBe(true)

    act(() => { result.current.scrollToBottom() })
    expect(scrollToSpy).toHaveBeenCalledWith({ top: 1000, behavior: 'smooth' })
    expect(result.current.show).toBe(false)
    expect(result.current.hasNew).toBe(false)
  })

  it('clears the highlight when the user scrolls back to the bottom', () => {
    const { el } = makeScrollContainer({ scrollHeight: 1000, clientHeight: 200, initialScrollTop: 800 })
    const ref = { current: el }
    const { result, rerender } = renderHook(
      ({ key }) => useScrollToBottomButton(ref, key),
      { initialProps: { key: 'a' } },
    )
    scrollTo(el, 200)
    rerender({ key: 'b' })
    expect(result.current.hasNew).toBe(true)
    scrollTo(el, 790)
    expect(result.current.hasNew).toBe(false)
  })

  it('does not throw when the ref is null', () => {
    const ref: { current: HTMLDivElement | null } = { current: null }
    expect(() => renderHook(() => useScrollToBottomButton(ref, undefined))).not.toThrow()
  })
})
