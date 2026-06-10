import { describe, it, expect } from 'vitest'
import { stopButtonTooltip } from './stopButtonTooltip'

const base = {
  connected: true,
  stopMarkerAvailable: false,
  isVerifying: false,
  isBundle: false,
}

describe('stopButtonTooltip', () => {
  it('reports the disconnected state first, regardless of everything else', () => {
    expect(
      stopButtonTooltip({ ...base, connected: false, stopMarkerAvailable: true }),
    ).toMatch(/websocket is not connected/i)
  })

  it('shows the active stop hint when the marker is available', () => {
    expect(stopButtonTooltip({ ...base, stopMarkerAvailable: true })).toBe(
      'Stop (clears queue)',
    )
  })

  it('shows the checking hint while a re-check is in flight', () => {
    expect(stopButtonTooltip({ ...base, isVerifying: true })).toMatch(/checking/i)
  })

  it('keeps the existing wording when not running as a bundle', () => {
    expect(stopButtonTooltip(base)).toBe(
      'Stop marker not in Roon library — click to retry (won’t affect playback)',
    )
  })

  it('points bundle users at the Getting Started setup section', () => {
    const tip = stopButtonTooltip({ ...base, isBundle: true })
    expect(tip).toMatch(/Getting Started/)
    expect(tip).toMatch(/Enabling the Stop button/)
    // The non-bundle "not in Roon library" phrasing must not leak through.
    expect(tip).not.toMatch(/not in Roon library/)
  })
})
