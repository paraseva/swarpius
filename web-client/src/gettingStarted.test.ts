import { describe, expect, it } from 'vitest'
import { shouldAutoShowWelcome, type AutoShowWelcomeInputs } from './gettingStarted'

// Pristine install, first feature-availability message received, intro
// not yet shown this session — the one combination that opens it.
const base: AutoShowWelcomeInputs = {
  configPristine: true,
  awaitingFirstUpdate: false,
  alreadyShown: false,
}

describe('shouldAutoShowWelcome', () => {
  it('opens on a pristine install once the first update has arrived', () => {
    expect(shouldAutoShowWelcome(base)).toBe(true)
  })

  it('waits until the first feature-availability message arrives', () => {
    expect(shouldAutoShowWelcome({ ...base, awaitingFirstUpdate: true })).toBe(false)
  })

  it('stays shut once the user has configured something', () => {
    expect(shouldAutoShowWelcome({ ...base, configPristine: false })).toBe(false)
  })

  it('does not re-open after being dismissed this session', () => {
    expect(shouldAutoShowWelcome({ ...base, alreadyShown: true })).toBe(false)
  })
})
