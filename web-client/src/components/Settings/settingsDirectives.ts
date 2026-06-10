import type { UseSettingsState } from '../../hooks/useSettingsState'

export type SaveStatus =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'error'; message: string }
  | { kind: 'restarting' }

export type DirectiveKind =
  | 'error' | 'warning' | 'info'
  | 'idle' | 'dirty' | 'validating' | 'pending' | 'saved'

export interface Directive { kind: DirectiveKind; text: string }

/**
 * Directives derived from the shell's save / validation state.
 * Tab-published issues stack above these in the rendered output.
 */
export function stateDirectives(
  saveStatus: SaveStatus,
  dirty: boolean,
  state: UseSettingsState,
): Directive[] {
  const out: Directive[] = []
  if (saveStatus.kind === 'error') {
    out.push({ kind: 'error', text: saveStatus.message })
    return out
  }
  if (saveStatus.kind === 'restarting') {
    out.push({ kind: 'pending', text: 'Restarting — your browser will reconnect shortly.' })
    return out
  }
  if (saveStatus.kind === 'saving') {
    out.push({ kind: 'validating', text: 'Saving…' })
  }
  if (state.validation.state === 'validating') {
    out.push({ kind: 'validating', text: 'Validating with providers…' })
  }
  if (state.validation.state === 'failed') {
    out.push({
      kind: 'error',
      text: 'Validation failed — see per-row errors on the Models tab.',
    })
  }
  // Suppressed while dirty: new edits make the saved state stale, and
  // restarting would discard them — show "Unsaved changes" instead.
  if (state.validation.pending_restart && !dirty) {
    out.push({
      kind: 'pending',
      text: 'Saved & validated. Click Restart to apply.',
    })
  }
  if (out.length > 0) return out
  if (saveStatus.kind === 'saved') return [{ kind: 'idle', text: 'Saved' }]
  if (dirty) return [{ kind: 'dirty', text: 'Unsaved changes' }]
  return []
}
