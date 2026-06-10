/**
 * Central state for the Settings UI.
 *
 * Subscribes to `feature-availability` (config / Roon / provider-check
 * fields) and to the settings-*-response channels. Exposes the
 * current `.env` snapshot, gating signals, and action helpers that
 * send a request and resolve a promise when the matching response
 * arrives — saves callers from filtering messages themselves.
 */
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useWebSocket } from '../websocketContext'
import type { ChannelId, SocketMessage } from '../websocketContext'
import { rememberBundleMode } from '../bundleMode'

export interface ProviderCheckResult {
  ok: boolean
  provider: string
  detail?: string
  error_kind?: 'auth_failed' | 'network' | 'not_found' | 'bad_request' | 'other'
  not_validated?: boolean
}

export type ValidationAgent = 'coordinator' | 'arbiter' | 'diagnostic' | 'analyser'

export type ValidationStateValue = 'open' | 'validating' | 'failed' | 'passed'

export interface AgentValidationResult {
  agent: ValidationAgent
  enabled: boolean
  provider: string | null
  model: string | null
  inherits_coordinator: boolean
  ok: boolean | null
  error_kind?: 'auth_failed' | 'network' | 'not_found' | 'bad_request' | 'other' | null
  detail?: string | null
  not_validated: boolean
}

export interface BackendReachabilityResult {
  backend: 'web-search' | 'tts'
  label: string
  ok: boolean
  error_kind?: 'auth_failed' | 'network' | 'not_found' | 'missing_credential' | 'other' | null
  detail?: string | null
}

export interface ValidationStatus {
  state: ValidationStateValue
  results: AgentValidationResult[]
  backends: BackendReachabilityResult[]
  pending_restart: boolean
}

export interface SettingsReadResult {
  ok: boolean
  env_path: string
  values: Record<string, string | null>
  /** Agent's implicit defaults for env vars (currently the bool
   * toggles). UI falls back to these when an env var isn't
   * explicitly set, so toggles render the effective value. */
  defaults: Record<string, string>
  secret_fields: string[]
  config_missing: string[]
  /** False in Docker (host .env not mounted; values come from
   * compose's env_file: injection into os.environ). UI uses this
   * to disable inputs + the Save / Reload / Restart buttons and
   * show the host-edit banner. Source/bundle: true. */
  editable: boolean
  /** Human-readable explanation rendered in the banner when
   * ``editable`` is false. null when editable. */
  editing_disabled_reason: string | null
}

export interface SettingsSaveResult {
  ok: boolean
  env_path?: string
  config_missing?: string[]
  updated_keys?: string[]
  invalid_keys?: string[]
  error?: string
}

export interface SettingsReloadResult {
  ok: boolean
  env_path?: string
  config_missing?: string[]
  error?: string
}

export type RoonState =
  | 'initialising'
  | 'paired'
  | 'failed'
  // Agent is waiting for required config (LLM_MODEL etc.) before
  // running ensure_initialised — the Settings page handles this case
  // and `requiresSettings` routing takes precedence in App.tsx, so
  // the user never lands on the RoonSetup view in this state.
  | 'awaiting_config'

export interface FeatureAvailabilityState {
  configComplete: boolean
  configMissing: string[]
  /** No assistant-configuration value has been set yet (fresh install).
   *  Drives the first-run Getting Started intro; durable across restarts
   *  since the agent derives it from the persisted config. */
  configPristine: boolean
  /** Roon setup phase. App routes to the RoonSetup view while not
   * "paired". Optional / undefined while agent hasn't reported yet. */
  roonState: RoonState
  roonStatusMessage: string
  roonFailureReason: string | null
  /** TTS_URL is set. Independent of reachability so a flapping
   * TCP target doesn't flicker the "Not Configured" chip. */
  ttsConfigured: boolean
  /** TTS_URL set AND the latest reachability probe passed. */
  ttsAvailable: boolean
  /** Dev-only Roon API Explorer panel. Hidden when ENABLE_ROON_EXPLORER
   * is unset, so the entry button never appears for normal users. */
  roonExplorerEnabled: boolean
  /** Running as the desktop bundle. Gates bundle-only guidance (the
   * stop-marker setup section + open-folder button in Getting Started). */
  isBundle: boolean
  /** True until we've received our first feature-availability message
   * — used to defer routing decisions so we don't flash the Settings
   * page during the WS handshake. */
  awaitingFirstUpdate: boolean
}

