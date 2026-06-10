import type { ValidationStatus } from './hooks/useSettingsState'

/**
 * Soft (amber) warning for the Settings nav icon: a configured backend
 * (web search / TTS) is unreachable, or an enabled optional agent
 * (arbiter / diagnostic / analyser) failed live LLM validation.
 *
 * The compulsory coordinator is excluded — its failure drives the hard
 * red `requiresSettings` route (forced Settings), which takes precedence
 * over this badge. `ok === null` means "not validated yet", not a failure.
 */
export function hasSoftSettingsWarning(validation: ValidationStatus): boolean {
  const backendUnreachable = validation.backends.some((b) => !b.ok)
  const optionalAgentFailed = validation.results.some(
    (r) => r.enabled && r.ok === false && r.agent !== 'coordinator',
  )
  return backendUnreachable || optionalAgentFailed
}
