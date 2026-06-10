import { describe, expect, it } from 'vitest'
import { scrollEdges } from './useScrollEdges'

describe('scrollEdges', () => {
  it('at the start: more content to the right only', () => {
    expect(scrollEdges(0, 100, 300)).toEqual({
      canScrollLeft: false,
      canScrollRight: true,
    })
  })

  it('in the middle: content off both edges', () => {
    expect(scrollEdges(100, 100, 300)).toEqual({
      canScrollLeft: true,
      canScrollRight: true,
    })
  })

  it('at the end: more content to the left only', () => {
    expect(scrollEdges(200, 100, 300)).toEqual({
      canScrollLeft: true,
      canScrollRight: false,
    })
  })

  it('no overflow: neither edge', () => {
    expect(scrollEdges(0, 300, 300)).toEqual({
      canScrollLeft: false,
      canScrollRight: false,
    })
  })

  it('tolerates sub-pixel rounding at both ends', () => {
    expect(scrollEdges(0.5, 100, 300).canScrollLeft).toBe(false)
    expect(scrollEdges(199.6, 100, 300).canScrollRight).toBe(false)
  })
})
