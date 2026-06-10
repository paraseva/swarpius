import React from 'react'

/**
 * Returns a ref that scrolls its element into view (smoothly, nearest
 * edge) every time `expanded` transitions to true. Use on the outer
 * container of a collapsible section so newly-revealed content lands
 * in the viewport instead of below the fold.
 */
export function useScrollIntoViewOnExpand<T extends HTMLElement>(expanded: boolean) {
  const ref = React.useRef<T>(null)
  React.useEffect(() => {
    if (!expanded || !ref.current) return
    // RAF defers the scroll until after the browser has laid out the
    // newly-mounted children — `block: 'nearest'` then has the full
    // section height to work with, so it shifts the viewport enough to
    // include the revealed content.
    const id = requestAnimationFrame(() => {
      ref.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
    return () => cancelAnimationFrame(id)
  }, [expanded])
  return ref
}
