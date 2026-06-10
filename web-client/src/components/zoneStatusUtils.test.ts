import { describe, expect, it } from 'vitest'
import { formatVolumePercent } from './zoneStatusUtils'

describe('formatVolumePercent', () => {
  it('renders raw value 50 in a 0-100 range as 50%', () => {
    expect(formatVolumePercent(50, 0, 100)).toBe('50%')
  })

  it('renders the device max as 100% regardless of native scale', () => {
    expect(formatVolumePercent(15, 0, 15)).toBe('100%')
    expect(formatVolumePercent(100, 0, 100)).toBe('100%')
  })

  it('renders the device min as 0%', () => {
    expect(formatVolumePercent(0, 0, 15)).toBe('0%')
    expect(formatVolumePercent(0, 0, 100)).toBe('0%')
  })

  it('handles negative-min ranges (e.g. dB scales)', () => {
    expect(formatVolumePercent(-80, -80, 0)).toBe('0%')
    expect(formatVolumePercent(0, -80, 0)).toBe('100%')
    expect(formatVolumePercent(-40, -80, 0)).toBe('50%')
  })

  it('rounds to nearest whole percent', () => {
    expect(formatVolumePercent(7, 0, 15)).toBe('47%')
    expect(formatVolumePercent(8, 0, 15)).toBe('53%')
  })

  it('clamps out-of-range values', () => {
    expect(formatVolumePercent(20, 0, 15)).toBe('100%')
    expect(formatVolumePercent(-5, 0, 15)).toBe('0%')
  })

  it('falls back to raw value when min/max yield a non-positive range', () => {
    expect(formatVolumePercent(42, 50, 50)).toBe('42%')
    expect(formatVolumePercent(42, 100, 0)).toBe('42%')
  })

  it('defaults to 0-100 when min/max are not supplied', () => {
    expect(formatVolumePercent(75)).toBe('75%')
  })
})
