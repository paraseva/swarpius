/**
 * F5-TTS server configuration.
 *
 * One setting (``TTS_URL``) drives both CLI-mode and browser-side
 * speech — the agent proxies its TCP connection to a WebSocket on
 * its own ``/tts`` path. The Test button hits the agent's
 * settings-test endpoint, which TCP-connects to the configured
 * address from the server side.
 */
import React from 'react'
import f from './fields.module.css'
import {
  TestConnectionButton,
  TestResult,
  TextField,
  type TestState,
} from './fields'
import { useTabForm } from './useTabForm'
import { usePublishTabForm } from './settingsFormContext'
import type { UseSettingsState } from '../../hooks/useSettingsState'

const FIELDS = ['TTS_URL'] as const
type FieldKey = (typeof FIELDS)[number]

const TTS_SCHEMES = /^(tcp|http|https|ws|wss):\/\//i

function stripTtsScheme(value: string): string {
  return value.replace(TTS_SCHEMES, '')
}

export const SpeechTab: React.FC<{ state: UseSettingsState }> = ({ state }) => {
  const form = useTabForm<Record<FieldKey, string>>({
    state,
    fields: FIELDS,
    readField: (envValues, field, defaults) => {
      const raw = envValues[field as string]
      const value =
        raw !== null && raw !== undefined && raw !== ''
          ? raw
          : defaults[field as string] ?? ''
      return field === 'TTS_URL' ? stripTtsScheme(value) : value
    },
  })
  usePublishTabForm('speech', form.dirty, form.buildUpdates, form.reset)

  // Pair the test result with the URL it was tested against so the
  // result auto-clears when the user edits the field — no effect+
  // setState round-trip needed.
  const [testedResult, setTestedResult] = React.useState<
    { url: string; state: TestState }
  >({ url: '', state: { kind: 'idle' } })
  const localResult: TestState =
    testedResult.url === form.values.TTS_URL ? testedResult.state : { kind: 'idle' }

  const runTest = async () => {
    const url = form.values.TTS_URL
    if (!url.trim()) return
    setTestedResult({ url, state: { kind: 'testing' } })
    try {
      // matches_saved lets the agent persist the result to the live
      // validation status only when the field is unchanged from what's
      // saved (a test of unsaved edits stays ephemeral).
      const result = await state.testProvider({
        provider: 'tts', url, matches_saved: !form.dirty,
      })
      if (result.ok) {
        setTestedResult({
          url,
          state: {
            kind: 'ok',
            detail: result.detail,
            notValidated: Boolean(result.not_validated),
          },
        })
      } else {
        setTestedResult({ url, state: { kind: 'error', detail: result.detail } })
      }
    } catch (err) {
      setTestedResult({
        url,
        state: {
          kind: 'error',
          detail: err instanceof Error ? err.message : String(err),
        },
      })
    }
  }

  const ttsBackend = state.validation.backends.find((b) => b.backend === 'tts')
  const validationState: TestState | null = ttsBackend
    ? ttsBackend.ok
      ? { kind: 'ok', detail: ttsBackend.detail ?? undefined, notValidated: false }
      : { kind: 'error', detail: ttsBackend.detail ?? undefined }
    : null

  // Display priority: in-flight test > completed local test > (clean ?
  // server validation : neutral). Local test wins over server because
  // the user just acted on the displayed values; validation may be
  // for older saved values.
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
        Optional. Point this at an F5-TTS server to give the assistant
        a spoken voice. Leave blank to disable speech.
      </p>

      <TextField
        id="speech-tts-url"
        label="F5-TTS Server URL"
        value={form.values.TTS_URL}
        onChange={(v) => form.setField('TTS_URL', stripTtsScheme(v))}
        placeholder="e.g. localhost:9998"
        monospace
        help={
          <>
            Host and port only (e.g. <code>localhost:9998</code>) — no{' '}
            <code>http://</code> or <code>ws://</code> prefix needed; the
            connection type is handled automatically.
          </>
        }
        trailing={
          <TestConnectionButton
            state={buttonState}
            onTest={runTest}
            disabled={!form.values.TTS_URL.trim()}
            label="Test"
          />
        }
      />
      <TestResult result={testResultState} />
    </div>
  )
}
