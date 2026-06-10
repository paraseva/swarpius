import type { AppView } from './appView'
import type { ConnectionStatus } from './websocketContext'
import type { RoonState } from './hooks/useSettingsState'

export type OverlayKind =
  | 'session-takeover'
  | 'restarting'
  | 'agent-unreachable'
  | 'connecting'
  | 'getting-started'
  | 'roon-core-lost'

export type AppSurface =
  | { kind: 'overlay'; overlay: OverlayKind }
  | { kind: 'view'; view: AppView }

export interface AppSurfaceInputs {
  status: ConnectionStatus
  isRestarting: boolean
  /** Consecutive WS failure cycles (transitions into a failure state
   *  without an intervening 'open'). Two is the threshold where a blip
   *  becomes a real problem worth surfacing. */
  failedConnectionCycles: number
  /** No feature-availability message has arrived yet on this connection. */
  awaitingFirstUpdate: boolean
  requiresSettings: boolean
  requiresRoonSetup: boolean
  roonState: RoonState
  roonCoreLost: boolean
  appView: AppView
  isDevMode: boolean
  roonExplorerEnabled: boolean
  /** The first-run Getting Started intro is requested (auto-opened on a
   *  pristine install, or reopened from the Settings header). Sits below
   *  the connection/session overlays but above the forced setup views. */
  showWelcome: boolean
}

const FAILED_CYCLES_THRESHOLD = 2

/**
 * Single source of truth for what the app shows: a blocking overlay, a
 * forced setup view, or the user's own view when nothing blocks. Returns
 * exactly one outcome by strict precedence, so the overlay and view
 * components stay presentational and need no cross-flags of their own.
 *
 * The one non-obvious rule: an explicit `appView === 'settings'` escapes
 * the forced Roon-setup view, so RoonSetup's "Open Settings" button can
 * reach Settings instead of being snapped straight back.
 */
export function resolveAppSurface(i: AppSurfaceInputs): AppSurface {
  if (i.status === 'taken_over') return { kind: 'overlay', overlay: 'session-takeover' }
  if (i.isRestarting) return { kind: 'overlay', overlay: 'restarting' }
  if (i.failedConnectionCycles >= FAILED_CYCLES_THRESHOLD && i.status !== 'open') {
    return { kind: 'overlay', overlay: 'agent-unreachable' }
  }
  if (i.status === 'open' && i.awaitingFirstUpdate) {
    return { kind: 'overlay', overlay: 'connecting' }
  }
  if (i.showWelcome) return { kind: 'overlay', overlay: 'getting-started' }
  if (i.requiresSettings) return { kind: 'view', view: 'settings' }
  if (i.requiresRoonSetup && i.appView !== 'settings') return { kind: 'view', view: 'roon-setup' }
  if (i.roonCoreLost && i.status === 'open') return { kind: 'overlay', overlay: 'roon-core-lost' }
  return { kind: 'view', view: downgradeView(i) }
}

function downgradeView(i: AppSurfaceInputs): AppView {
  if (!i.isDevMode && i.appView === 'analysis') return 'assistant'
  if (!i.roonExplorerEnabled && i.appView === 'roon-explorer') return 'assistant'
  return i.appView
}
