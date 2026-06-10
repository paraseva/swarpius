import { useEffect, useState, type RefObject } from 'react'

export interface ScrollEdges {
  canScrollLeft: boolean
  canScrollRight: boolean
}

/** Whether a horizontally-scrollable element has content off either edge.
 *  A 1px tolerance absorbs sub-pixel rounding so the ends read as reached. */
export function scrollEdges(
  scrollLeft: number,
  clientWidth: number,
  scrollWidth: number,
): ScrollEdges {
  const max = scrollWidth - clientWidth
  return {
    canScrollLeft: scrollLeft > 1,
    canScrollRight: scrollLeft < max - 1,
  }
}

/** Track which edges of a scrollable element hide content, so callers can fade
 *  that edge as a scroll affordance. Recomputed on scroll, viewport resize, and
 *  element resize. */
export function useScrollEdges<T extends HTMLElement>(
  ref: RefObject<T | null>,
): ScrollEdges {
  const [edges, setEdges] = useState<ScrollEdges>({
    canScrollLeft: false,
    canScrollRight: false,
  })

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () =>
      setEdges(scrollEdges(el.scrollLeft, el.clientWidth, el.scrollWidth))
    // Defer the first measure out of the effect body (read after layout).
    const raf = requestAnimationFrame(update)
    el.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update)
    let observer: ResizeObserver | undefined
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(update)
      observer.observe(el)
    }
    return () => {
      cancelAnimationFrame(raf)
      el.removeEventListener('scroll', update)
      window.removeEventListener('resize', update)
      observer?.disconnect()
    }
  }, [ref])

  return edges
}
