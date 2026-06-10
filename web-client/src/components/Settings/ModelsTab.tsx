/**
 * Per-agent LLM configuration.
 *
 * One row per agent (Coordinator + the three optional sub-agents).
 * Each row picks its own provider / model / API key. Optional rows
 * have an enable toggle — off means no validation, no LLM client.
 *
 * Keys are shared across rows that use the same provider: two rows
 * on Anthropic both read and write ``LLM_API_KEY_ANTHROPIC``.
 */
import React from 'react'
import f from './fields.module.css'
import {
  PasswordField,
  SelectField,
  TestConnectionButton,
  TextField,
  ToggleField,
  type TestState,
} from './fields'
import { LLM_PROVIDERS, getProvider, parseModelString } from './providers'
import { usePublishTabForm, usePublishTabIssue } from './settingsFormContext'
import type { TabIssue } from './settingsFormContext'
import type {
  AgentValidationResult,
  UseSettingsState,
  ValidationAgent,
} from '../../hooks/useSettingsState'

interface AgentSpec {
  id: ValidationAgent
  label: string
  essential: boolean
  enableEnv: string | null
  modelEnv: string
  help: string
}

const AGENTS: AgentSpec[] = [
  {
    id: 'coordinator',
    label: 'Coordinator',
    essential: true,
    enableEnv: null,
    modelEnv: 'LLM_MODEL',
    help:
      'Handles your chat requests. Pick a capable model — the Coordinator shapes the whole experience.',
  },
  {
    id: 'arbiter',
    label: 'Arbiter',
    essential: false,
    enableEnv: 'ENABLE_INTERRUPT_ARBITER',
    modelEnv: 'LLM_MODEL_ARBITER',
    help:
      'Decides what to do when you send a new request while another is still running — queue it, interrupt, or replace. A small fast model is fine.',
  },
  {
    id: 'diagnostic',
    label: 'Diagnostic',
    essential: false,
    enableEnv: 'ENABLE_DIAGNOSTIC_AGENT',
    modelEnv: 'LLM_MODEL_DIAGNOSTIC',
    help:
      'Groups related questions into the same conversation thread. A small fast model is fine.',
  },
  {
    id: 'analyser',
    label: 'Analyser',
    essential: false,
    enableEnv: 'ENABLE_PASSIVE_ANALYSER',
    modelEnv: 'LLM_MODEL_ANALYSER',
    help:
      'Analyses conversations to evaluate assistant performance. A strong model is recommended. When enabled, scans every 30 minutes by default — configuration options are on the Conversation Analyser tab.',
  },
]

interface RowState {
  enabled: boolean
  provider: string
  model: string
}

interface ModelsFormState {
  rows: Record<ValidationAgent, RowState>
  keys: Record<string, string>
}

function isTrue(v: string | null | undefined, defaultValue = false): boolean {
  if (v === null || v === undefined || v === '') return defaultValue
  return v.toLowerCase() === 'true'
}

function buildInitial(state: UseSettingsState): ModelsFormState {
  const values = state.readResult?.values ?? {}
  const defaults = state.readResult?.defaults ?? {}
  const rows = {} as Record<ValidationAgent, RowState>
  for (const agent of AGENTS) {
    const enabled = agent.essential
      ? true
      : isTrue(
          values[agent.enableEnv!] ?? defaults[agent.enableEnv!],
        )
    const { provider, model } = parseModelString(values[agent.modelEnv] || '')
    rows[agent.id] = {
      enabled,
      // No provider defaulted — an unset row shows the "Select provider…"
      // placeholder so its empty state is honest (and matches canTest).
      provider: provider || '',
      model: model || '',
    }
  }
  const keys: Record<string, string> = {}
  for (const p of LLM_PROVIDERS) {
    if (!p.apiKeyEnv) continue
    keys[p.id] = values[p.apiKeyEnv] || ''
  }
  return { rows, keys }
}

const providerOptions = (() => {
  const verified = LLM_PROVIDERS.filter((p) => p.verified)
  const other = LLM_PROVIDERS.filter((p) => !p.verified)
  return [
    {
      label: 'Verified',
      options: verified.map((p) => ({ value: p.id, label: p.label })),
    },
    ...(other.length > 0
      ? [
          {
            label: 'Other LiteLLM providers (untested)',
            options: other.map((p) => ({ value: p.id, label: p.label })),
          },
        ]
      : []),
  ]
})()