const CHANNEL_FEATURE_AVAILABILITY: ChannelId = 'feature-availability'
const CHANNEL_VALIDATION_STATUS: ChannelId = 'validation-status'
const CHANNEL_READ_REQUEST: ChannelId = 'settings-read-request'
const CHANNEL_READ_RESPONSE: ChannelId = 'settings-read-response'
const CHANNEL_SAVE_REQUEST: ChannelId = 'settings-save-request'
const CHANNEL_SAVE_RESPONSE: ChannelId = 'settings-save-response'
const CHANNEL_RELOAD_REQUEST: ChannelId = 'settings-reload-request'
const CHANNEL_RELOAD_RESPONSE: ChannelId = 'settings-reload-response'
const CHANNEL_TEST_REQUEST: ChannelId = 'settings-test-request'
const CHANNEL_TEST_RESPONSE: ChannelId = 'settings-test-response'

const RESPONSE_TIMEOUT_MS = 30_000

function parsePayload<T = unknown>(message: SocketMessage): T | null {
  if (message.payload != null) {
    return message.payload as T
  }
  try {
    return JSON.parse(message.body) as T
  } catch {
    return null
  }
}

function newRequestId(): string {
  // Adequate uniqueness for in-flight request correlation; we don't
  // need cryptographic randomness here.
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

export function useSettingsState() {
  const { messages, sendMessage, status } = useWebSocket()

  // Derive the latest availability snapshot from the messages stream
  // by useMemo (rather than accumulating via setState) so the
  // react-hooks linter doesn't complain about setState-in-effect.
  // Walking the whole array per render is cheap — messages are
  // already in memory and we only need the most recent.
  const availability = useMemo<FeatureAvailabilityState>(() => {
    let latest: FeatureAvailabilityState = {
      configComplete: false,
      configMissing: [],
      configPristine: false,
      roonState: 'initialising',
      roonStatusMessage: '',
      roonFailureReason: null,
      ttsConfigured: false,
      ttsAvailable: false,
      roonExplorerEnabled: false,
      isBundle: false,
      awaitingFirstUpdate: true,
    }
    for (const msg of messages) {
      if (msg.direction !== 'inbound') continue
      if (msg.channel !== CHANNEL_FEATURE_AVAILABILITY) continue
      const payload = parsePayload<{
        config_complete?: boolean
        config_missing?: string[]
        config_pristine?: boolean
        roon_state?: RoonState
        roon_status_message?: string
        roon_failure_reason?: string | null
        tts_configured?: boolean
        tts_available?: boolean
        roon_explorer_enabled?: boolean
        is_bundle?: boolean
      }>(msg)
      if (!payload) continue
      latest = {
        configComplete: payload.config_complete ?? false,
        configMissing: payload.config_missing ?? [],
        configPristine: payload.config_pristine ?? false,
        roonState: payload.roon_state ?? 'initialising',
        roonStatusMessage: payload.roon_status_message ?? '',
        roonFailureReason: payload.roon_failure_reason ?? null,
        ttsConfigured: Boolean(payload.tts_configured),
        ttsAvailable: Boolean(payload.tts_available),
        roonExplorerEnabled: Boolean(payload.roon_explorer_enabled),
        isBundle: Boolean(payload.is_bundle),
        awaitingFirstUpdate: false,
      }
    }
    return latest
  }, [messages])

  // Persist the run mode so a cold load after the agent exits (the bundle
  // navigate-away case) can still tailor the unreachable message. The
  // awaitingFirstUpdate guard keeps a never-connected cold load from wiping it.
  useEffect(() => {
    if (!availability.awaitingFirstUpdate) {
      rememberBundleMode(availability.isBundle)
    }
  }, [availability.awaitingFirstUpdate, availability.isBundle])

  // Latest validation status — the server pushes a snapshot on
  // connect and on every state transition (OPEN → VALIDATING →
  // PASSED|FAILED), so we just keep the most recent.
  const validation = useMemo<ValidationStatus>(() => {
    let latest: ValidationStatus = {
      state: 'open',
      results: [],
      backends: [],
      pending_restart: false,
    }
    for (const msg of messages) {
      if (msg.direction !== 'inbound') continue
      if (msg.channel !== CHANNEL_VALIDATION_STATUS) continue
      const payload = parsePayload<ValidationStatus>(msg)
      if (!payload) continue
      latest = payload
    }
    return latest
  }, [messages])

  // Same pattern for the .env snapshot — derive from the latest read
  // response in the message stream.
  const readResult = useMemo((): SettingsReadResult | null => {
    let found: SettingsReadResult | null = null
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const msg = messages[i]
      if (msg.direction !== 'inbound') continue
      if (msg.channel !== CHANNEL_READ_RESPONSE) continue
      found = parsePayload<SettingsReadResult>(msg)
      break
    }
    return found
  }, [messages])

  // Track in-flight requests by request_id so multiple concurrent
  // actions (e.g. testing one provider while saving another) can
  // resolve without stepping on each other.
  const pendingRef = useRef<
    Map<string, (response: unknown) => void>
  >(new Map())

  const lastProcessedRef = useRef<number>(-1)

  // Effect resolves pending in-flight request promises. No setState
  // calls here — derived state above handles that path.
  useEffect(() => {
    const startIdx = lastProcessedRef.current + 1
    for (let i = startIdx; i < messages.length; i++) {
      const msg = messages[i]
      if (msg.direction !== 'inbound') continue
      if (
        msg.channel !== CHANNEL_READ_RESPONSE &&
        msg.channel !== CHANNEL_SAVE_RESPONSE &&
        msg.channel !== CHANNEL_RELOAD_RESPONSE &&
        msg.channel !== CHANNEL_TEST_RESPONSE
      ) {
        continue
      }
      const payload = parsePayload<{ request_id?: string }>(msg)
      if (!payload) continue
      const requestId = payload.request_id
      if (!requestId) continue
      const resolver = pendingRef.current.get(requestId)
      if (resolver) {
        pendingRef.current.delete(requestId)
        resolver(payload)
      }
    }
    lastProcessedRef.current = messages.length - 1
  }, [messages])

  // Cancel pending requests on unmount so we don't leak resolvers.
  useEffect(
    () => () => {
      pendingRef.current.clear()
    },
    [],
  )

  const sendAndAwait = useCallback(
    <T>(channel: ChannelId, body: Record<string, unknown>): Promise<T> =>
      new Promise<T>((resolve, reject) => {
        const requestId = newRequestId()
        const message = JSON.stringify({ ...body, request_id: requestId })
        const timeout = window.setTimeout(() => {
          if (pendingRef.current.has(requestId)) {
            pendingRef.current.delete(requestId)
            reject(new Error(`Timed out waiting for ${channel}`))
          }
        }, RESPONSE_TIMEOUT_MS)
        pendingRef.current.set(requestId, (payload) => {
          window.clearTimeout(timeout)
          resolve(payload as T)
        })
        sendMessage(channel, message)
      }),
    [sendMessage],
  )

  const readSettings = useCallback(
    (): Promise<SettingsReadResult> =>
      sendAndAwait<SettingsReadResult>(CHANNEL_READ_REQUEST, {}),
    [sendAndAwait],
  )

  const saveSettings = useCallback(
    async (
      updates: Record<string, string>,
      options: { restart?: boolean } = {},
    ): Promise<SettingsSaveResult> => {
      const result = await sendAndAwait<SettingsSaveResult>(
        CHANNEL_SAVE_REQUEST,
        { updates, restart: Boolean(options.restart) },
      )
      // Refresh readResult so every other tab's form sees the new
      // values (e.g. the API key saved via Essentials reflects in
      // the API Keys tab's matching row). Skip on restart — the
      // process exits and we'll get a fresh read on reconnect.
      if (result.ok && !options.restart) {
        readSettings().catch(() => {})
      }
      return result
    },
    [sendAndAwait, readSettings],
  )

  const reloadSettings = useCallback(
    (): Promise<SettingsReloadResult> =>
      sendAndAwait<SettingsReloadResult>(CHANNEL_RELOAD_REQUEST, {}),
    [sendAndAwait],
  )

  const testProvider = useCallback(
    (payload: Record<string, unknown>): Promise<ProviderCheckResult> =>
      sendAndAwait<ProviderCheckResult>(CHANNEL_TEST_REQUEST, payload),
    [sendAndAwait],
  )

  // Auto-fetch the current .env once the WS is actually connected.
  // Firing on mount alone is unsafe: WebSocketProvider's sendMessage
  // silently drops messages when the socket isn't open, and the
  // initial mount happens while status is still 'connecting'. Re-
  // firing on every status transition to 'open' also handles the
  // save-and-restart path — the agent exits, the WS goes 'closed',
  // a new connection opens after the supervisor / Docker / manual
  // relaunch, and we pick up the fresh .env values automatically.
  useEffect(() => {
    if (status !== 'open') return
    readSettings().catch(() => {
      /* error surfaced by the page when it tries to read again */
    })
  }, [status, readSettings])

  return useMemo(
    () => ({
      ...availability,
      readResult,
      validation,
      readSettings,
      saveSettings,
      reloadSettings,
      testProvider,
    }),
    [availability, readResult, validation, readSettings, saveSettings, reloadSettings, testProvider],
  )
}

export type UseSettingsState = ReturnType<typeof useSettingsState>
