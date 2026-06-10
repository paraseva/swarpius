import { describe, expect, it } from 'vitest'
import { hasSoftSettingsWarning } from './settingsWarning'
import type {
  AgentValidationResult,
  BackendReachabilityResult,
  ValidationAgent,
  ValidationStatus,
} from './hooks/useSettingsState'

const agent = (
  name: ValidationAgent,
  ok: boolean | null,
  enabled = true,
): AgentValidationResult => ({
  agent: name,
  enabled,
  provider: 'anthropic',
  model: 'anthropic/claude-sonnet-4-6',
  inherits_coordinator: false,
  ok,
  not_validated: ok === null,
})

const backend = (
  name: BackendReachabilityResult['backend'],
  ok: boolean,
): BackendReachabilityResult => ({ backend: name, label: name, ok })

const status = (over: Partial<ValidationStatus> = {}): ValidationStatus => ({
  state: 'passed',
  results: [],
  backends: [],
  pending_restart: false,
  ...over,
})

describe('hasSoftSettingsWarning', () => {
  it('is false when nothing is wrong', () => {
    expect(
      hasSoftSettingsWarning(
        status({
          results: [agent('coordinator', true), agent('arbiter', true)],
          backends: [backend('web-search', true), backend('tts', true)],
        }),
      ),
    ).toBe(false)
  })

  it('warns when a configured backend is unreachable', () => {
    expect(
      hasSoftSettingsWarning(status({ backends: [backend('tts', false)] })),
    ).toBe(true)
  })

  it('warns when an enabled optional agent failed live validation', () => {
    expect(
      hasSoftSettingsWarning(status({ results: [agent('arbiter', false)] })),
    ).toBe(true)
  })

  it('does NOT warn for the coordinator — its failure is the hard red route', () => {
    expect(
      hasSoftSettingsWarning(status({ results: [agent('coordinator', false)] })),
    ).toBe(false)
  })

  it('ignores a disabled agent even if it failed', () => {
    expect(
      hasSoftSettingsWarning(
        status({ results: [agent('analyser', false, false)] }),
      ),
    ).toBe(false)
  })

  it('does not warn for an agent not validated yet (ok === null)', () => {
    expect(
      hasSoftSettingsWarning(status({ results: [agent('diagnostic', null)] })),
    ).toBe(false)
  })
})
