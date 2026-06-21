import React from 'react'
import f from './fields.module.css'
import { useWebSocket, type SocketMessage } from '../../websocketContext'

type Phase = 'idle' | 'confirming' | 'clearing' | 'done' | 'error'

interface ClearResponse {
  request_id?: string
  ok?: boolean
  reason?: string
}

function parseResponse(message: SocketMessage): ClearResponse | null {
  const raw = message.payload ?? message.body
  if (raw && typeof raw === 'object') return raw as ClearResponse
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw) as ClearResponse
    } catch {
      return null
    }
  }
  return null
}

/**
 * Privacy & Data tab. An action tab (not a settings form): it does not
 * participate in Save & Validate. Lets the user delete the locally-stored
 * conversation history + the assistant's working memory.
 */
export const PrivacyTab: React.FC = () => {
  const { sendMessage, clearMessages, messages, isLlmActive } = useWebSocket()
  const [phase, setPhase] = React.useState<Phase>('idle')
  const [error, setError] = React.useState('')
  const pendingId = React.useRef<string | null>(null)

  React.useEffect(() => {
    if (phase !== 'clearing' || !pendingId.current) return
    const match = messages.find(
      (m) =>
        m.channel === 'clear-conversation-response' &&
        m.direction === 'inbound' &&
        parseResponse(m)?.request_id === pendingId.current,
    )
    if (!match) return
    const payload = parseResponse(match)
    pendingId.current = null
    if (payload?.ok) {
      clearMessages?.()
      setPhase('done')
    } else {
      setError(payload?.reason || 'Could not clear history. Please try again.')
      setPhase('error')
    }
  }, [messages, phase, clearMessages])

  const confirm = () => {
    const requestId = crypto.randomUUID()
    pendingId.current = requestId
    setError('')
    setPhase('clearing')
    sendMessage('clear-conversation-request', JSON.stringify({ request_id: requestId }))
  }

  const showButton = phase === 'idle' || phase === 'done' || phase === 'error'

  return (
    <div>
      <p className={f.tabIntro}>
        Swarpius stores your chat history and the assistant&apos;s working
        memory on this machine, so a restart resumes where you left off.
        Clearing removes the conversation transcript, the assistant&apos;s
        memory of it, and any cached search results — a fresh start. Your saved
        zones and other settings are kept.
      </p>

      {showButton ? (
        <button type="button" onClick={() => setPhase('confirming')} disabled={isLlmActive}>
          Clear conversation history
        </button>
      ) : null}

      {isLlmActive ? (
        <p>Finish the current request before clearing history.</p>
      ) : null}

      {phase === 'confirming' ? (
        <div role="group" aria-label="Confirm clear history">
          <p>
            This permanently deletes your chat history and the assistant&apos;s
            memory of this conversation. It cannot be undone.
          </p>
          <button type="button" onClick={confirm}>Yes, clear it</button>
          <button type="button" onClick={() => setPhase('idle')}>Cancel</button>
        </div>
      ) : null}

      {phase === 'clearing' ? <p>Clearing…</p> : null}
      {phase === 'done' ? <p role="status">Conversation history cleared.</p> : null}
      {phase === 'error' ? <p role="alert">{error}</p> : null}
    </div>
  )
}
