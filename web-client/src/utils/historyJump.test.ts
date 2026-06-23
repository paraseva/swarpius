import { describe, expect, it, vi } from 'vitest'
import { loadDaysThrough } from './historyJump'

describe('loadDaysThrough', () => {
  it('fills [dayStart, oldestLoaded) when the day is older than memory', () => {
    const range = vi.fn()
    const issued = loadDaysThrough(1_000, [{ timestamp: 2_000 }], range)
    expect(issued).toBe(true)
    expect(range).toHaveBeenCalledWith(1_000, 2_000)
  })

  it('does nothing when the day is already within loaded history', () => {
    const range = vi.fn()
    const issued = loadDaysThrough(3_000, [{ timestamp: 2_000 }], range)
    expect(issued).toBe(false)
    expect(range).not.toHaveBeenCalled()
  })

  it('treats empty memory as "now", so a past day still loads', () => {
    const range = vi.fn()
    expect(loadDaysThrough(1_000, [], range)).toBe(true)
    expect(range).toHaveBeenCalledTimes(1)
  })
})
