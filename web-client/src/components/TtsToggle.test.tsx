import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { TtsToggle } from './TtsToggle'

afterEach(cleanup)

const noop = () => {}
const checkbox = () => screen.getByRole('checkbox') as HTMLInputElement

describe('TtsToggle on/off + disabled state', () => {
  it('is off and disabled when TTS is not configured, even if the stored preference is on', () => {
    render(<TtsToggle enabled onChange={noop} disabled notConfigured />)
    expect(checkbox().checked).toBe(false)
    expect(checkbox().disabled).toBe(true)
  })

  it('is on when configured, enabled, and the server is healthy', () => {
    render(<TtsToggle enabled onChange={noop} health="healthy" />)
    expect(checkbox().checked).toBe(true)
    expect(checkbox().disabled).toBe(false)
  })

  it('is off and disabled when configured and enabled but the server is unreachable', () => {
    render(<TtsToggle enabled onChange={noop} health="failing" />)
    expect(checkbox().checked).toBe(false)
    expect(checkbox().disabled).toBe(true)
  })

  it('restores the on preference once the server recovers (preference never mutated)', () => {
    const { rerender } = render(<TtsToggle enabled onChange={noop} health="failing" />)
    expect(checkbox().checked).toBe(false)
    rerender(<TtsToggle enabled onChange={noop} health="healthy" />)
    expect(checkbox().checked).toBe(true)
  })

  it('stays off when the user preference is off', () => {
    render(<TtsToggle enabled={false} onChange={noop} health="healthy" />)
    expect(checkbox().checked).toBe(false)
  })
})
