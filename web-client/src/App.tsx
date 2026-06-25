import React from 'react'
import { WebSocketProvider } from './WebSocketProvider'
import { RequestFocusProvider } from './RequestFocusProvider'
import { ChatPanel } from './components/ChatPanel'
import { ErrorBoundary } from './components/ErrorBoundary'
import { HistoryWindow } from './components/HistoryWindow'
import { TtsToggle } from './components/TtsToggle'
import { ZoneStatusPanel } from './components/ZoneStatusPanel'
import { LlmDiagnosticsPanel } from './components/LlmDiagnosticsPanel'
import { TokenUsagePanel } from './components/TokenUsagePanel'
import { PromptBudgetPanel } from './components/PromptBudgetPanel'
import { RequestSummaryPanel } from './components/RequestSummaryPanel'
import { SessionSummaryBar } from './components/SessionSummaryBar'
const AnalysisBrowser = React.lazy(() =>
  import('./components/AnalysisBrowser').then(m => ({ default: m.AnalysisBrowser })),
)
const RoonExplorer = React.lazy(() =>
  import('./components/RoonExplorer').then(m => ({ default: m.RoonExplorer })),
)
const CostDashboard = React.lazy(() =>
  import('./components/CostDashboard').then(m => ({ default: m.CostDashboard })),
)
const Settings = React.lazy(() =>
  import('./components/Settings/Settings').then(m => ({ default: m.Settings })),
)
const RoonSetup = React.lazy(() =>
  import('./components/RoonSetup/RoonSetup').then(m => ({ default: m.RoonSetup })),
)
import { AgentUnreachableModal, ConnectingSplash, RoonCoreLostModal } from './components/ConnectionStatusModal'
import { RestartModal } from './components/RestartModal'
import { DefaultZoneBadge, type DefaultZoneInfo } from './components/DefaultZoneBadge'
import { SessionTakeoverOverlay } from './components/SessionTakeoverOverlay'
import { useWebSocket } from './websocketContext'
import { playServerTts, TTS_RECOVERED_EVENT_NAME, type TtsStatusPhase } from './tts'
import { APP_WS_URL, deriveTtsWebSocketUrl } from './config'
import { parseJson } from './utils/parseJson'
import { type TtsIndicatorPhase } from './components/TtsStatusIndicator'

type TtsHealth = 'checking' | 'healthy' | 'failing'
import { useDiagnostics } from './hooks/useDiagnostics'
import { useDevMode } from './hooks/useDevMode'
import { useSettingsState } from './hooks/useSettingsState'
import { useRoonCommands } from './hooks/useRoonCommands'
import { useClientTtsHealthOverride } from './hooks/useClientTtsHealthOverride'
import { GuidanceProvider } from './components/GuidanceProvider'
import { GuidanceButton } from './components/GuidanceButton'
import { GettingStartedModal } from './components/GettingStartedModal'
import { GettingStartedContext } from './components/gettingStartedContext'
import { shouldAutoShowWelcome } from './gettingStarted'
import { SwarpiusLogo } from './components/SwarpiusLogo'
import { CloseIcon } from './components/CloseIcon'
import { type AppView, viewAfterRestart } from './appView'
import { hasSoftSettingsWarning } from './settingsWarning'
import { wasBundleMode } from './bundleMode'
import { resolveAppSurface } from './appSurface'
import { useConnectionFailureCount } from './hooks/useConnectionFailureCount'
import s from './App.module.css'

type DiagnosticPanelKey = 'agents' | 'tools' | 'errors' | 'requests' | 'llm' | 'prompt' | 'tokens'
type MobileTab = 'now-playing' | 'chat'

