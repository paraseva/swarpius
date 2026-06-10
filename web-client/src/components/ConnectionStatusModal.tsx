import React from 'react'
import s from './ConnectionStatusModal.module.css'

/**
 * Full-screen overlays for the connection / setup state. Each is purely
 * presentational — App's resolveAppSurface decides which (if any) shows,
 * so these carry no gating logic of their own.
 */

/** Connected to the agent, but its first state update hasn't arrived yet.
 *  Bridges the gap so a fresh connection is never a blank screen. */
export const ConnectingSplash: React.FC = () => (
  <div className={s.backdrop} role="alert" aria-live="polite">
    <div className={s.card}>
      <h2 className={s.title}>
        <span className={s.spinner} aria-hidden="true" />
        Connecting to Swarpius…
      </h2>
    </div>
  </div>
)

/** The browser can't reach the agent process. Strictly about the agent /
 *  WebSocket link — Roon connectivity is the RoonSetup view's concern.
 *  The bundle agent exits with its window, so reconnect can't recover —
 *  tell the user to relaunch. */
export const AgentUnreachableModal: React.FC<{ isBundle: boolean }> = ({ isBundle }) => (
  <div className={s.backdrop} role="alert" aria-live="polite">
    <div className={s.card}>
      <h2 className={s.title}>
        {!isBundle && <span className={s.spinner} aria-hidden="true" />}
        {isBundle ? 'Swarpius has closed' : 'Continuing to attempt connection…'}
      </h2>
      <div className={s.body}>
        {isBundle ? (
          <p>
            Swarpius is no longer running. Close this browser window and run
            the Swarpius app again.
          </p>
        ) : (
          <>
            <p>The browser can't reach the Swarpius agent. Things to check:</p>
            <ul className={s.list}>
              <li>
                <strong>Agent is running</strong> — confirm the Swarpius agent
                process is up.
              </li>
              <li>
                <strong>Address</strong> — the agent must be bound on the
                <code> host:port</code> the browser is connecting to.
              </li>
            </ul>
            <p>
              Details may be found in the agent's log file (<code>LOG_FILE</code>
               if set, or <code>data/logs/swarpius.log</code> if not).
            </p>
          </>
        )}
      </div>
    </div>
  </div>
)

/** The agent is reachable but has lost its link to the Roon Core.
 *  Self-clears when the Core reconnects (the agent retries on its own). */
export const RoonCoreLostModal: React.FC = () => (
  <div className={s.backdrop} role="alert" aria-live="polite">
    <div className={s.card}>
      <h2 className={s.title}>
        <span className={s.spinner} aria-hidden="true" />
        Reconnecting to your Roon Core…
      </h2>
      <div className={s.body}>
        <p>
          Swarpius has lost its connection to your Roon Core. This usually
          clears on its own within a minute (e.g. after a Core restart).
        </p>
        <p>
          Playback and controls will resume automatically once the Core is
          back. If it doesn't recover, check that the Roon Core is powered
          on and reachable on your network.
        </p>
      </div>
    </div>
  </div>
)
