/**
 * Form-state machinery for Settings tabs.
 *
 * Single source of truth: the server's last-read .env snapshot.
 * Each tab stores ONLY the diff (fields the user has edited away
 * from the snapshot). dirty / buildUpdates / reset all derive from
 * that diff. Editing back to the saved value automatically prunes
 * the entry so dirty stays honest.
 */
import React from 'react'
import type { UseSettingsState } from '../../hooks/useSettingsState'

export interface UseTabFormResult<F extends Record<string, string>> {
  values: F
  setField: (key: keyof F, value: string) => void
  dirty: boolean
  /** True iff the user has edited this specific field away from the
   * current server snapshot. */
  isFieldDirty: (key: keyof F) => boolean
  /** The set of field names currently edited away from the server
   * snapshot — used by Test buttons to mirror server validation
   * when in sync. */
  dirtyFields: ReadonlySet<keyof F>
  /** Returns just the edited fields. Empty when nothing changed. */
  buildUpdates: () => Record<string, string>
  /** Discard all local edits — values snap back to the server snapshot. */
  reset: () => void
}

interface UseTabFormOptions<F extends Record<string, string>> {
  state: UseSettingsState
  fields: ReadonlyArray<keyof F>
  readField?: (
    envValues: Record<string, string | null>,
    field: keyof F,
    defaults: Record<string, string>,
  ) => string
}

function defaultReadField<F extends Record<string, string>>(
  envValues: Record<string, string | null>,
  field: keyof F,
  defaults: Record<string, string>,
): string {
  const raw = envValues[field as string]
  if (raw !== null && raw !== undefined && raw !== '') return raw
  return defaults[field as string] ?? ''
}

export function useTabForm<F extends Record<string, string>>(
  options: UseTabFormOptions<F>,
): UseTabFormResult<F> {
  const { state, fields } = options
  const reader = options.readField ?? defaultReadField<F>

  const serverValues = React.useMemo(() => {
    const envValues = state.readResult?.values ?? {}
    const defaults = state.readResult?.defaults ?? {}
    const out = {} as F
    for (const field of fields) {
      out[field] = reader(envValues, field, defaults) as F[typeof field]
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-derive when readResult identity changes
  }, [state.readResult, fields])

  const [edits, setEdits] = React.useState<Partial<F>>({})

  // Prune edits that no longer differ from the server snapshot. Covers
  // both "user typed it back" and "server caught up after Save". The
  // reconciliation has to happen post-render — the comparison is
  // between local edits and a server snapshot, which can't be reduced
  // to derived state.
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEdits((prev) => {
      let changed = false
      const next: Partial<F> = {}
      for (const k of Object.keys(prev) as (keyof F)[]) {
        const v = prev[k]
        if (v !== undefined && v !== serverValues[k]) {
          next[k] = v
        } else {
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [serverValues])

  const values = React.useMemo(
    () => ({ ...serverValues, ...edits }) as F,
    [serverValues, edits],
  )

  const setField = React.useCallback(
    (key: keyof F, value: string) => {
      setEdits((prev) => {
        if (value === serverValues[key]) {
          if (!(key in prev)) return prev
          const next = { ...prev }
          delete next[key]
          return next
        }
        if (prev[key] === value) return prev
        return { ...prev, [key]: value as F[typeof key] }
      })
    },
    [serverValues],
  )

  const dirtyFields = React.useMemo(
    () => new Set(Object.keys(edits) as (keyof F)[]),
    [edits],
  )
  const dirty = dirtyFields.size > 0
  const isFieldDirty = React.useCallback(
    (key: keyof F) => dirtyFields.has(key),
    [dirtyFields],
  )

  const buildUpdates = React.useCallback((): Record<string, string> => {
    const updates: Record<string, string> = {}
    for (const k of Object.keys(edits) as (keyof F)[]) {
      updates[k as string] = (edits[k] ?? '').trim()
    }
    return updates
  }, [edits])

  const reset = React.useCallback(() => {
    setEdits({})
  }, [])

  return {
    values, setField, dirty, isFieldDirty, dirtyFields, buildUpdates, reset,
  }
}