interface AgentRowProps {
  agent: AgentSpec
  row: RowState
  keys: Record<string, string>
  onRowChange: (next: RowState) => void
  onKeyChange: (providerId: string, value: string) => void
  validation?: AgentValidationResult
  missing: boolean
  rowDirty: boolean
  state: UseSettingsState
}

const AgentRow: React.FC<AgentRowProps> = ({
  agent, row, keys, onRowChange, onKeyChange, validation, missing, rowDirty, state,
}) => {
  const info = getProvider(row.provider || 'anthropic')
  const isLocal = Boolean(info.local)
  const disabled = !row.enabled

  const inputsClass = disabled ? f.rowInputsDisabled : ''

  const rawKey = keys[info.id] ?? ''

  // Pair the local Test result with a fingerprint of the row inputs
  // it was tested against so the result auto-clears when any input
  // changes. Server validation state is read live below — never
  // copied into local state.
  const fingerprint = `${row.provider}|${row.model}|${rawKey}|${row.enabled}`
  const [testedResult, setTestedResult] = React.useState<
    { fingerprint: string; state: TestState }
  >({ fingerprint: '', state: { kind: 'idle' } })
  const localResult: TestState =
    testedResult.fingerprint === fingerprint ? testedResult.state : { kind: 'idle' }

  // Sub-agent with no explicit provider/model uses Coordinator's config.
  const inheriting =
    !agent.essential && row.enabled && !row.provider && !row.model.trim()

  const apiKey = rawKey.trim()
  const canTest =
    row.enabled
    && !inheriting
    && Boolean(row.provider)
    && Boolean(row.model.trim())
    && (isLocal || !info.apiKeyEnv || Boolean(apiKey))

  const runTest = async () => {
    if (!canTest) return
    const fp = fingerprint
    setTestedResult({ fingerprint: fp, state: { kind: 'testing' } })
    try {
      const result = await state.testProvider({
        provider: row.provider,
        model: row.model.trim(),
        api_key: apiKey,
      })
      if (result.ok) {
        setTestedResult({
          fingerprint: fp,
          state: {
            kind: 'ok',
            detail: result.detail,
            notValidated: Boolean(result.not_validated),
          },
        })
      } else {
        setTestedResult({
          fingerprint: fp,
          state: { kind: 'error', detail: result.detail },
        })
      }
    } catch (err) {
      setTestedResult({
        fingerprint: fp,
        state: {
          kind: 'error',
          detail: err instanceof Error ? err.message : String(err),
        },
      })
    }
  }

  // Server validation state for this row, or null when there's
  // nothing meaningful to report (empty config, disabled row).
  const validationState: TestState | null = (() => {
    if (!row.enabled) return null
    if (inheriting) {
      return {
        kind: 'ok',
        detail: "Uses the Coordinator's configuration",
        notValidated: false,
      }
    }
    if (!validation || validation.ok === null || !validation.provider) return null
    if (validation.ok) {
      return {
        kind: 'ok',
        detail: validation.detail ?? undefined,
        notValidated: validation.not_validated,
      }
    }
    return { kind: 'error', detail: validation.detail ?? undefined }
  })()

  // Display priority: in-flight test > completed local test >
  // (clean ? validation : neutral Test).
  const buttonState: TestState =
    localResult.kind !== 'idle'
      ? localResult
      : rowDirty || !validationState
      ? { kind: 'idle' }
      : validationState

  const buttonTitle = inheriting
    ? "Uses the Coordinator's configuration"
    : !row.enabled
    ? 'This agent is disabled'
    : !canTest
    ? 'Fill provider, model, and API key to test'
    : undefined

  return (
    <section
      className={`${f.providerSection} ${missing ? f.providerSectionMissing : ''}`}
    >
      <div className={f.providerSectionHeader}>
        <span className={f.providerName}>
          {agent.label}
          {agent.essential ? (
            <span className={f.providerRequiredTag}>Essential</span>
          ) : null}
        </span>
        <TestConnectionButton
          state={buttonState}
          onTest={runTest}
          disabled={!canTest}
          label="Test"
          title={buttonTitle}
        />
      </div>

      {!agent.essential ? (
        <div className={f.providerToggleRow}>
          <ToggleField
            id={`enable-${agent.id}`}
            label={row.enabled ? 'Enabled' : 'Disabled'}
            value={row.enabled}
            onChange={(v) => onRowChange({ ...row, enabled: v })}
          />
        </div>
      ) : null}

      <p className={f.rowHelp}>{agent.help}</p>

      <div className={inputsClass} aria-disabled={disabled}>
        <SelectField
          id={`row-${agent.id}-provider`}
          label="Provider"
          value={row.provider}
          onChange={(v) => {
            const next = { ...row, provider: v }
            next.model = ''
            onRowChange(next)
          }}
          options={providerOptions}
          placeholder="Select provider…"
        />
        <TextField
          id={`row-${agent.id}-model`}
          label="Model"
          value={row.model}
          onChange={(v) => onRowChange({ ...row, model: v })}
          placeholder={
            row.enabled
              ? `e.g. ${info.modelPlaceholder}`
              : 'Toggle on to configure'
          }
          monospace
          help={
            !agent.essential && row.enabled && !row.provider && !row.model
              ? "Leave blank to use the Coordinator's model."
              : undefined
          }
        />
        {!isLocal && info.apiKeyEnv ? (
          <PasswordField
            id={`row-${agent.id}-key`}
            label={`${info.label} API key`}
            value={keys[info.id] ?? ''}
            onChange={(v) => onKeyChange(info.id, v)}
            placeholder={
              row.enabled
                ? `Paste your ${info.label} key`
                : 'Toggle on to configure'
            }
            help={
              info.helpUrl ? (
                <a href={info.helpUrl} target="_blank" rel="noopener noreferrer">
                  Get a key from {info.label}
                </a>
              ) : null
            }
          />
        ) : isLocal ? (
          <p className={f.help}>
            {info.label} runs locally and needs no API key.
          </p>
        ) : null}
      </div>
    </section>
  )
}

