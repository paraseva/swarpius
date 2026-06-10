import React from 'react'
import s from './ConnectionStatusModal.module.css'
import { useWebSocket } from '../websocketContext'

/**
 * Full-screen overlay shown between the Restart click and the
 * next successful WS reconnect. Blocks input so a stray message or
 * Settings save can't hit a server that's about to exit.
 */
export const RestartModal: React.FC = () => {
  const { isRestarting, status } = useWebSocket()
  if (!isRestarting) return null

  const subtitle =
    status === 'open'
      ? 'Disconnecting...'
      : 'Reconnecting...'

  return (
    <div className={s.backdrop} role="alert" aria-live="polite">
      <div className={s.card}>
        <h2 className={s.title}>
          <span className={s.spinner} aria-hidden="true" />
          Restarting Swarpius…
        </h2>
        <div className={s.body}>
          <p>{subtitle}</p>
        </div>
      </div>
    </div>
  )
}
