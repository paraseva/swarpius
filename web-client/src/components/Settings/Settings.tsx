/**
 * Settings page shell.
 *
 * Hosts the tab navigation, the global Save & Validate / Restart
 * buttons, and the directive line. All form state is owned by the
 * individual tabs; they publish their dirty state + a buildUpdates
 * accessor via ``SettingsFormRegistry`` so the shell can collect
 * across tabs and submit in one save request.
 */
import React from 'react'
import s from './Settings.module.css'
import { formatAgentIssues, formatBackendIssue, formatMissingField } from '../../validationStrings'
import { AnalyserTab } from './AnalyserTab'
import { ModelsTab } from './ModelsTab'
import { PersonaTab } from './PersonaTab'
import { PrivacyTab } from './PrivacyTab'
import { RoonTab } from './RoonTab'
import { SpeechTab } from './SpeechTab'
import { WebSearchTab } from './WebSearchTab'
import { SettingsFormRegistry, type AggregateFormState } from './SettingsFormRegistry'
import { FieldsDisabledContext } from './settingsFormContext'
import { GuidanceButton } from '../GuidanceButton'
import { useGettingStarted } from '../gettingStartedContext'
import { useUpdateCheck, useUpdateCheckEnabled, UPDATE_RELEASES_URL } from '../../updateCheck'
import { useScrollEdges } from '../../hooks/useScrollEdges'
import { CloseIcon } from '../CloseIcon'
import { AttributionsModal } from '../AttributionsModal'
import { stateDirectives, type SaveStatus } from './settingsDirectives'
import { useWebSocket } from '../../websocketContext'
import type {
  BackendReachabilityResult,
  UseSettingsState,
} from '../../hooks/useSettingsState'

export type SettingsTabId =
  | 'models'
  | 'roon'
  | 'web-search'
  | 'speech'
  | 'analyser'
  | 'persona'
  | 'privacy'

interface TabSpec {
  id: SettingsTabId
  label: string
  required?: boolean
}

const TABS: TabSpec[] = [
  { id: 'models', label: 'Models', required: true },
  { id: 'roon', label: 'Roon' },
  { id: 'web-search', label: 'Web Search' },
  { id: 'speech', label: 'Text-to-Speech' },
  { id: 'analyser', label: 'Conversation Analyser' },
  { id: 'persona', label: 'Persona' },
  { id: 'privacy', label: 'Privacy & Data' },
]

interface SettingsProps {
  state: UseSettingsState
  forceOpen: boolean
  onClose?: () => void
  /** Called when a restart is initiated from Settings. The app decides what
   *  to do — it only leaves the Settings view if that's the current view. */
  onRestart?: () => void
}