export const ModelsTab: React.FC<{ state: UseSettingsState }> = ({ state }) => {
  const initial = React.useMemo(
    () => buildInitial(state),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-init when readResult identity changes
    [state.readResult],
  )
  const [form, setForm] = React.useState<ModelsFormState>(initial)
  const lastSeenRef = React.useRef<ModelsFormState>(initial)

  // Per-field merge: fields unchanged from the last server snapshot
  // pick up new values; edited fields stay as-is. Lets a save on
  // another tab refresh clean fields here without wiping in-flight
  // edits.
  React.useEffect(() => {
    setForm((prev) => mergeWithServer(prev, initial, lastSeenRef.current))
    lastSeenRef.current = initial
  }, [initial])

  const updateRow = (agentId: ValidationAgent, next: RowState) => {
    setForm((prev) => ({ ...prev, rows: { ...prev.rows, [agentId]: next } }))
  }

  const updateKey = (providerId: string, value: string) => {
    setForm((prev) => ({ ...prev, keys: { ...prev.keys, [providerId]: value } }))
  }

  const dirty = !shallowEqual(form, initial)

  const missingEnvNames = new Set(state.configMissing)
  const missingForAgent = (agent: AgentSpec): boolean => {
    if (missingEnvNames.has(agent.modelEnv)) return true
    const row = form.rows[agent.id]
    if (!row.enabled) return false
    const info = getProvider(row.provider)
    if (info.apiKeyEnv && missingEnvNames.has(info.apiKeyEnv)) return true
    return false
  }

  const validationByAgent = React.useMemo(() => {
    const m = {} as Record<ValidationAgent, AgentValidationResult>
    for (const r of state.validation.results) {
      m[r.agent] = r
    }
    return m
  }, [state.validation])

  const buildUpdates = React.useCallback((): Record<string, string> => {
    const updates: Record<string, string> = {}
    for (const agent of AGENTS) {
      const row = form.rows[agent.id]
      const initialRow = initial.rows[agent.id]

      if (agent.enableEnv && row.enabled !== initialRow.enabled) {
        updates[agent.enableEnv] = row.enabled ? 'true' : 'false'
      }

      const modelSpec =
        row.provider && row.model
          ? `${row.provider}/${row.model.trim()}`
          : ''
      const initialSpec =
        initialRow.provider && initialRow.model
          ? `${initialRow.provider}/${initialRow.model.trim()}`
          : ''
      if (modelSpec !== initialSpec) {
        updates[agent.modelEnv] = modelSpec
      }
    }
    for (const [providerId, key] of Object.entries(form.keys)) {
      if (key === (initial.keys[providerId] ?? '')) continue
      const info = getProvider(providerId)
      if (!info.apiKeyEnv) continue
      updates[info.apiKeyEnv] = key.trim()
    }
    return updates
  }, [form, initial])

  const reset = React.useCallback(() => {
    setForm(initial)
  }, [initial])

  usePublishTabForm('models', dirty, buildUpdates, reset)

  // Coordinator essentials gate: provider, model, and the API key (when
  // the provider needs one) must all be filled or the agent can't make
  // its first call. We publish the first missing piece as an error so the
  // shell disables Save until it's resolved.
  const essentialsIssue = React.useMemo<TabIssue | null>(() => {
    const coord = form.rows.coordinator
    const info = getProvider(coord.provider)
    if (!coord.provider) {
      return { kind: 'error', text: 'Coordinator provider is required', tabId: 'models' }
    }
    if (!coord.model.trim()) {
      return { kind: 'error', text: 'Coordinator model is required', tabId: 'models' }
    }
    if (info.apiKeyEnv && !(form.keys[coord.provider] ?? '').trim()) {
      return {
        kind: 'error',
        text: `Coordinator ${info.label} API key is required`,
        tabId: 'models',
      }
    }
    return null
  }, [form])
  usePublishTabIssue('models-essentials', essentialsIssue)

  return (
    <div>
      <p className={f.tabIntro}>
        Pick the AI models the assistant uses. Only the Coordinator is
        required — the optional helpers are off by default to keep
        costs in check.
      </p>

      {AGENTS.map((agent) => {
        const row = form.rows[agent.id]
        const initialRow = initial.rows[agent.id]
        const providerKey = row.provider
        const rowDirty =
          row.enabled !== initialRow.enabled
          || row.provider !== initialRow.provider
          || row.model !== initialRow.model
          || (
            providerKey
            && (form.keys[providerKey] ?? '') !== (initial.keys[providerKey] ?? '')
          )
        return (
          <AgentRow
            key={agent.id}
            agent={agent}
            row={row}
            keys={form.keys}
            onRowChange={(next) => updateRow(agent.id, next)}
            onKeyChange={updateKey}
            validation={validationByAgent[agent.id]}
            missing={missingForAgent(agent)}
            rowDirty={Boolean(rowDirty)}
            state={state}
          />
        )
      })}
    </div>
  )
}

