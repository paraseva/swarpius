/**
 * Web search backend configuration. Three backends — Brave, Tavily,
 * or self-hosted SearXNG. Only the selected backend's credential
 * field is shown.
 */
import React from 'react'
import f from './fields.module.css'
import {
  PasswordField,
  SelectField,
  TestConnectionButton,
  TestResult,
  TextField,
  type TestState,
} from './fields'
import { useTabForm } from './useTabForm'
import { usePublishTabForm, FieldsDisabledContext } from './settingsFormContext'
import { parseSearxngUrl, combineSearxngUrl, type SearxngScheme } from './searxngUrl'
import type { UseSettingsState } from '../../hooks/useSettingsState'

const FIELDS = [
  'WEB_SEARCH_PROVIDER',
  'BRAVE_API_KEY',
  'TAVILY_API_KEY',
  'SEARXNG_URL',
] as const

type FieldKey = (typeof FIELDS)[number]

const PROVIDER_OPTIONS = [
  { value: '', label: 'None (disable web search)' },
  { value: 'brave', label: 'Brave Search' },
  { value: 'tavily', label: 'Tavily' },
  { value: 'searxng', label: 'SearXNG (self-hosted)' },
]

export const WebSearchTab: React.FC<{ state: UseSettingsState }> = ({ state }) => {
  const form = useTabForm<Record<FieldKey, string>>({
    state,
    fields: FIELDS,
  })
  usePublishTabForm('web-search', form.dirty, form.buildUpdates, form.reset)

  const provider = form.values.WEB_SEARCH_PROVIDER.trim().toLowerCase()
  const fieldsDisabled = React.useContext(FieldsDisabledContext)
  // Split SEARXNG_URL into a protocol dropdown + host so the scheme is
  // always explicit (the backend needs http(s)://, and a missing scheme
  // would otherwise fail silently).
  const searxng = parseSearxngUrl(form.values.SEARXNG_URL)
  const searxngScheme = searxng.scheme ?? 'http://'

  // Pair the result with the form-values fingerprint it was tested
  // against so the result auto-clears when any input changes — no
  // effect+setState round-trip needed.
  const currentFingerprint = `${form.values.WEB_SEARCH_PROVIDER}|${form.values.BRAVE_API_KEY}|${form.values.TAVILY_API_KEY}|${form.values.SEARXNG_URL}`
  const [testedResult, setTestedResult] = React.useState<
    { fingerprint: string; state: TestState }
  >({ fingerprint: '', state: { kind: 'idle' } })
  const localResult: TestState =
    testedResult.fingerprint === currentFingerprint
      ? testedResult.state
      : { kind: 'idle' }

  const runTest = async () => {
    if (!provider) return
    const fingerprint = currentFingerprint
    setTestedResult({ fingerprint, state: { kind: 'testing' } })
    try {
      // matches_saved lets the agent persist the result to the live
      // validation status only when nothing on the tab is unsaved (a test
      // of unsaved edits stays ephemeral).
      const payload = {
        ...(provider === 'searxng'
          ? { provider: 'searxng', url: form.values.SEARXNG_URL }
          : provider === 'brave'
          ? { provider: 'brave', api_key: form.values.BRAVE_API_KEY }
          : { provider: 'tavily', api_key: form.values.TAVILY_API_KEY }),
        matches_saved: !form.dirty,
      }
      const result = await state.testProvider(payload)
      if (result.ok) {
        setTestedResult({
          fingerprint,
          state: {
            kind: 'ok',
            detail: result.detail,
            notValidated: Boolean(result.not_validated),
          },
        })
      } else {
        setTestedResult({ fingerprint, state: { kind: 'error', detail: result.detail } })
      }
    } catch (err) {
      setTestedResult({
        fingerprint,
        state: {
          kind: 'error',
          detail: err instanceof Error ? err.message : String(err),
        },
      })
    }
  }

  const webBackend = state.validation.backends.find(
    (b) => b.backend === 'web-search',
  )
  const validationState: TestState | null = webBackend
    ? webBackend.ok
      ? { kind: 'ok', detail: webBackend.detail ?? undefined, notValidated: false }
      : { kind: 'error', detail: webBackend.detail ?? undefined }
    : null
  const buttonState: TestState =
    localResult.kind !== 'idle'
      ? localResult
      : form.dirty || !validationState
      ? { kind: 'idle' }
      : validationState
  const testResultState =
    localResult.kind !== 'idle' ? localResult : buttonState

  return (
    <div>
      <p className={f.tabIntro}>
        Strongly recommended. Without web search, the assistant can
        only answer from what it learned during training — it'll
        struggle with recent events or anything specific to your
        library.
      </p>

      <SelectField
        id="web-search-provider"
        label="Service"
        value={form.values.WEB_SEARCH_PROVIDER}
        onChange={(v) => form.setField('WEB_SEARCH_PROVIDER', v)}
        options={PROVIDER_OPTIONS}
        help="Brave has a free tier for casual use; Tavily is a paid hosted option; SearXNG is self-hosted."
      />

      {provider === 'brave' ? (
        <>
          <PasswordField
            id="web-search-brave-key"
            label="Brave API key"
            value={form.values.BRAVE_API_KEY}
            onChange={(v) => form.setField('BRAVE_API_KEY', v)}
            placeholder="Paste your Brave Search API key here"
            help={
              <a href="https://brave.com/search/api/" target="_blank" rel="noopener noreferrer">
                Get a Brave Search API key
              </a>
            }
            trailing={
              <TestConnectionButton
                state={buttonState}
                onTest={runTest}
                disabled={!form.values.BRAVE_API_KEY.trim()}
              />
            }
          />
          <TestResult result={testResultState} />
        </>
      ) : provider === 'tavily' ? (
        <>
          <PasswordField
            id="web-search-tavily-key"
            label="Tavily API key"
            value={form.values.TAVILY_API_KEY}
            onChange={(v) => form.setField('TAVILY_API_KEY', v)}
            placeholder="Paste your Tavily API key here"
            help={
              <a href="https://tavily.com/" target="_blank" rel="noopener noreferrer">
                Get a Tavily API key
              </a>
            }
            trailing={
              <TestConnectionButton
                state={buttonState}
                onTest={runTest}
                disabled={!form.values.TAVILY_API_KEY.trim()}
              />
            }
          />
          <TestResult result={testResultState} />
        </>
      ) : provider === 'searxng' ? (
        <>
          <TextField
            id="web-search-searxng-url"
            label="SearXNG Server URL"
            value={searxng.rest}
            onChange={(v) => {
              const parsed = parseSearxngUrl(v)
              form.setField(
                'SEARXNG_URL',
                combineSearxngUrl(parsed.scheme ?? searxngScheme, parsed.rest),
              )
            }}
            placeholder="localhost:8888"
            monospace
            help="Host and port of your SearXNG instance (a path is optional)."
            leading={
              <select
                className={f.select}
                style={{ flex: '0 0 auto', width: 'auto' }}
                value={searxngScheme}
                onChange={(e) =>
                  form.setField(
                    'SEARXNG_URL',
                    combineSearxngUrl(e.target.value as SearxngScheme, searxng.rest),
                  )
                }
                disabled={fieldsDisabled}
                aria-label="Protocol"
              >
                <option value="http://">http://</option>
                <option value="https://">https://</option>
              </select>
            }
            trailing={
              <TestConnectionButton
                state={buttonState}
                onTest={runTest}
                disabled={!searxng.rest.trim()}
              />
            }
          />
          <TestResult result={testResultState} />
        </>
      ) : null}
    </div>
  )
}