export const Settings: React.FC<SettingsProps> = ({ state, forceOpen, onClose, onRestart }) => {
  const { markRestarting } = useWebSocket()
  const gettingStarted = useGettingStarted()
  const [activeTab, setActiveTab] = React.useState<SettingsTabId>('models')
  const [showLicences, setShowLicences] = React.useState(false)
  const [reloadStatus, setReloadStatus] = React.useState<
    'idle' | 'loading' | 'ok' | 'error'
  >('idle')
  const [saveStatus, setSaveStatus] = React.useState<SaveStatus>({ kind: 'idle' })

  // Aggregate dirty + change-collection state, lifted from each tab
  // via the registry. The shell calls saveSettings once with the
  // combined updates dict.
  const [aggregate, setAggregate] = React.useState<AggregateFormState>({
    dirty: false,
    issues: [],
    hasErrors: false,
    issueKindByTab: {},
    collectUpdates: () => ({}),
    resetAll: () => {},
  })

  // The tab opens on Models (its default), and the Models attention mark
  // + the missing-config banner point there while config is incomplete —
  // but the user can still visit and fill the other tabs in the same
  // session. Save stays gated on the Models essentials being present.

  const handleReload = async () => {
    setReloadStatus('loading')
    try {
      const result = await state.reloadSettings()
      setReloadStatus(result.ok ? 'ok' : 'error')
      await state.readSettings()
      // Reload is an explicit "snap back to disk state" — drop any
      // in-flight edits so dirty + Save & Validate match what's on
      // disk again.
      aggregate.resetAll()
    } catch {
      setReloadStatus('error')
    }
    window.setTimeout(() => setReloadStatus('idle'), 2500)
  }

  const envPath = state.readResult?.env_path ?? ''
  // ``editable`` defaults to true while readResult is unset (initial
  // load): we don't want the form to flash disabled before the first
  // response arrives. The backend reports false only in Docker mode.
  const editable = state.readResult?.editable ?? true
  const editingDisabledReason = state.readResult?.editing_disabled_reason ?? null

  // Update check is a client-side, opt-out preference (the check runs in the
  // browser), so it lives in localStorage — no backend round-trip.
  const { enabled: updateCheckEnabled, setEnabled: setUpdateCheckEnabled } =
    useUpdateCheckEnabled()
  const {
    available: updateAvailable,
    checking: updateChecking,
    checkNow: checkForUpdate,
  } = useUpdateCheck(updateCheckEnabled, __APP_VERSION__)
  const tabNavRef = React.useRef<HTMLElement>(null)
  const { canScrollLeft, canScrollRight } = useScrollEdges(tabNavRef)

  const [copyStatus, setCopyStatus] = React.useState<'idle' | 'copied' | 'error'>('idle')
  const handleCopyPath = async () => {
    if (!envPath) return
    const ok = await copyToClipboard(envPath)
    setCopyStatus(ok ? 'copied' : 'error')
    window.setTimeout(() => setCopyStatus('idle'), 2000)
  }

  const handleSaveAndValidate = async () => {
    const updates = aggregate.collectUpdates()
    if (Object.keys(updates).length === 0) {
      setSaveStatus({ kind: 'idle' })
      return
    }
    setSaveStatus({ kind: 'saving' })
    try {
      const result = await state.saveSettings(updates)
      if (!result.ok) {
        setSaveStatus({ kind: 'error', message: result.error ?? 'Save failed' })
        return
      }
      setSaveStatus({ kind: 'saved' })
      window.setTimeout(
        () =>
          setSaveStatus((cur) => (cur.kind === 'saved' ? { kind: 'idle' } : cur)),
        2500,
      )
    } catch (err) {
      setSaveStatus({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
      })
    }
  }

  const handleRestart = async () => {
    setSaveStatus({ kind: 'saving' })
    try {
      // saveSettings with an empty updates payload + restart: true
      // pipes through the existing handler — write_env_file with an
      // empty dict writes nothing and the restart signal still fires.
      const result = await state.saveSettings({}, { restart: true })
      if (!result.ok) {
        setSaveStatus({ kind: 'error', message: result.error ?? 'Restart failed' })
        return
      }
      setSaveStatus({ kind: 'restarting' })
      markRestarting()
      // Restart initiated from Settings — signal the app, which leaves for the
      // assistant only when Settings is the current view.
      onRestart?.()
    } catch (err) {
      setSaveStatus({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
      })
    }
  }

  // Failed agent rows from the latest validation pass. Includes
  // runtime auth failures (the LLM client flags the validator on
  //401 / 404 mid-session), so the banner stays accurate even after
  // a key is revoked while the agent is running.
  const failedAgents = state.validation.results.filter(
    (r) => r.enabled && r.ok === false,
  )

  // Build the directive stack: tab-published issues (already sorted
  // by kind priority) followed by state-derived items (save/validate
  // status, pending restart). Errors at the top so they're the first
  // thing the user reads.
  const directives = [
    ...aggregate.issues.map((i) => ({ kind: i.kind, text: i.text })),
    ...stateDirectives(saveStatus, aggregate.dirty, state),
  ]

  return (
    <SettingsFormRegistry onAggregateChange={setAggregate}>
     <FieldsDisabledContext.Provider value={!editable}>
      <section className={s.settings} aria-label="Swarpius Settings">
        <header className={s.header}>
          <span className={`panel-heading-group ${s.headerTitle}`}>
            <h3 className={s.title}>Settings</h3>
            <GuidanceButton id="settings" />
          </span>
          <div className={s.headerActions}>
            <button
              type="button"
              className={s.headerButton}
              onClick={gettingStarted.open}
              title="Show the Getting Started guide"
            >
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
                <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
              </svg>
              Getting Started
            </button>
            {envPath ? (
              <button
                type="button"
                className={`${s.headerButton} ${s.copyPathButton}`}
                onClick={handleCopyPath}
                title={`Copy .env path (${envPath})`}
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                </svg>
                {copyStatus === 'copied' ? 'Copied' : copyStatus === 'error' ? 'Copy failed' : 'Copy .env path'}
              </button>
            ) : null}
            <button
              type="button"
              className={s.headerButton}
              onClick={handleReload}
              disabled={reloadStatus === 'loading' || !editable}
              title={
                editable
                  ? 'Re-read the .env file from disk (picks up out-of-band edits)'
                  : 'Reload disabled in Docker — click Restart to pick up host edits'
              }
            >
              <svg
                viewBox="0 0 24 24"
                width="14"
                height="14"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M1 4v6h6M23 20v-6h-6" />
                <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15" />
              </svg>
              {reloadStatus === 'loading'
                ? 'Reloading…'
                : reloadStatus === 'ok'
                ? 'Reloaded'
                : reloadStatus === 'error'
                ? 'Reload failed'
                : 'Reload .env'}
            </button>
          </div>
          {!forceOpen && onClose ? (
            <button
              type="button"
              className={`${s.headerClose} close-button`}
              onClick={onClose}
              aria-label="Close Settings"
              title="Close Settings"
            >
              <CloseIcon />
            </button>
          ) : null}
        </header>

        {!editable && editingDisabledReason ? (
          <div className={s.infoBanner} role="status">
            <svg
              className={s.requiredBannerIcon}
              viewBox="0 0 24 24"
              width="20"
              height="20"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <div className={s.requiredBannerBody}>
              <div className={s.requiredBannerTitle}>
                Settings editing is disabled in Docker mode
              </div>
              <div>{renderInlineCode(editingDisabledReason)}</div>
            </div>
          </div>
        ) : null}

        {!state.configComplete && state.configMissing.length > 0 ? (
          <div className={s.requiredBanner} role="alert">
            <svg
              className={s.requiredBannerIcon}
              viewBox="0 0 24 24"
              width="20"
              height="20"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            <div className={s.requiredBannerBody}>
              <div className={s.requiredBannerTitle}>
                Configuration required before chat will work
              </div>
              <div>Open the Models tab to set:</div>
              <div className={s.requiredBannerFields}>
                {state.configMissing.map((field, i) => (
                  <React.Fragment key={field}>
                    {i > 0 ? ', ' : null}
                    <button
                      type="button"
                      className={s.requiredFieldLink}
                      onClick={() => setActiveTab('models')}
                      title="Open the Models tab"
                    >
                      {formatMissingField(field)}
                    </button>
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>
        ) : null}

        {failedAgents.length > 0 ? (
          <div className={s.requiredBanner} role="alert">
            <svg
              className={s.requiredBannerIcon}
              viewBox="0 0 24 24"
              width="20"
              height="20"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <div className={s.requiredBannerBody}>
              <div className={s.requiredBannerTitle}>
                {failedAgents.length === 1
                  ? 'One agent validation failed'
                  : `${failedAgents.length} agent validations failed`}
              </div>
              <div>
                Open the <strong>Models</strong> tab — keys may have
                expired or been revoked, or the configured model is no
                longer available.
              </div>
              <div className={s.requiredBannerFields}>
                {formatAgentIssues(failedAgents)}
              </div>
            </div>
          </div>
        ) : null}

        <div className={s.body}>
          <div className={s.tabNavWrap}>
            <nav
              ref={tabNavRef}
              className={[s.tabNav, canScrollLeft && s.fadeLeft, canScrollRight && s.fadeRight]
                .filter(Boolean)
                .join(' ')}
              aria-label="Settings sections"
            >
              {TABS.map((tab) => {
                const isActive = tab.id === activeTab
                const mark = tabAttentionMark(
                  tab,
                  state,
                  aggregate.issueKindByTab,
                )
                return (
                  <button
                    key={tab.id}
                    type="button"
                    className={`${s.tabButton} ${
                      isActive ? s.tabButtonActive : ''
                    }`}
                    aria-current={isActive ? 'page' : undefined}
                    onClick={() => setActiveTab(tab.id)}
                  >
                    <span className={s.tabLabel}>{tab.label}</span>
                    {mark ? (
                      <span
                        className={
                          mark.severity === 'error'
                            ? s.tabRequiredMark
                            : s.tabWarningMark
                        }
                        aria-label={mark.label}
                        title={mark.detail}
                      >
                        !
                      </span>
                    ) : null}
                  </button>
                )
              })}
            </nav>
            <div className={s.actionsFooter}>
              {directives.map((d, i) => (
                <div
                  key={`${d.kind}-${i}`}
                  className={`${s.directive} ${
                    d.kind === 'error' ? s.directiveError :
                    d.kind === 'warning' ? s.directiveDirty :
                    d.kind === 'pending' ? s.directivePending :
                    d.kind === 'dirty' ? s.directiveDirty :
                    d.kind === 'validating' ? s.directiveValidating :
                    ''
                  }`}
                  role="status"
                >
                  {d.text}
                </div>
              ))}
              <div className={s.actionButtons}>
                <button
                  type="button"
                  className={`${s.actionButton} ${s.actionPrimary}`}
                  onClick={handleSaveAndValidate}
                  disabled={
                    !aggregate.dirty
                    || aggregate.hasErrors
                    || saveStatus.kind === 'saving'
                    || !editable
                  }
                  title={
                    !editable
                      ? 'Editing disabled in Docker — edit agent/.env on the host'
                      : aggregate.hasErrors
                      ? 'Resolve the issues above before saving'
                      : 'Write changes to .env and re-run live validation'
                  }
                >
                  Save &amp; Validate
                </button>
                <button
                  type="button"
                  className={s.actionButton}
                  onClick={handleRestart}
                  disabled={
                    saveStatus.kind === 'saving'
                    || saveStatus.kind === 'restarting'
                  }
                  title={
                    !editable
                      ? 'Restart the agent to pick up host .env edits'
                      : 'Restart the agent (does not save unsaved changes)'
                  }
                >
                  Restart
                </button>
              </div>
            </div>
          </div>

          <div className={s.content}>
            {state.readResult ? (
              <TabContent tabId={activeTab} state={state} />
            ) : (
              <p className={s.loadingNote}>Loading current settings…</p>
            )}
          </div>
        </div>
        <div className={s.licencesFooter}>
          <div className={s.footerLeft}>
            <span className={s.appVersionRow}>
              <span className={s.appVersion}>Swarpius v{__APP_VERSION__}</span>
              {updateAvailable ? (
                <a
                  className={s.updateAvailable}
                  href={UPDATE_RELEASES_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Update: v{updateAvailable.replace(/^v/, '')}
                </a>
              ) : (
                <button
                  type="button"
                  className={s.updateCheckButton}
                  onClick={checkForUpdate}
                  disabled={updateChecking}
                  title="Click to check for updates"
                >
                  {updateChecking ? 'Checking…' : 'Latest version'}
                </button>
              )}
            </span>
            <label className={s.updateToggle}>
              <input
                type="checkbox"
                checked={updateCheckEnabled}
                onChange={(e) => setUpdateCheckEnabled(e.target.checked)}
              />
              Check for updates automatically
            </label>
          </div>
          <p className={s.footerNotice}>
            <span className={s.copyright}>© 2026 Paraseva Ltd</span>
            <span className={s.disclaimer}> · Swarpius is an independent project, not affiliated with or endorsed by Roon Labs LLC. Roon is a trademark of Roon Labs LLC.</span>
          </p>
          <button
            type="button"
            className={s.headerButton}
            onClick={() => setShowLicences(true)}
          >
            Open-source licences
          </button>
        </div>
      </section>
      {showLicences ? (
        <AttributionsModal onClose={() => setShowLicences(false)} />
      ) : null}
     </FieldsDisabledContext.Provider>
    </SettingsFormRegistry>
  )
}

/**
 * Render a string with markdown-lite inline code spans: text between
 * backticks becomes a ``<code>`` element. The Docker-mode banner uses
 * this so backend-provided reason copy can mark file paths and shell
 * commands as monospace without exposing UI presentation choices in
 * the backend constant.
 *
 * Falsy input renders as empty so the JSX call site doesn't have to
 * guard for the pre-readResult render pass.
 */
function renderInlineCode(text: string | null | undefined): React.ReactNode {
  if (!text) return null
  return text.split('`').map((segment, i) =>
    // Even-indexed segments are plain text; odd-indexed segments were
    // wrapped in backticks in the source string.
    i % 2 === 0 ? segment : <code key={i}>{segment}</code>,
  )
}


/**
 * Copy to the clipboard, falling back to ``document.execCommand``
 * when ``navigator.clipboard`` isn't available — typically a non-
 * secure HTTP context (e.g. LAN access via ``http://192.168.x.x``).
 * Returns true on success.
 */
async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Fall through to the textarea fallback.
    }
  }
  // Legacy fallback for insecure contexts. execCommand is deprecated
  // but still works in every relevant browser and is the only path
  // that works on plain HTTP outside localhost.
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}


interface TabContentProps {
  tabId: SettingsTabId
  state: UseSettingsState
}

// All tabs stay mounted with visibility toggled so each tab's in-flight
// form state survives a tab switch.
const TabContent: React.FC<TabContentProps> = ({ tabId, state }) => (
  <>
    <div hidden={tabId !== 'models'}><ModelsTab state={state} /></div>
    <div hidden={tabId !== 'roon'}><RoonTab state={state} /></div>
    <div hidden={tabId !== 'web-search'}><WebSearchTab state={state} /></div>
    <div hidden={tabId !== 'speech'}><SpeechTab state={state} /></div>
    <div hidden={tabId !== 'analyser'}><AnalyserTab state={state} /></div>
    <div hidden={tabId !== 'persona'}><PersonaTab state={state} /></div>
    <div hidden={tabId !== 'privacy'}><PrivacyTab /></div>
  </>
)

/**
 * Maps a backend probe to the Settings tab that exposes its config.
 * Used to badge the tab (and the Settings nav icon) when reachability
 * fails.
 */
function backendIssueForTab(
  tabId: SettingsTabId,
  backends: BackendReachabilityResult[],
): string | null {
  const TAB_MAP: Record<BackendReachabilityResult['backend'], SettingsTabId> = {
    'web-search': 'web-search',
    'tts': 'speech',
  }
  for (const b of backends) {
    if (!b.ok && TAB_MAP[b.backend] === tabId) {
      return formatBackendIssue(b)
    }
  }
  return null
}


interface TabMark {
  severity: 'error' | 'warning'
  label: string
  detail: string
}

function tabAttentionMark(
  tab: TabSpec,
  state: UseSettingsState,
  issueKindByTab: Record<string, 'error' | 'warning' | 'info'>,
): TabMark | null {
  if (tab.required && !state.configComplete) {
    return {
      severity: 'error',
      label: 'Required action',
      detail: 'Required configuration missing',
    }
  }
  if (tab.id === 'models') {
    const failed = state.validation.results.filter(
      (r) => r.enabled && r.ok === false,
    )
    if (failed.length > 0) {
      return {
        severity: 'error',
        label: 'Agent validation failed',
        detail: formatAgentIssues(failed),
      }
    }
  }
  const tabIssueKind = issueKindByTab[tab.id]
  if (tabIssueKind === 'error') {
    return {
      severity: 'error',
      label: 'Tab has an error',
      detail: 'This tab has an unresolved error',
    }
  }
  const backendDetail = backendIssueForTab(tab.id, state.validation.backends)
  if (backendDetail) {
    return {
      severity: 'warning',
      label: 'Backend issue',
      detail: backendDetail,
    }
  }
  if (tabIssueKind === 'warning') {
    return {
      severity: 'warning',
      label: 'Tab has a warning',
      detail: 'This tab has an unresolved warning',
    }
  }
  return null
}

export default Settings