function shallowEqual(a: ModelsFormState, b: ModelsFormState): boolean {
  for (const agent of AGENTS) {
    const ra = a.rows[agent.id]
    const rb = b.rows[agent.id]
    if (
      ra.enabled !== rb.enabled ||
      ra.provider !== rb.provider ||
      ra.model !== rb.model
    ) return false
  }
  for (const key of new Set([...Object.keys(a.keys), ...Object.keys(b.keys)])) {
    if ((a.keys[key] ?? '') !== (b.keys[key] ?? '')) return false
  }
  return true
}

function mergeWithServer(
  prev: ModelsFormState,
  next: ModelsFormState,
  lastSeen: ModelsFormState,
): ModelsFormState {
  const rows = {} as Record<ValidationAgent, RowState>
  for (const agent of AGENTS) {
    const p = prev.rows[agent.id]
    const n = next.rows[agent.id]
    const s = lastSeen.rows[agent.id]
    rows[agent.id] = {
      enabled: p.enabled === s.enabled ? n.enabled : p.enabled,
      provider: p.provider === s.provider ? n.provider : p.provider,
      model: p.model === s.model ? n.model : p.model,
    }
  }
  const keys: Record<string, string> = {}
  for (const id of new Set([
    ...Object.keys(prev.keys),
    ...Object.keys(next.keys),
    ...Object.keys(lastSeen.keys),
  ])) {
    const p = prev.keys[id] ?? ''
    const n = next.keys[id] ?? ''
    const s = lastSeen.keys[id] ?? ''
    keys[id] = p === s ? n : p
  }
  return { rows, keys }
}
