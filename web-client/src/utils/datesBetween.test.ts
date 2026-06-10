/**
 * Tests for datesBetween — TZ-independent date range enumeration.
 *
 * The previous fillDateGaps implementations constructed `new Date('YYYY-MM-DDT00:00:00')`
 * (local-time interpretation) and iterated with `setDate`. In any non-UTC
 * timezone, `toISOString().slice(0, 10)` on the result was off by a day,
 * and DST transitions made it worse (days skipped or duplicated). These
 * tests pin the TZ-independent behaviour of the new helper.
 */

import { describe, it, expect } from 'vitest'
import { datesBetween } from './trendData'

describe('datesBetween', () => {
  it('returns single date when start equals end', () => {
    expect(datesBetween('2026-03-29', '2026-03-29')).toEqual(['2026-03-29'])
  })

  it('enumerates consecutive days in ascending order', () => {
    expect(datesBetween('2026-03-27', '2026-03-30')).toEqual([
      '2026-03-27', '2026-03-28', '2026-03-29', '2026-03-30',
    ])
  })

  it('handles UK spring-forward DST boundary', () => {
    // 2026-03-29 is the UK spring-forward day (01:00 UTC → 02:00 BST local).
    // Buggy setDate-based iteration could skip or double this day when run
    // in the Europe/London timezone.
    expect(datesBetween('2026-03-28', '2026-03-30')).toEqual([
      '2026-03-28', '2026-03-29', '2026-03-30',
    ])
  })

  it('handles UK fall-back DST boundary', () => {
    // 2026-10-25 is the UK fall-back day (02:00 BST → 01:00 GMT local).
    // Buggy iteration could produce duplicate or missing entries here.
    expect(datesBetween('2026-10-24', '2026-10-26')).toEqual([
      '2026-10-24', '2026-10-25', '2026-10-26',
    ])
  })

  it('crosses month boundaries cleanly', () => {
    expect(datesBetween('2026-01-30', '2026-02-02')).toEqual([
      '2026-01-30', '2026-01-31', '2026-02-01', '2026-02-02',
    ])
  })

  it('crosses year boundaries cleanly', () => {
    expect(datesBetween('2025-12-30', '2026-01-02')).toEqual([
      '2025-12-30', '2025-12-31', '2026-01-01', '2026-01-02',
    ])
  })

  it('handles leap-year February', () => {
    // 2024 is a leap year; Feb has 29 days.
    expect(datesBetween('2024-02-28', '2024-03-01')).toEqual([
      '2024-02-28', '2024-02-29', '2024-03-01',
    ])
  })

  it('returns empty when end is before start', () => {
    expect(datesBetween('2026-03-30', '2026-03-28')).toEqual([])
  })
})
