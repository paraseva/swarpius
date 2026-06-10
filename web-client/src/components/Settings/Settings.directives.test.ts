import { describe, expect, it } from 'vitest'
import { stateDirectives, type SaveStatus } from './settingsDirectives'
import type { UseSettingsState, ValidationStatus } from '../../hooks/useSettingsState'

const idle: SaveStatus = { kind: 'idle' }

function stateWith(over: Partial<ValidationStatus> = {}): UseSettingsState {
  return {
    validation: {
      state: 'passed',
      results: [],
      backends: [],
      pending_restart: false,
      ...over,
    },
  } as UseSettingsState
}

describe('stateDirectives — restart banner vs unsaved changes', () => {
  it('shows the restart banner when saved & validated and nothing is unsaved', () => {
    const out = stateDirectives(idle, false, stateWith({ pending_restart: true }))
    expect(out).toEqual([
      { kind: 'pending', text: 'Saved & validated. Click Restart to apply.' },
    ])
  })

  it('reverts to "Unsaved changes" once new edits are made (banner steps aside)', () => {
    // pending_restart is still true (saved-but-unapplied), but new edits
    // make it stale — and restarting would discard them.
    const out = stateDirectives(idle, true, stateWith({ pending_restart: true }))
    expect(out).toEqual([{ kind: 'dirty', text: 'Unsaved changes' }])
  })

  it('shows "Unsaved changes" when dirty with no pending restart', () => {
    expect(stateDirectives(idle, true, stateWith())).toEqual([
      { kind: 'dirty', text: 'Unsaved changes' },
    ])
  })

  it('shows nothing when clean', () => {
    expect(stateDirectives(idle, false, stateWith())).toEqual([])
  })

  it('surfaces a save error regardless of dirty', () => {
    const out = stateDirectives({ kind: 'error', message: 'boom' }, true, stateWith())
    expect(out).toEqual([{ kind: 'error', text: 'boom' }])
  })
})
