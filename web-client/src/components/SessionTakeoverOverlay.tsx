import React from 'react'
import { useWebSocket } from '../websocketContext'
import s from './SessionTakeoverOverlay.module.css'

/** Full-screen modal shown when the server reports that another tab or
 *  device has taken over this session (WebSocket close code 4001). The
 *  only escape hatch is an explicit reconnect, which clears the
 *  takeover state by reloading the page — so the user is always aware
 *  they've displaced the other session. */
export const SessionTakeoverOverlay: React.FC = () => {
  const { status } = useWebSocket()

  if (status !== 'taken_over') return null

  return (
    <div className={s.overlay} role="dialog" aria-modal="true" aria-labelledby="takeover-title">
      <div className={s.dialog}>
        <h2 id="takeover-title" className={s.title}>Session taken over</h2>
        <p className={s.body}>
          Another browser tab or device is now using this Swarpius session.
          This tab has been disconnected since only one session is supported.
        </p>
        <p className={s.body}>
          Reconnect to take the session back.
        </p>
        <button
          type="button"
          className={s.reconnect}
          onClick={() => window.location.reload()}
        >
          Reconnect here
        </button>
      </div>
    </div>
  )
}
