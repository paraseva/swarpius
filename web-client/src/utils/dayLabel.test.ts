import { describe, expect, it } from 'vitest'
import { dayLabel, isNewDay } from './dayLabel'

const NOON = new Date(2026, 5, 21, 12, 0, 0).getTime()  // local

describe('dayLabel', () => {
  it('labels the same day as Today', () => {
    expect(dayLabel(new Date(2026, 5, 21, 9, 0, 0).getTime(), NOON)).toBe('Today')
  })

  it('labels the previous day as Yesterday', () => {
    expect(dayLabel(new Date(2026, 5, 20, 23, 0, 0).getTime(), NOON)).toBe('Yesterday')
  })

  it('labels older days with weekday + date', () => {
    const label = dayLabel(new Date(2026, 5, 16, 10, 0, 0).getTime(), NOON)
    expect(label).toMatch(/16/)
    expect(label).not.toBe('Today')
    expect(label).not.toBe('Yesterday')
  })
})

describe('isNewDay', () => {
  it('is false within the same calendar day', () => {
    expect(isNewDay(
      new Date(2026, 5, 21, 1, 0, 0).getTime(),
      new Date(2026, 5, 21, 23, 0, 0).getTime(),
    )).toBe(false)
  })

  it('is true across a day boundary', () => {
    expect(isNewDay(
      new Date(2026, 5, 20, 23, 59, 0).getTime(),
      new Date(2026, 5, 21, 0, 1, 0).getTime(),
    )).toBe(true)
  })
})
