/**
 * RoonSetup — full-page setup view shown while Roon pairing is in
 * progress (initialising) or has hit an error (failed). App.tsx
 * routes away once roon_state === "paired".
 */
import React from 'react'
import s from './RoonSetup.module.css'
import type { UseSettingsState } from '../../hooks/useSettingsState'

interface RoonSetupProps {
  state: UseSettingsState
  /** Click handler for the "Open Settings" escape hatch — lets the
   * user configure ROON_CORE_URL manually if auto-discovery is
   * failing. */
  onOpenSettings?: () => void
}

export const RoonSetup: React.FC<RoonSetupProps> = ({ state, onOpenSettings }) => {
  if (state.roonState === 'failed') {
    return <FailureView state={state} onOpenSettings={onOpenSettings} />
  }
  return <InitialisingView state={state} onOpenSettings={onOpenSettings} />
}

const InitialisingView: React.FC<RoonSetupProps> = ({ state, onOpenSettings }) => (
  <div className={s.container}>
    <section className={s.card} aria-labelledby="roon-setup-title">
      <div className={s.headerRow}>
        <span className={s.spinner} aria-hidden="true" />
        <h2 id="roon-setup-title" className={s.title}>Connecting to Roon</h2>
      </div>

      <p className={s.statusLine}>
        {state.roonStatusMessage || 'Checking providers & services…'}
      </p>

      <div>
        <p className={s.instructions}>
          <strong>First-time setup?</strong> Swarpius needs to be
          authorised inside the Roon app. This is a one-time step:
        </p>
        <ol className={s.steps}>
          <li>Open the <strong>Roon</strong> app on any of your devices.</li>
          <li>Go to <strong>Settings → Extensions</strong>.</li>
          <li>Find <strong>Swarpius</strong> in the list and click <strong>Enable</strong>.</li>
        </ol>
        <p className={s.instructions} style={{ marginTop: '0.6rem' }}>
          This page will continue automatically once Roon confirms the
          extension is enabled — no need to refresh.
        </p>
      </div>

      <p className={s.helpFooter}>
        Discovery not finding your Roon Core? It might be on a
        different network or behind firewall rules — open Settings →
        Roon and set <code>ROON_CORE_URL</code> manually to bypass
        auto-discovery.
      </p>

      {onOpenSettings ? (
        <div className={s.actionRow}>
          <button
            type="button"
            className={s.actionButton}
            onClick={onOpenSettings}
          >
            Open Settings
          </button>
        </div>
      ) : null}
    </section>
  </div>
)

const FailureView: React.FC<RoonSetupProps> = ({ onOpenSettings }) => (
  <div className={s.container}>
    <section className={s.card} aria-labelledby="roon-setup-title">
      <div className={s.headerRow}>
        <h2 id="roon-setup-title" className={`${s.title} ${s.titleFailure}`}>
          Roon setup failed
        </h2>
      </div>

      <p className={s.instructions}>
        Swarpius couldn't connect to your Roon Core. Check the following:
      </p>
      <ul className={s.steps}>
        <li>Roon Core is running.</li>
        <li>Roon Core is reachable from this machine.</li>
        <li>
          The Swarpius extension is enabled — open Roon → Settings →
          Extensions and enable it.
        </li>
      </ul>

      <p className={s.instructions}>
        If Swarpius still can't find your Core, set its address manually to
        bypass network discovery:
      </p>
      <ol className={s.steps}>
        <li>Open <strong>Settings → Roon</strong> (button below).</li>
        <li>
          Set <strong>Roon Core URL</strong> to your Core's address
          (e.g. <code>192.168.1.50:9330</code>).
        </li>
        <li><strong>Apply &amp; Restart</strong> to retry.</li>
      </ol>

      <p className={s.helpFooter}>
        Alternatively, set <code>ROON_CORE_URL</code> in your <code>.env</code>{' '}
        and restart.
      </p>

      {onOpenSettings ? (
        <div className={s.actionRow}>
          <button
            type="button"
            className={s.actionButton}
            onClick={onOpenSettings}
          >
            Open Settings
          </button>
        </div>
      ) : null}
    </section>
  </div>
)

export default RoonSetup
