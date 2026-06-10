/**
 * Cross-tab form coordination for the Settings page.
 *
 * Each tab publishes its dirty state + a way to extract its diff via
 * ``usePublishTabForm``. The shell collects across all tabs through
 * the registry so the global Save & Validate button can write every
 * tab's pending changes in one request.
 */
import React from 'react'

import {
  RegistryContext,
  type TabAccessor,
  type TabIssue,
} from './settingsFormContext'

export interface AggregateFormState {
  dirty: boolean
  /** Issues raised by tabs, in priority order (error > warning > info). */
  issues: TabIssue[]
  /** True iff any issue has ``kind === 'error'`` — used to gate Save. */
  hasErrors: boolean
  /** Highest-priority issue kind per tab id (error > warning > info).
   *  Tabs without issues are absent from the map. The shell uses this
   *  to render an attention indicator on the nav. */
  issueKindByTab: Record<string, 'error' | 'warning' | 'info'>
  collectUpdates: () => Record<string, string>
  resetAll: () => void
}

interface SettingsFormRegistryProps {
  children: React.ReactNode
  onAggregateChange: (state: AggregateFormState) => void
}

export const SettingsFormRegistry: React.FC<SettingsFormRegistryProps> = ({
  children, onAggregateChange,
}) => {
  const accessorsRef = React.useRef<Map<string, TabAccessor>>(new Map())
  const [dirtyMap, setDirtyMap] = React.useState<Record<string, boolean>>({})
  const [issueMap, setIssueMap] = React.useState<Record<string, TabIssue>>({})

  const registerAccessor = React.useCallback(
    (id: string, accessor: TabAccessor) => {
      accessorsRef.current.set(id, accessor)
    },
    [],
  )

  const unregister = React.useCallback((id: string) => {
    accessorsRef.current.delete(id)
    setDirtyMap((prev) => {
      if (!(id in prev)) return prev
      const next = { ...prev }
      delete next[id]
      return next
    })
    setIssueMap((prev) => {
      if (!(id in prev)) return prev
      const next = { ...prev }
      delete next[id]
      return next
    })
  }, [])

  const setDirty = React.useCallback((id: string, dirty: boolean) => {
    setDirtyMap((prev) =>
      prev[id] === dirty ? prev : { ...prev, [id]: dirty },
    )
  }, [])

  const setIssue = React.useCallback((id: string, issue: TabIssue | null) => {
    setIssueMap((prev) => {
      if (issue === null) {
        if (!(id in prev)) return prev
        const next = { ...prev }
        delete next[id]
        return next
      }
      const existing = prev[id]
      if (existing && existing.kind === issue.kind && existing.text === issue.text) {
        return prev
      }
      return { ...prev, [id]: issue }
    })
  }, [])

  const aggregateDirty = Object.values(dirtyMap).some(Boolean)
  const KIND_PRIORITY: Record<TabIssue['kind'], number> = {
    error: 0, warning: 1, info: 2,
  }
  const issues = React.useMemo(
    () =>
      Object.values(issueMap).sort(
        (a, b) => KIND_PRIORITY[a.kind] - KIND_PRIORITY[b.kind],
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- KIND_PRIORITY is module-constant
    [issueMap],
  )
  const hasErrors = issues.some((i) => i.kind === 'error')
  const issueKindByTab = React.useMemo(() => {
    const out: Record<string, 'error' | 'warning' | 'info'> = {}
    for (const issue of Object.values(issueMap)) {
      const prev = out[issue.tabId]
      if (!prev || KIND_PRIORITY[issue.kind] < KIND_PRIORITY[prev]) {
        out[issue.tabId] = issue.kind
      }
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps -- KIND_PRIORITY is module-constant
  }, [issueMap])

  const collectUpdates = React.useCallback((): Record<string, string> => {
    const merged: Record<string, string> = {}
    for (const accessor of accessorsRef.current.values()) {
      Object.assign(merged, accessor.buildUpdates())
    }
    return merged
  }, [])

  const resetAll = React.useCallback(() => {
    for (const accessor of accessorsRef.current.values()) {
      accessor.reset()
    }
  }, [])

  React.useEffect(() => {
    onAggregateChange({
      dirty: aggregateDirty,
      issues,
      hasErrors,
      issueKindByTab,
      collectUpdates,
      resetAll,
    })
  }, [aggregateDirty, issues, hasErrors, issueKindByTab, onAggregateChange, collectUpdates, resetAll])

  const ctxValue = React.useMemo(
    () => ({ registerAccessor, unregister, setDirty, setIssue }),
    [registerAccessor, unregister, setDirty, setIssue],
  )

  return (
    <RegistryContext.Provider value={ctxValue}>
      {children}
    </RegistryContext.Provider>
  )
}

