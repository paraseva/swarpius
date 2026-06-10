/**
 * Shared context + types for the Settings form registry.
 *
 * Split from ``SettingsFormRegistry.tsx`` so the file-level
 * react-refresh rule (only-export-components) stays happy — the
 * registry component lives there; non-component exports live here.
 */
import React from 'react'

export interface TabAccessor {
  buildUpdates: () => Record<string, string>
  reset: () => void
}

/**
 * A form-state correctness signal a tab can surface to the shell.
 * ``error`` blocks Save & Validate; ``warning`` / ``info`` just
 * render as directives. ``tabId`` lets the shell mark the owning
 * tab in the nav so the user knows where to look.
 */
export interface TabIssue {
  kind: 'error' | 'warning' | 'info'
  text: string
  tabId: string
}

export interface RegistryContextValue {
  registerAccessor: (tabId: string, accessor: TabAccessor) => void
  unregister: (tabId: string) => void
  setDirty: (tabId: string, dirty: boolean) => void
  setIssue: (tabId: string, issue: TabIssue | null) => void
}

export const RegistryContext =
  React.createContext<RegistryContextValue | null>(null)


/**
 * True iff the Settings UI should render inputs as disabled
 * (read-only mode). Set by ``Settings.tsx`` from
 * ``state.readResult.editable``; consumed by every field
 * component so a single flip at the root disables the whole form.
 *
 * Currently only Docker mode sets this true — the host ``.env``
 * isn't mounted into the container, so saves can't persist.
 */
export const FieldsDisabledContext = React.createContext<boolean>(false)


/**
 * Tabs call this every render to publish their current dirty state
 * + a stable accessor for extracting their diff at save time. The
 * accessor's closure reads from a ref so its identity stays stable,
 * avoiding registry churn on keystrokes.
 */
export function usePublishTabForm(
  id: string,
  dirty: boolean,
  buildUpdates: () => Record<string, string>,
  reset: () => void,
): void {
  const ctx = React.useContext(RegistryContext)

  const liveRef = React.useRef({ buildUpdates, reset })
  React.useEffect(() => {
    liveRef.current = { buildUpdates, reset }
  })

  const accessor = React.useMemo<TabAccessor>(
    () => ({
      buildUpdates: () => liveRef.current.buildUpdates(),
      reset: () => liveRef.current.reset(),
    }),
    [],
  )

  React.useEffect(() => {
    if (!ctx) return
    ctx.registerAccessor(id, accessor)
    return () => ctx.unregister(id)
  }, [ctx, id, accessor])

  React.useEffect(() => {
    if (!ctx) return
    ctx.setDirty(id, dirty)
  }, [ctx, id, dirty])
}


/**
 * Tabs call this to surface a correctness issue (missing required
 * field, invalid value) to the Settings shell. ``error`` kind blocks
 * Save & Validate; lighter kinds just render as directives. Pass
 * ``null`` when there's no issue. Memoise ``issue`` (useMemo) so
 * the effect doesn't re-fire on every keystroke.
 */
export function usePublishTabIssue(
  id: string,
  issue: TabIssue | null,
): void {
  const ctx = React.useContext(RegistryContext)
  React.useEffect(() => {
    if (!ctx) return
    ctx.setIssue(id, issue)
    return () => ctx.setIssue(id, null)
  }, [ctx, id, issue])
}