/* Below this viewport width we switch to the tab-navigation layout
   (Chat / Now Playing tabs instead of side-by-side panels). Set to
   960px because at any split-mode viewport below ~900px the status
   column becomes too narrow to hold the zone card's 5-button action
   row (255px min-content) even in the 1-col card layout, and the
   card overflows `overflow:hidden` on the status column. Tabbed mode
   gives the status panel the full viewport width, so the card fits
   comfortably. Paired with the CSS breakpoint in several module
   files — keep this constant and the `max-width: 959px` media
   queries in sync. */
const MOBILE_BREAKPOINT = 960

function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState(
    () => typeof window !== 'undefined' && window.innerWidth < MOBILE_BREAKPOINT,
  )
  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches)
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [])
  return isMobile
}

const AppShell: React.FC = () => {
  const { messages, trimmedCount, status, isRestarting, sendMessage } = useWebSocket()
  const [isAutoTtsEnabled, setIsAutoTtsEnabled] = React.useState<boolean>(() => {
    try {
      const stored = localStorage.getItem('swarpius:autoTts')
      return stored === null ? true : stored === 'true'
    } catch {
      return true
    }
  })
  React.useEffect(() => {
    try {
      localStorage.setItem('swarpius:autoTts', String(isAutoTtsEnabled))
    } catch { /* ignore */ }
  }, [isAutoTtsEnabled])
  const [openPanels, setOpenPanels] = React.useState<Set<DiagnosticPanelKey>>(() => new Set(['agents', 'llm']))
  const [appView, setAppView] = React.useState<AppView>(() => {
    try {
      const stored = localStorage.getItem('swarpius:appView')
      if (stored === 'assistant' || stored === 'analysis' || stored === 'settings' || stored === 'roon-explorer' || stored === 'cost') {
        return stored
      }
    } catch { /* ignore */ }
    return 'assistant'
  })
  // Persist voluntary navigation only. 'roon-setup' is always set by
  // forced-routing — restoring it on the next mount would leave the
  // user stuck there if Roon is already paired (the auto-eject is
  // gated on a ref that doesn't survive remount).
  React.useEffect(() => {
    if (appView === 'roon-setup') return
    try {
      localStorage.setItem('swarpius:appView', appView)
    } catch { /* ignore */ }
  }, [appView])
  const [defaultZone, setDefaultZone] = React.useState<DefaultZoneInfo | null>(null)
  const [theme, setTheme] = React.useState<'dark' | 'light'>(() => {
    try {
      return (localStorage.getItem('swarpius:theme') as 'dark' | 'light') || 'dark'
    } catch {
      return 'dark'
    }
  })
  const {
    isDiagnosticsOpen,
    setIsDiagnosticsOpen,
    unreadCounts,
    totalUnread,
    latestUsage,
    toggleDiagnostics,
  } = useDiagnostics(messages)
  const { isDevMode, toggleDevMode } = useDevMode()
  const isMobile = useIsMobile()
  // Dev-mode UI is desktop-only: suppress it on mobile without disturbing the
  // persisted preference, so it returns on a wide screen. Drops the logo
  // highlight and closes the diagnostics/analysis surfaces in tabbed mode.
  const devModeActive = isDevMode && !isMobile
  const [mobileTab, setMobileTab] = React.useState<MobileTab>('now-playing')
  const settingsState = useSettingsState()
  const isTtsConfigured = settingsState.ttsConfigured
  const ttsWsUrl = React.useMemo(
    () => (isTtsConfigured ? deriveTtsWebSocketUrl(APP_WS_URL) : ''),
    [isTtsConfigured],
  )

  // First-run Getting Started intro. Auto-opens once on a pristine
  // install (latched so dismissing it while still unconfigured doesn't
  // re-open it); the Settings header button reopens it on demand.
  const [welcomeOpen, setWelcomeOpen] = React.useState(false)
  const welcomeShownRef = React.useRef(false)
  React.useEffect(() => {
    if (shouldAutoShowWelcome({
      configPristine: settingsState.configPristine,
      awaitingFirstUpdate: settingsState.awaitingFirstUpdate,
      alreadyShown: welcomeShownRef.current,
    })) {
      welcomeShownRef.current = true
      setWelcomeOpen(true)
    }
  }, [settingsState.configPristine, settingsState.awaitingFirstUpdate])
  const openGettingStarted = React.useCallback(() => setWelcomeOpen(true), [])
  const gettingStartedControls = React.useMemo(
    () => ({ open: openGettingStarted }),
    [openGettingStarted],
  )
  const roonCommands = useRoonCommands(sendMessage)

  // Latest Roon Core health from the `roon-core-status` channel. The
  // agent re-sends the current state on every connect, so this re-derives
  // correctly after a refresh / reconnect.
  const roonCoreLost = React.useMemo(() => {
    let lost = false
    for (const m of messages) {
      if (m.channel !== 'roon-core-status' || m.direction !== 'inbound') continue
      const payload = parseJson<{ state?: string }>(m.payload ?? m.body)
      if (payload?.state === 'lost') lost = true
      else if (payload?.state === 'connected') lost = false
    }
    return lost
  }, [messages])

  // Force the Settings view when either:
  //  - required config is missing, OR
  //  - the runtime is in awaiting_config (config_complete may have
  //    just flipped true via Save, but no restart happened so the
  //    runtime hasn't actually initialised yet — Restart is
  //    the only way forward).
  const requiresSettings =
    !settingsState.awaitingFirstUpdate &&
    (!settingsState.configComplete ||
      settingsState.roonState === 'awaiting_config')

  // If Roon isn't paired yet (initialising or failed), force the
  // RoonSetup view. Takes precedence over Chat / Analysis but NOT
  // over a forced Settings route.
  const requiresRoonSetup =
    !settingsState.awaitingFirstUpdate &&
    settingsState.roonState !== 'paired' &&
    settingsState.roonState !== 'awaiting_config'

  // Soft (amber) signal for the Settings nav icon: a configured backend
  // (web search / TTS) is unreachable, or an enabled optional agent
  // (arbiter / diagnostic / analyser) failed validation. Distinct from
  // the hard red "required config missing / coordinator invalid" route
  // (requiresSettings): informational, not blocking.
  const hasSoftWarning = hasSoftSettingsWarning(settingsState.validation)

  // The single blocking-surface decision (overlay or forced view), the
  // source of truth for what's on screen. Derived during render, not via
  // effect+setState, so the user's own appView is preserved and restored
  // once the forcing condition clears. Full precedence: see resolveAppSurface.
  const failedConnectionCycles = useConnectionFailureCount(status)
  const surface = resolveAppSurface({
    status,
    isRestarting,
    failedConnectionCycles,
    awaitingFirstUpdate: settingsState.awaitingFirstUpdate,
    requiresSettings,
    requiresRoonSetup,
    roonState: settingsState.roonState,
    roonCoreLost,
    appView,
    isDevMode: devModeActive,
    roonExplorerEnabled: settingsState.roonExplorerEnabled,
    showWelcome: welcomeOpen,
  })
  // Overlay surfaces cover the screen, so the view behind one is moot —
  // fall back to the assistant view (it stays mounted; display toggles).
  const effectiveAppView: AppView = surface.kind === 'view' ? surface.view : 'assistant'
  const effectiveDiagnosticsOpen = devModeActive && isDiagnosticsOpen

  const toggleAccordionPanel = (panel: DiagnosticPanelKey) => {
    setOpenPanels((prev) => {
      const next = new Set(prev)
      if (next.has(panel)) {
        next.delete(panel)
      } else {
        next.add(panel)
      }
      return next
    })
  }

  /** Open just this panel, collapse all others. Idempotent — clicking
   *  solo on the sole open panel leaves it open (no surprising close).
   *  Shortcut for the common "I want full height for this one" flow
   *  where the user would otherwise have to click through each other
   *  header to collapse it first. */
  const soloAccordionPanel = (panel: DiagnosticPanelKey) => {
    setOpenPanels(new Set([panel]))
  }

  const toggleTheme = () => {
    setTheme((prev) => {
      const next = prev === 'dark' ? 'light' : 'dark'
      try {
        localStorage.setItem('swarpius:theme', next)
      } catch { /* ignore */ }
      return next
    })
  }

  React.useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  const [testTtsPhase, setTestTtsPhase] = React.useState<TtsIndicatorPhase | null>(null)

  // The agent's periodic TCP probe transmits ttsAvailable via
  // feature-availability and remains the authoritative signal; the
  // client latch only bridges the gap until the next probe lands.
  // Unconfigured maps to 'healthy' because the toggle's "Not
  // Configured" chip covers that branch.
  const clientTtsFailing = useClientTtsHealthOverride()
  const ttsHealth: TtsHealth = !isTtsConfigured
    ? 'healthy'
    : settingsState.awaitingFirstUpdate
      ? 'checking'
      : settingsState.ttsAvailable && !clientTtsFailing
        ? 'healthy'
        : 'failing'

  // On every probe that flips ttsAvailable false→true, dispatch the
  // recovered event so a stale click-failure latch defers to the
  // probe. Transitions to false need no action — the visual is
  // already 'failing' from ttsAvailable.
  const lastProbeRef = React.useRef<boolean | null>(null)
  React.useEffect(() => {
    if (settingsState.awaitingFirstUpdate) return
    const previous = lastProbeRef.current
    lastProbeRef.current = settingsState.ttsAvailable
    if (previous === false && settingsState.ttsAvailable) {
      window.dispatchEvent(new CustomEvent(TTS_RECOVERED_EVENT_NAME))
    }
  }, [settingsState.awaitingFirstUpdate, settingsState.ttsAvailable])

  const speakBrand = () => {
    if (!isTtsConfigured) return
    const handleStatus = (phase: TtsStatusPhase) => {
      if (phase === 'sending' || phase === 'playing') {
        setTestTtsPhase(phase)
      } else {
        setTestTtsPhase(null)
      }
    }
    playServerTts(
      'This is the Swarpius web client.',
      ttsWsUrl,
      handleStatus,
    ).catch((error) => {
      console.error('playServerTts error:', error)
      setTestTtsPhase(null)
    })
  }
  const defaultZoneProcessedRef = React.useRef(0)
  React.useEffect(() => {
    const relativeIdx = Math.max(0, defaultZoneProcessedRef.current - trimmedCount)
    const nextMessages = messages.slice(relativeIdx)
    defaultZoneProcessedRef.current = messages.length + trimmedCount
    for (const msg of nextMessages) {
      if (msg.direction !== 'inbound' || msg.channel !== 'default-zone-update') continue
      try {
        const payload = typeof msg.payload === 'object' && msg.payload !== null
          ? msg.payload as DefaultZoneInfo
          : JSON.parse(msg.body) as DefaultZoneInfo
        setDefaultZone(payload)
      } catch { /* ignore malformed */ }
    }
  }, [messages, trimmedCount])

  const closeDiagnosticsDrawer = () => {
    setIsDiagnosticsOpen(false)
  }

  // Keyboard shortcuts
  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // Ctrl+Shift+D: toggle diagnostics drawer (dev mode only)
      if (isDevMode && (event.ctrlKey || event.metaKey) && event.shiftKey && event.key === 'D') {
        event.preventDefault()
        toggleDiagnostics()
        if (!isDiagnosticsOpen) setAppView('assistant')
        return
      }
      // Ctrl+Shift+A: toggle analysis view (dev mode only)
      if (isDevMode && (event.ctrlKey || event.metaKey) && event.shiftKey && event.key === 'A') {
        event.preventDefault()
        const next = appView === 'analysis' ? 'assistant' : 'analysis'
        setAppView(next)
        if (next === 'analysis') setIsDiagnosticsOpen(false)
        return
      }
      if (event.key === 'Escape' && isDiagnosticsOpen) {
        setIsDiagnosticsOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isDevMode, isDiagnosticsOpen, appView, toggleDiagnostics, setIsDiagnosticsOpen, setAppView])

  // Track header height so the diagnostics drawer can position below it
  const headerRef = React.useRef<HTMLElement>(null)
  React.useEffect(() => {
    const el = headerRef.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      document.documentElement.style.setProperty('--header-height', `${el.offsetHeight}px`)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return (
    <GettingStartedContext value={gettingStartedControls}>
    <div className="app">
      {/* session-takeover and restarting overlays live in the outer App
          (they must survive this shell's reconnect remount); the resolver
          gives them precedence, so these never stack underneath them. */}
      {surface.kind === 'overlay' && surface.overlay === 'connecting' && <ConnectingSplash />}
      {surface.kind === 'overlay' && surface.overlay === 'agent-unreachable' && (
        <AgentUnreachableModal isBundle={settingsState.isBundle || wasBundleMode()} />
      )}
      {surface.kind === 'overlay' && surface.overlay === 'getting-started' && (
        <GettingStartedModal
          onClose={() => setWelcomeOpen(false)}
          isBundle={settingsState.isBundle}
          onOpenStopMarkerFolder={roonCommands.openStopMarkerFolder}
        />
      )}
      {surface.kind === 'overlay' && surface.overlay === 'roon-core-lost' && <RoonCoreLostModal />}
      <header className="app-header" ref={headerRef}>
        <div className="app-header-left">
          <button
            id="audio-button"
            type="button"
            className={`${s.appBrandButton} ${devModeActive ? s.appBrandButtonDev : ''} ${isMobile ? s.appBrandButtonDisabled : ''}`}
            onDoubleClick={isMobile ? undefined : toggleDevMode}
            disabled={isMobile}
            aria-label="Swarpius"
          >
            <SwarpiusLogo aria-label="Swarpius" role="img" className={s.appBrandLogo} />
          </button>
        </div>

        <div className="app-header-centre">
          <DefaultZoneBadge zone={defaultZone} />
        </div>

        <div className="app-header-right">
          <TtsToggle
            enabled={isAutoTtsEnabled}
            onChange={setIsAutoTtsEnabled}
            disabled={!isTtsConfigured}
            notConfigured={!isTtsConfigured && !settingsState.awaitingFirstUpdate}
            onTestTts={speakBrand}
            testTtsPhase={testTtsPhase}
            health={ttsHealth}
          />
          {devModeActive && (
            <button
              type="button"
              className={s.headerIconButton}
              disabled={requiresSettings}
              onClick={() => {
                toggleDiagnostics()
                if (!effectiveDiagnosticsOpen) setAppView('assistant')
              }}
              aria-expanded={effectiveDiagnosticsOpen}
              aria-controls="diagnostics-drawer"
              title={effectiveDiagnosticsOpen ? 'Hide Live Diagnostics (Ctrl+Shift+D)' : 'Show Live Diagnostics (Ctrl+Shift+D)'}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
              </svg>
              {totalUnread > 0 ? <span className={s.headerIconBadge}>{totalUnread}</span> : null}
              {unreadCounts.errors > 0 ? <span className={`${s.headerIconBadge} ${s.headerIconBadgeError}`}>{unreadCounts.errors}</span> : null}
            </button>
          )}
          {devModeActive && (
            <button
              type="button"
              className={`${s.headerIconButton} ${effectiveAppView === 'analysis' ? s.headerIconButtonActive : ''}`}
              disabled={requiresSettings}
              onClick={() => {
                const next = effectiveAppView === 'analysis' ? 'assistant' : 'analysis'
                setAppView(next)
                if (next === 'analysis') setIsDiagnosticsOpen(false)
              }}
              aria-pressed={effectiveAppView === 'analysis'}
              title={effectiveAppView === 'analysis' ? 'Back to Assistant (Ctrl+Shift+A)' : 'Open Conversation Analysis (Ctrl+Shift+A)'}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 3v18h18" />
                <path d="M7 16l4-8 4 4 4-8" />
              </svg>
            </button>
          )}
          {settingsState.roonExplorerEnabled && !isMobile && (
            <button
              type="button"
              className={`${s.headerIconButton} ${effectiveAppView === 'roon-explorer' ? s.headerIconButtonActive : ''}`}
              disabled={requiresSettings}
              onClick={() => {
                const next = effectiveAppView === 'roon-explorer' ? 'assistant' : 'roon-explorer'
                setAppView(next)
                if (next === 'roon-explorer') setIsDiagnosticsOpen(false)
              }}
              aria-pressed={effectiveAppView === 'roon-explorer'}
              title={effectiveAppView === 'roon-explorer' ? 'Close Roon Explorer' : 'Open Roon Explorer'}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.5-3.5" />
              </svg>
            </button>
          )}
          <button
            type="button"
            className={`${s.headerIconButton} ${effectiveAppView === 'cost' ? s.headerIconButtonActive : ''}`}
            disabled={requiresSettings}
            onClick={() => {
              const next = effectiveAppView === 'cost' ? 'assistant' : 'cost'
              setAppView(next)
              if (next === 'cost') setIsDiagnosticsOpen(false)
            }}
            aria-pressed={effectiveAppView === 'cost'}
            title={effectiveAppView === 'cost' ? 'Close Costs' : 'Open Costs'}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="1" x2="12" y2="23" />
              <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
            </svg>
          </button>
          <button
            type="button"
            className={`${s.headerIconButton} ${effectiveAppView === 'settings' ? s.headerIconButtonActive : ''}`}
            disabled={requiresSettings}
            onClick={() => {
              const next = effectiveAppView === 'settings' ? 'assistant' : 'settings'
              setAppView(next)
              if (next === 'settings') setIsDiagnosticsOpen(false)
            }}
            aria-pressed={effectiveAppView === 'settings'}
            title={effectiveAppView === 'settings' ? 'Close Settings' : 'Open Settings'}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            {requiresSettings ? (
              <span className={`${s.headerIconBadge} ${s.headerIconBadgeError}`}>!</span>
            ) : hasSoftWarning ? (
              <span className={`${s.headerIconBadge} ${s.headerIconBadgeWarning}`} title="Some settings need attention">!</span>
            ) : null}
          </button>
          <button
            type="button"
            className={s.headerIconButton}
            onClick={toggleTheme}
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'dark' ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5" />
                <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
            )}
          </button>
        </div>
      </header>

      <div className="app-content">
        <main className="app-main" style={{ display: effectiveAppView === 'assistant' ? undefined : 'none' }}>
          <section className="chat-column" style={isMobile && mobileTab !== 'chat' ? { display: 'none' } : undefined}>
            <ErrorBoundary name="Chat">
              <ChatPanel
                isAutoTtsEnabled={isAutoTtsEnabled}
                isDevMode={devModeActive}
                isMobile={isMobile}
                ttsHealth={ttsHealth}
                ttsWsUrl={ttsWsUrl}
              />
            </ErrorBoundary>
          </section>

          <section className="status-column" style={isMobile && mobileTab !== 'now-playing' ? { display: 'none' } : undefined}>
            <ErrorBoundary name="Zone Status">
              <ZoneStatusPanel defaultZoneName={defaultZone?.zone_name ?? null} defaultZoneAlias={defaultZone?.alias ?? null} defaultZoneGroupName={defaultZone?.group_name ?? null} defaultZoneIsGrouped={defaultZone?.is_grouped ?? false} />
            </ErrorBoundary>
          </section>
        </main>
        {devModeActive && (
          <main className="app-main app-main-analysis" style={{ display: effectiveAppView === 'analysis' ? undefined : 'none' }}>
            <ErrorBoundary name="Conversation Analysis">
              <React.Suspense fallback={<div className={s.lazyLoadFallback}>Loading analysis...</div>}>
                <AnalysisBrowser onClose={() => setAppView('assistant')} />
              </React.Suspense>
            </ErrorBoundary>
          </main>
        )}
        {settingsState.roonExplorerEnabled && !isMobile && (
          <main className="app-main app-main-analysis" style={{ display: effectiveAppView === 'roon-explorer' ? undefined : 'none' }}>
            <ErrorBoundary name="Roon Explorer">
              <React.Suspense fallback={<div className={s.lazyLoadFallback}>Loading Roon Explorer…</div>}>
                <RoonExplorer onClose={() => setAppView('assistant')} />
              </React.Suspense>
            </ErrorBoundary>
          </main>
        )}
        <main className="app-main app-main-analysis" style={{ display: effectiveAppView === 'cost' ? undefined : 'none' }}>
          <ErrorBoundary name="Cost Dashboard">
            <React.Suspense fallback={<div className={s.lazyLoadFallback}>Loading cost dashboard…</div>}>
              <CostDashboard onClose={() => setAppView('assistant')} />
            </React.Suspense>
          </ErrorBoundary>
        </main>
        <main className="app-main app-main-analysis" style={{ display: effectiveAppView === 'settings' ? undefined : 'none' }}>
          <ErrorBoundary name="Settings">
            <React.Suspense fallback={<div className={s.lazyLoadFallback}>Loading settings…</div>}>
              <Settings
                state={settingsState}
                forceOpen={requiresSettings}
                onClose={() => setAppView('assistant')}
                onRestart={() => setAppView(viewAfterRestart)}
              />
            </React.Suspense>
          </ErrorBoundary>
        </main>
        <main className="app-main app-main-analysis" style={{ display: effectiveAppView === 'roon-setup' ? undefined : 'none' }}>
          <ErrorBoundary name="RoonSetup">
            <React.Suspense fallback={<div className={s.lazyLoadFallback}>Loading…</div>}>
              <RoonSetup
                state={settingsState}
                onOpenSettings={() => setAppView('settings')}
              />
            </React.Suspense>
          </ErrorBoundary>
        </main>
      </div>

      {isMobile && (
        <nav className={s.mobileTabBar} aria-label="Mobile navigation">
          <button
            type="button"
            className={`${s.mobileTab} ${mobileTab === 'now-playing' ? s.mobileTabActive : ''}`}
            onClick={() => setMobileTab('now-playing')}
            aria-pressed={mobileTab === 'now-playing'}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="20" height="20">
              <circle cx="12" cy="12" r="10" />
              <polygon points="10,8 16,12 10,16" />
            </svg>
            <span>Now Playing</span>
          </button>
          <button
            type="button"
            className={`${s.mobileTab} ${mobileTab === 'chat' ? s.mobileTabActive : ''}`}
            onClick={() => setMobileTab('chat')}
            aria-pressed={mobileTab === 'chat'}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="20" height="20">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <span>Chat</span>
          </button>
        </nav>
      )}

      {devModeActive && <aside
        id="diagnostics-drawer"
        className={`${s.diagnosticsDrawer} ${effectiveDiagnosticsOpen ? s.diagnosticsDrawerOpen : ''}`}
        inert={!effectiveDiagnosticsOpen}
      >
        <div className={s.diagnosticsHeader}>
          <span className="panel-heading-group">
            <div className={s.diagnosticsTitle}>Live Diagnostics</div>
            <GuidanceButton id="live-diagnostics" isDevMode />
          </span>
          <SessionSummaryBar messages={messages} />
          <button type="button" className="close-button" onClick={closeDiagnosticsDrawer} aria-label="Close Live Diagnostics">
            <CloseIcon />
          </button>
        </div>

        <ErrorBoundary name="Live Diagnostics">
          <div className={s.accordionContainer} role="tablist" aria-label="Live Diagnostics panels">
            {([
                { key: 'agents' as const, label: 'Agents', content: <HistoryWindow title="Agents" channel="agent-outputs" syncKey="agent-outputs" /> },
                { key: 'tools' as const, label: 'Tools', content: <HistoryWindow title="Tools" channel="tool-outputs" syncKey="tool-outputs" /> },
                { key: 'errors' as const, label: 'Errors', content: <HistoryWindow title="Errors" channel="errors" syncKey="errors" /> },
                { key: 'requests' as const, label: 'Session Requests', content: <RequestSummaryPanel /> },
                { key: 'llm' as const, label: 'LLM Diagnostics', content: <LlmDiagnosticsPanel /> },
                { key: 'prompt' as const, label: 'Prompt Budget', content: <PromptBudgetPanel /> },
                { key: 'tokens' as const, label: 'Token Usage', content: <TokenUsagePanel latestUsage={latestUsage} /> },
              ]).map(({ key, label, content }) => (
                <div key={key} className={`${s.accordionSection} ${openPanels.has(key) ? s.accordionSectionOpen : s.accordionSectionClosed}`}>
                  <div className={s.accordionHeader}>
                    <button
                      type="button"
                      className={s.accordionTrigger}
                      onClick={() => toggleAccordionPanel(key)}
                      aria-expanded={openPanels.has(key)}
                      onKeyDown={(e) => {
                        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End') return
                        e.preventDefault()
                        const triggers = (e.currentTarget.closest(`.${s.accordionContainer}`) as HTMLElement)?.querySelectorAll<HTMLButtonElement>(`.${s.accordionTrigger}`)
                        if (!triggers?.length) return
                        const idx = Array.from(triggers).indexOf(e.currentTarget)
                        let next: number
                        if (e.key === 'ArrowDown') next = idx < triggers.length - 1 ? idx + 1 : 0
                        else if (e.key === 'ArrowUp') next = idx > 0 ? idx - 1 : triggers.length - 1
                        else if (e.key === 'Home') next = 0
                        else next = triggers.length - 1
                        triggers[next].focus()
                      }}
                    >
                      <svg className={s.accordionChevron} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="9 18 15 12 9 6" />
                      </svg>
                      <span>{label}</span>
                      {key === 'errors' && unreadCounts.errors > 0 ? (
                        <span className={`${s.accordionBadge} ${s.accordionBadgeError}`}>{unreadCounts.errors}</span>
                      ) : null}
                    </button>
                    <button
                      type="button"
                      className={s.accordionSolo}
                      onClick={() => soloAccordionPanel(key)}
                      title={`Show only ${label}`}
                      aria-label={`Show only ${label} (collapse other panels)`}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="4 14 4 20 10 20" />
                        <polyline points="20 10 20 4 14 4" />
                        <line x1="4" y1="20" x2="11" y2="13" />
                        <line x1="20" y1="4" x2="13" y2="11" />
                      </svg>
                    </button>
                  </div>
                  <div className={s.accordionBody}>
                    {content}
                  </div>
                </div>
              ))}
            </div>
          </ErrorBoundary>
      </aside>}
    </div>
    </GettingStartedContext>
  )
}

const RemountingShell: React.FC = () => {
  const { connectionGeneration } = useWebSocket()
  return <AppShell key={connectionGeneration} />
}

const App: React.FC = () => (
  <GuidanceProvider>
    <WebSocketProvider>
      <RequestFocusProvider>
        <RemountingShell />
        <SessionTakeoverOverlay />
        <RestartModal />
      </RequestFocusProvider>
    </WebSocketProvider>
  </GuidanceProvider>
)

export default App
