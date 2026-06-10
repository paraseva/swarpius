import { describe, expect, it } from 'vitest'
import { computeDateLabels } from './computeDateLabels'

const day = (d: string) => ({ date: `2026-04-${d}` })

describe('computeDateLabels', () => {
  it('labels every point and only every point when points.length <= 6', () => {
    const points = [day('01'), day('02'), day('03')]
    const labels = computeDateLabels(points)
    expect(labels).toEqual([
      { idx: 0, label: '04-01' },
      { idx: 1, label: '04-02' },
      { idx: 2, label: '04-03' },
    ])
  })

  it('always anchors the final point even when interval would skip it', () => {
    // 8 points, interval = floor(8/6) = 1 → strides hit 0..7 inclusive
    // (anchor not strictly needed). Verify the anchor at length-1.
    const eight = ['01','02','03','04','05','06','07','08'].map(day)
    const labels = computeDateLabels(eight)
    expect(labels[labels.length - 1].idx).toBe(7)
    expect(labels[labels.length - 1].label).toBe('04-08')
  })

  it('appends a final-point label when stride misses the last index', () => {
    // 14 points → interval = floor(14/6) = 2 → strides 0,2,4,6,8,10,12
    // Last index is 13 — not visited by the stride, so the anchor must
    // append it as a separate label.
    const fourteen = Array.from({ length: 14 }, (_, i) =>
      day(String(i + 1).padStart(2, '0')),
    )
    const labels = computeDateLabels(fourteen)
    const stridedIndices = [0, 2, 4, 6, 8, 10, 12]
    const lastTwo = labels.slice(-2).map(l => l.idx)
    expect(labels.slice(0, -1).map(l => l.idx)).toEqual(stridedIndices)
    expect(lastTwo[1]).toBe(13)
  })

  it('does not double-label the final point when the stride already lands on it', () => {
    // 7 points → interval = floor(7/6) = 1 → strides hit every index 0..6
    // including the last. Anchor logic should detect that and not append
    // a duplicate.
    const seven = ['01','02','03','04','05','06','07'].map(day)
    const labels = computeDateLabels(seven)
    const lastTwo = labels.slice(-2).map(l => l.idx)
    expect(lastTwo).toEqual([5, 6])
  })

  it('formats labels as MM-DD by stripping the year prefix', () => {
    const labels = computeDateLabels([day('15')])
    expect(labels[0].label).toBe('04-15')
    expect(labels[0].label).not.toContain('2026')
  })
})
