import { describe, expect, it } from 'vitest'
import { viewAfterRestart } from './appView'

describe('viewAfterRestart', () => {
  it('returns to the assistant after a restart from the Settings view', () => {
    expect(viewAfterRestart('settings')).toBe('assistant')
  })

  it('leaves any other current view unchanged — a restart must not pull the user off it', () => {
    for (const view of ['assistant', 'analysis', 'roon-explorer', 'roon-setup'] as const) {
      expect(viewAfterRestart(view)).toBe(view)
    }
  })
})
