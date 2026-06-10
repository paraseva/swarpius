import { describe, expect, it } from 'vitest'
import {
  formatAgentIssue,
  formatAgentIssues,
  formatBackendIssue,
  formatMissingField,
} from './validationStrings'
import type {
  AgentValidationResult,
  BackendReachabilityResult,
} from './hooks/useSettingsState'

const agent = (
  over: Partial<AgentValidationResult> = {},
): AgentValidationResult => ({
  agent: 'arbiter',
  enabled: true,
  provider: 'anthropic',
  model: 'anthropic/claude-sonnet-4-6',
  inherits_coordinator: false,
  ok: false,
  not_validated: false,
  ...over,
})

const backend = (
  over: Partial<BackendReachabilityResult> = {},
): BackendReachabilityResult => ({
  backend: 'web-search',
  label: 'Web Search',
  ok: false,
  ...over,
})

describe('formatAgentIssue', () => {
  it('maps each error_kind to a human phrase', () => {
    expect(formatAgentIssue(agent({ agent: 'arbiter', error_kind: 'not_found' })))
      .toBe('Arbiter model not found')
    expect(formatAgentIssue(agent({ agent: 'diagnostic', error_kind: 'auth_failed' })))
      .toBe('Diagnostic API key invalid')
    expect(formatAgentIssue(agent({ agent: 'analyser', error_kind: 'network' })))
      .toBe('Analyser unreachable')
    expect(formatAgentIssue(agent({ agent: 'coordinator', error_kind: 'bad_request' })))
      .toBe('Coordinator request rejected')
  })

  it('falls back to the agent name alone when the kind is other/null', () => {
    expect(formatAgentIssue(agent({ agent: 'arbiter', error_kind: 'other' }))).toBe('Arbiter')
    expect(formatAgentIssue(agent({ agent: 'arbiter', error_kind: null }))).toBe('Arbiter')
  })
})

describe('formatAgentIssues', () => {
  it('comma-joins multiple agents', () => {
    expect(
      formatAgentIssues([
        agent({ agent: 'arbiter', error_kind: 'not_found' }),
        agent({ agent: 'diagnostic', error_kind: 'auth_failed' }),
      ]),
    ).toBe('Arbiter model not found, Diagnostic API key invalid')
  })
})

describe('formatBackendIssue', () => {
  it('maps each backend error_kind to a human phrase with a clear subject', () => {
    expect(formatBackendIssue(backend({ backend: 'web-search', error_kind: 'auth_failed' })))
      .toBe('Web Search API key invalid')
    expect(formatBackendIssue(backend({ backend: 'tts', error_kind: 'network' })))
      .toBe('TTS server unreachable')
    expect(formatBackendIssue(backend({ backend: 'web-search', error_kind: 'missing_credential' })))
      .toBe('Web Search API key missing')
    expect(formatBackendIssue(backend({ backend: 'tts', error_kind: 'not_found' })))
      .toBe('TTS server endpoint not found')
  })

  it('falls back to the subject alone when the kind is other/null', () => {
    expect(formatBackendIssue(backend({ backend: 'web-search', error_kind: 'other' }))).toBe('Web Search')
    expect(formatBackendIssue(backend({ backend: 'web-search', error_kind: null }))).toBe('Web Search')
  })
})

describe('formatMissingField', () => {
  it('humanises required-config field names', () => {
    expect(formatMissingField('LLM_MODEL')).toBe('Coordinator model')
    expect(formatMissingField('LLM_API_KEY_ANTHROPIC')).toBe('Anthropic API key')
    expect(formatMissingField('LLM_API_KEY_OPENAI')).toBe('OpenAI API key')
  })

  it('passes an unrecognised field through unchanged', () => {
    expect(formatMissingField('SOME_OTHER_VAR')).toBe('SOME_OTHER_VAR')
  })
})
