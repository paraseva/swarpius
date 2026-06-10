import { describe, expect, it } from 'vitest'
import { resolveAppSurface, type AppSurfaceInputs } from './appSurface'

// A baseline where nothing is wrong: connected, first update received,
// config complete, Roon paired, on the assistant view. Each test flips
// only the fields under test so precedence is unambiguous.
const base: AppSurfaceInputs = {
  status: 'open',
  isRestarting: false,
  failedConnectionCycles: 0,
  awaitingFirstUpdate: false,
  requiresSettings: false,
  requiresRoonSetup: false,
  roonState: 'paired',
  roonCoreLost: false,
  appView: 'assistant',
  isDevMode: true,
  roonExplorerEnabled: true,
  showWelcome: false,
}

const overlay = (o: string) => ({ kind: 'overlay', overlay: o })
const view = (v: string) => ({ kind: 'view', view: v })

describe('resolveAppSurface precedence', () => {
  it('shows the session-takeover overlay above everything', () => {
    expect(
      resolveAppSurface({
        ...base,
        status: 'taken_over',
        isRestarting: true,
        requiresSettings: true,
        failedConnectionCycles: 5,
      }),
    ).toEqual(overlay('session-takeover'))
  })

  it('shows the restart overlay above connection failures and forced views', () => {
    expect(
      resolveAppSurface({
        ...base,
        isRestarting: true,
        status: 'closed',
        failedConnectionCycles: 5,
        requiresSettings: true,
      }),
    ).toEqual(overlay('restarting'))
  })

  it('shows agent-unreachable after two failed cycles while not open', () => {
    expect(
      resolveAppSurface({ ...base, status: 'closed', failedConnectionCycles: 2 }),
    ).toEqual(overlay('agent-unreachable'))
  })

  it('does not show agent-unreachable on a single failed cycle', () => {
    expect(
      resolveAppSurface({ ...base, status: 'closed', failedConnectionCycles: 1 }),
    ).toEqual(view('assistant'))
  })

  it('does not show agent-unreachable once the connection is open again', () => {
    expect(
      resolveAppSurface({ ...base, status: 'open', failedConnectionCycles: 5 }),
    ).toEqual(view('assistant'))
  })

  it('shows the connecting splash when open but no first update has arrived', () => {
    expect(
      resolveAppSurface({ ...base, awaitingFirstUpdate: true }),
    ).toEqual(overlay('connecting'))
  })

  it('shows the getting-started intro over the forced settings view when requested', () => {
    expect(
      resolveAppSurface({ ...base, showWelcome: true, requiresSettings: true }),
    ).toEqual(overlay('getting-started'))
  })

  it('shows the getting-started intro over a normal view when requested', () => {
    expect(
      resolveAppSurface({ ...base, showWelcome: true }),
    ).toEqual(overlay('getting-started'))
  })

  it('lets the connection/session overlays win over the getting-started intro', () => {
    expect(
      resolveAppSurface({ ...base, showWelcome: true, status: 'taken_over' }),
    ).toEqual(overlay('session-takeover'))
    expect(
      resolveAppSurface({ ...base, showWelcome: true, isRestarting: true }),
    ).toEqual(overlay('restarting'))
    expect(
      resolveAppSurface({
        ...base, showWelcome: true, status: 'closed', failedConnectionCycles: 2,
      }),
    ).toEqual(overlay('agent-unreachable'))
    expect(
      resolveAppSurface({ ...base, showWelcome: true, awaitingFirstUpdate: true }),
    ).toEqual(overlay('connecting'))
  })

  it('forces the settings view when required, above roon setup and core-lost', () => {
    expect(
      resolveAppSurface({
        ...base,
        requiresSettings: true,
        requiresRoonSetup: true,
        roonCoreLost: true,
      }),
    ).toEqual(view('settings'))
  })

  it('forces the roon-setup view when Roon needs setup', () => {
    expect(
      resolveAppSurface({ ...base, requiresRoonSetup: true }),
    ).toEqual(view('roon-setup'))
  })

  it('lets an explicit Settings choice escape the forced roon-setup view', () => {
    expect(
      resolveAppSurface({ ...base, requiresRoonSetup: true, appView: 'settings' }),
    ).toEqual(view('settings'))
  })

  it('shows the roon-core-lost overlay when the Core drops mid-session', () => {
    expect(
      resolveAppSurface({ ...base, roonCoreLost: true }),
    ).toEqual(overlay('roon-core-lost'))
  })

  it('does not show roon-core-lost while a forced setup view is active', () => {
    expect(
      resolveAppSurface({ ...base, roonCoreLost: true, requiresRoonSetup: true }),
    ).toEqual(view('roon-setup'))
  })

  it('shows the user view when nothing is blocking', () => {
    expect(resolveAppSurface({ ...base, appView: 'analysis' })).toEqual(view('analysis'))
  })

  it('downgrades analysis to assistant when dev mode is off', () => {
    expect(
      resolveAppSurface({ ...base, appView: 'analysis', isDevMode: false }),
    ).toEqual(view('assistant'))
  })

  it('downgrades roon-explorer to assistant when the feature is off', () => {
    expect(
      resolveAppSurface({ ...base, appView: 'roon-explorer', roonExplorerEnabled: false }),
    ).toEqual(view('assistant'))
  })
})
